import copy
import json
import unittest
from decimal import Decimal

from royalfruits.casino import CONFIG_DIR, Casino, MinerSession
from royalfruits.miner import MinerEngine, ReplayRNG
from royalfruits.miner_config import MinerConfig, MinerConfigError
from royalfruits.miner_sim import format_report, simulate_miner
from royalfruits.rng import SeededRNG
from royalfruits.wallet import InsufficientFunds, Wallet

D = Decimal
WOOD, STONE, IRON, GOLD, DIAMOND, VOID = 1, 2, 3, 4, 5, 6


def raw_config():
    with open(CONFIG_DIR / "miner.json", encoding="utf-8") as fh:
        return json.load(fh, parse_float=Decimal)


def make_cfg(patch=None):
    raw = copy.deepcopy(raw_config())
    if patch:
        patch(raw)
    return MinerConfig.from_dict(raw)


def blank(cfg):
    """A mine with plain blocks only: no ores, tables, TNT or bedrock."""
    return [[cfg.layer_of(r).block] * cfg.width for r in range(cfg.depth)]


class FixedRNG:
    def __init__(self, *values):
        self.values = list(values)
        self.calls = 0

    def randbelow(self, n):
        self.calls += 1
        v = self.values.pop(0) if self.values else 0
        return v % n


class MinerBase(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg()
        self.eng = MinerEngine(self.cfg, FixedRNG())

    def mine(self, mine, tier, col=4, bet=1, eng=None):
        return (eng or self.eng).mine_round(bet, tier, mine, col)


class DrawTests(MinerBase):
    def test_weights_sum_to_one_and_follow_the_config(self):
        self.assertEqual(sum(w for _, w in self.cfg.draw), 100)
        self.assertEqual([k for k, _ in self.cfg.draw][0], "NONE")
        self.assertEqual(self.cfg.draw_ints[-1][1], 1_000_000)

    def test_draw_thresholds(self):
        edges = [thr for _, thr in self.cfg.draw_ints]
        for tier, hi in enumerate(edges):
            lo = edges[tier - 1] if tier else 0
            for x in (lo, hi - 1):
                self.assertEqual(MinerEngine(self.cfg, FixedRNG(x)).draw(), tier)

    def test_draw_frequencies(self):
        eng = MinerEngine(self.cfg, SeededRNG(11))
        n = 200000
        counts = [0] * 7
        for _ in range(n):
            counts[eng.draw()] += 1
        for tier, (_, w) in enumerate(self.cfg.draw):
            self.assertAlmostEqual(counts[tier] / n, float(w) / 100, delta=0.004)

    def test_nothing_pays_zero_and_generates_no_mine(self):
        rng = FixedRNG(0)
        res = MinerEngine(self.cfg, rng).play(1)
        self.assertEqual(res.drawn, "NONE")
        self.assertEqual(res.total_win, 0)
        self.assertIsNone(res.mine)
        self.assertEqual(res.events, [])
        self.assertEqual(rng.calls, 1)  # only the draw consumed randomness

    def test_invalid_bet_rejected(self):
        with self.assertRaises(ValueError):
            self.eng.play(3)


class GeneratorTests(MinerBase):
    def test_shape_layers_and_allowed_content(self):
        eng = MinerEngine(self.cfg, SeededRNG(5))
        allowed = {"grass": {"grass"}, "dirt": {"dirt"}}
        for layer in self.cfg.layers:
            allowed.setdefault(layer.name, set())
        for _ in range(200):
            mine = eng.generator.generate(eng.rng)
            self.assertEqual((len(mine), len(mine[0])), (60, 9))
            self.assertEqual(set(mine[0]), {"grass"})
            self.assertEqual(set(mine[1]), {"dirt"})
            for layer in self.cfg.layers:
                ok = {layer.block, "bedrock"} | {f"{layer.block}:{o}" for o in layer.ores}
                if layer.first_row >= 2:
                    ok |= {"table", "tnt"}
                for r in range(layer.first_row, layer.last_row + 1):
                    self.assertTrue(set(mine[r]) <= ok, (layer.name, set(mine[r]) - ok))

    def test_bedrock_per_layer_and_never_blocks_a_row(self):
        eng = MinerEngine(self.cfg, SeededRNG(6))
        for _ in range(300):
            mine = eng.generator.generate(eng.rng)
            for layer in self.cfg.layers:
                n = sum(row.count("bedrock") for row in mine[layer.first_row:layer.last_row + 1])
                self.assertEqual(n, self.cfg.bedrock_per_layer.get(layer.name, 0))
            for row in mine:
                self.assertLessEqual(row.count("bedrock"), self.cfg.bedrock_max_per_row)
                self.assertGreater(len(row) - row.count("bedrock"), 0)

    def test_table_and_tnt_density(self):
        eng = MinerEngine(self.cfg, SeededRNG(7))
        n, tables, tnts = 400, 0, 0
        for _ in range(n):
            mine = eng.generator.generate(eng.rng)
            tables += sum(row.count("table") for row in mine)
            tnts += sum(row.count("tnt") for row in mine)
        rows = 58  # rows >= min_row 2
        self.assertAlmostEqual(tables / n, rows / 12, delta=0.4)
        self.assertAlmostEqual(tnts / n, rows / 10, delta=0.5)

    def test_generation_is_deterministic_for_a_seed(self):
        a = MinerEngine(self.cfg, SeededRNG(99)).play(1).to_dict()
        b = MinerEngine(self.cfg, SeededRNG(99)).play(1).to_dict()
        self.assertEqual(a, b)

    def test_outcome_is_independent_of_bet_and_balance(self):
        for seed in range(40):
            a = MinerEngine(self.cfg, SeededRNG(seed)).play(1)
            b = MinerEngine(self.cfg, SeededRNG(seed)).play(5)
            self.assertEqual(a.mine, b.mine)
            self.assertEqual(a.events and [e["e"] for e in a.events], b.events and [e["e"] for e in b.events])
            self.assertEqual(a.total_win * 5, b.total_win)


class DurabilityTests(MinerBase):
    def test_durability_drops_by_hardness_and_wood_breaks_at_zero(self):
        res = self.mine(blank(self.cfg), WOOD)
        digs = [e for e in res.events if e["e"] == "dig"]
        self.assertEqual([e["dur"] for e in digs], list(range(9, -1, -1)))
        self.assertEqual(res.reason, "broken")
        self.assertEqual(res.depth, 10)
        self.assertEqual(res.durability_left, 0)
        self.assertEqual(res.events[-1]["e"], "break")
        self.assertEqual(res.total_win, 0)

    def test_deepstone_costs_two(self):
        res = self.mine(blank(self.cfg), DIAMOND)  # 45: 25 for grass..stone, then 20 / 2 = 10 deepstone rows
        digs = {e["r"]: e for e in res.events if e["e"] == "dig"}
        self.assertEqual(digs[24]["dur"], 45 - 25)
        self.assertEqual(digs[25]["cost"], 2)
        self.assertEqual(digs[25]["dur"], 18)
        self.assertEqual(res.depth, 35)
        self.assertEqual(res.reason, "broken")

    def test_voidstone_costs_three(self):
        res = self.mine(blank(self.cfg), VOID)  # 85: 25 + 50 = 75, then 10 -> 3 voidstone blocks, 1 left
        digs = {e["r"]: e for e in res.events if e["e"] == "dig"}
        self.assertEqual(digs[49]["dur"], 10)
        self.assertEqual(digs[50]["cost"], 3)
        self.assertEqual(digs[52]["dur"], 1)
        self.assertEqual(digs[53]["dur"], 0)
        self.assertEqual(res.depth, 54)

    def test_ore_costs_the_host_layer_hardness(self):
        mine = blank(self.cfg)
        mine[25][4] = "deepstone:gold"
        res = self.mine(mine, DIAMOND)
        ore = next(e for e in res.events if e["e"] == "dig" and e["r"] == 25)
        self.assertEqual((ore["cost"], ore["ore"]), (2, "gold"))

    def test_the_block_that_empties_the_pickaxe_is_still_mined_and_paid(self):
        mine = blank(self.cfg)
        mine[9][4] = "stone:coal"  # the 10th block of a 10-durability wood pickaxe
        res = self.mine(mine, WOOD)
        self.assertEqual(res.depth, 10)
        self.assertEqual(res.total_win, D("0.1"))
        self.assertEqual(res.reason, "broken")


class TableTests(MinerBase):
    def test_table_upgrades_one_tier_and_refills_durability(self):
        mine = blank(self.cfg)
        mine[3][4] = "table"
        res = self.mine(mine, WOOD)
        table = next(e for e in res.events if e["e"] == "table")
        self.assertEqual((table["upgraded"], table["tier"], table["dur"]), (True, STONE, 20))
        self.assertEqual(res.end_tier, STONE)
        self.assertEqual(res.upgrades, 1)
        # 3 blocks (7 left), table costs nothing and sets 20, then 20 more blocks -> row 23
        self.assertEqual(res.depth, 24)

    def test_table_costs_no_durability(self):
        mine = blank(self.cfg)
        mine[3][4] = "table"
        res = self.mine(mine, DIAMOND)
        before = next(e for e in res.events if e["e"] == "dig" and e["r"] == 2)["dur"]
        self.assertEqual(before, 45 - 3)
        self.assertEqual(next(e for e in res.events if e["e"] == "table")["dur"], 85)

    def test_no_upgrade_past_void(self):
        mine = blank(self.cfg)
        mine[2][4] = "table"
        res = self.mine(mine, VOID)
        table = next(e for e in res.events if e["e"] == "table")
        self.assertFalse(table["upgraded"])
        self.assertEqual((table["tier"], table["dur"]), (VOID, 83))  # not refilled
        self.assertEqual((res.end_tier, res.upgrades), (VOID, 0))

    def test_two_tables_chain(self):
        mine = blank(self.cfg)
        mine[2][4] = "table"
        mine[3][4] = "table"
        res = self.mine(mine, WOOD)
        self.assertEqual((res.end_tier, res.upgrades), (IRON, 2))


class TntTests(MinerBase):
    def test_tnt_collects_ores_in_3x3_and_chain_reacts(self):
        mine = blank(self.cfg)
        mine[5][4] = "tnt"
        mine[4][3] = "stone:coal"        # inside the first blast
        mine[6][5] = "stone:gold"        # inside the first blast
        mine[7][5] = "stone:iron"        # outside the first blast (row 7), also outside the second (col 5)
        mine[6][3] = "tnt"               # in range of the first -> chain
        mine[7][2] = "stone:gold"        # only inside the chained blast (rows 5-7, cols 2-4)
        mine[8][4] = "stone:iron"        # outside both blasts
        res = self.mine(mine, WOOD)
        tnt = [e for e in res.events if e["e"] == "tnt"]
        self.assertEqual(len(tnt), 1)  # the chain is one event from the pickaxe's TNT
        cells = {tuple(c) for c in tnt[0]["cells"]}
        self.assertIn((6, 3), cells)    # the chained TNT was destroyed
        self.assertIn((7, 2), cells)    # reached only by the second explosion
        self.assertNotIn((7, 5), cells)
        self.assertNotIn((8, 4), cells)
        got = sorted((o["ore"]) for o in tnt[0]["ores"])
        self.assertEqual(got, ["coal", "gold", "gold"])
        self.assertEqual(res.tnt_win, D("0.1") + D(1) + D(1))
        self.assertEqual(tnt[0]["dur"], 5)  # costs no durability (5 blocks above it were dug)
        for r, c in cells:
            self.assertNotEqual(c, -1)

    def test_tnt_pays_with_the_pickaxe_multiplier(self):
        mine = blank(self.cfg)
        mine[2][4] = "tnt"
        mine[3][5] = "stone:gold"
        res = self.mine(mine, GOLD)
        self.assertEqual(res.tnt_win, D(2))

    def test_tnt_does_not_destroy_bedrock_and_clears_the_way(self):
        mine = blank(self.cfg)
        mine[2][4] = "tnt"
        mine[3][3] = "bedrock"
        res = self.mine(mine, WOOD)
        tnt = next(e for e in res.events if e["e"] == "tnt")
        self.assertNotIn([3, 3], tnt["cells"])
        # rows 1-3 x cols 3-5 = 9 cells, minus the bedrock cell and the (1,4) cell the pickaxe already mined
        self.assertEqual(len(tnt["cells"]), 7)
        # the pickaxe then falls through the emptied cell for free
        move = next(e for e in res.events if e["e"] == "move")
        self.assertEqual((move["r"], move["c"]), (3, 4))

    def test_tnt_is_clipped_at_the_edges(self):
        mine = blank(self.cfg)
        mine[2][0] = "tnt"
        res = self.mine(mine, WOOD, col=0)
        tnt = next(e for e in res.events if e["e"] == "tnt")
        # rows 1-3 x cols 0-1 = 6 cells (clipped at the left edge), minus the already mined (1,0)
        self.assertEqual(len(tnt["cells"]), 5)


class BedrockTests(MinerBase):
    def test_deflects_to_a_random_free_side(self):
        for choice, side in ((0, 3), (1, 5)):
            mine = blank(self.cfg)
            mine[3][4] = "bedrock"
            eng = MinerEngine(self.cfg, FixedRNG(choice))
            res = self.mine(mine, WOOD, eng=eng)
            slide = next(e for e in res.events if e["e"] == "slide")
            self.assertEqual((slide["r"], slide["c"], slide["to"]), (2, 4, side))
            self.assertEqual(res.rng_choices, [choice])
            later = [e for e in res.events if e["e"] == "dig" and e["r"] == 3]
            self.assertEqual(later[0]["c"], side)  # continues down in the side column
            self.assertEqual(res.reason, "broken")

    def test_slide_cost_is_the_hardness_of_the_side_cell(self):
        mine = blank(self.cfg)
        mine[3][4] = "bedrock"
        res = self.mine(mine, WOOD, eng=MinerEngine(self.cfg, FixedRNG(0)))
        digs = [e for e in res.events if e["e"] == "dig"]
        self.assertEqual(digs[3]["dur"], 6)  # (0,4)(1,4)(2,4) then the side cell (2,3)
        self.assertEqual((digs[3]["r"], digs[3]["c"]), (2, 3))

    def test_trapped_when_both_sides_are_blocked(self):
        mine = blank(self.cfg)
        mine[3][4] = mine[2][3] = mine[2][5] = "bedrock"
        res = self.mine(mine, WOOD)
        self.assertEqual(res.reason, "trapped")
        self.assertEqual(res.events[-1], {"e": "break", "r": 2, "c": 4, "reason": "trapped"})
        self.assertEqual(res.depth, 3)

    def test_edge_column_has_one_side_and_needs_no_random_choice(self):
        mine = blank(self.cfg)
        mine[3][0] = "bedrock"
        eng = MinerEngine(self.cfg, FixedRNG())
        res = self.mine(mine, WOOD, col=0, eng=eng)
        slide = next(e for e in res.events if e["e"] == "slide")
        self.assertEqual(slide["to"], 1)
        self.assertEqual(res.rng_choices, [])
        self.assertEqual(eng.rng.calls, 0)

    def test_a_visited_cell_is_not_revisited_so_the_pickaxe_cannot_ping_pong(self):
        mine = blank(self.cfg)
        mine[3][4] = mine[3][3] = "bedrock"
        res = self.mine(mine, WOOD, eng=MinerEngine(self.cfg, FixedRNG(0)))
        slides = [e["to"] for e in res.events if e["e"] == "slide"]
        self.assertEqual(slides, [3, 2])  # 4 -> 3 -> 2, never back to 4

    def test_trapped_in_a_dead_end_pocket(self):
        mine = blank(self.cfg)
        mine[3][4] = mine[3][3] = mine[2][2] = "bedrock"
        res = self.mine(mine, WOOD, eng=MinerEngine(self.cfg, FixedRNG(0)))
        self.assertEqual(res.reason, "trapped")

    def test_void_pickaxe_mines_through_bedrock(self):
        mine = blank(self.cfg)
        mine[3][4] = "bedrock"
        res = self.mine(mine, VOID)
        self.assertFalse([e for e in res.events if e["e"] == "slide"])
        dig = next(e for e in res.events if e["e"] == "dig" and e["r"] == 3)
        self.assertEqual((dig["block"], dig["c"], dig["cost"]), ("bedrock", 4, 1))
        self.assertEqual(res.rng_choices, [])


class PayTests(MinerBase):
    def test_ore_pays_bet_times_pay_times_tier_multiplier(self):
        pay = self.cfg.ore_pay
        for tier, mult in ((IRON, 1), (GOLD, 2), (VOID, 3)):
            for ore in ("coal", "gold", "voidcrystal"):
                mine = blank(self.cfg)
                mine[2][4] = f"stone:{ore}"
                res = self.mine(mine, tier, bet=2)
                self.assertEqual(res.ore_win, pay[ore] * mult * 2, (tier, ore))
                self.assertEqual(res.total_win, res.ore_win)

    def test_gold_pickaxe_doubles_and_void_triples_the_same_ore(self):
        mine = blank(self.cfg)
        mine[2][4] = "stone:diamond"
        wins = [self.mine(mine, t).total_win for t in (IRON, GOLD, VOID)]
        self.assertEqual(wins, [D(10), D(20), D(30)])

    def test_multiplier_follows_the_upgraded_tier(self):
        mine = blank(self.cfg)
        mine[2][4] = "table"             # iron -> gold
        mine[3][4] = "stone:gold"
        res = self.mine(mine, IRON)
        self.assertEqual(res.total_win, D(2))

    def test_documented_ore_pays(self):
        self.assertEqual({k: float(v) for k, v in self.cfg.ore_pay.items()},
                         {"coal": 0.1, "iron": 0.3, "gold": 1, "emerald": 3, "diamond": 10, "voidcrystal": 50})
        self.assertEqual([p.durability for p in self.cfg.pickaxes][:4], [10, 20, 35, 25])
        self.assertEqual([p.ore_multiplier for p in self.cfg.pickaxes], [1, 1, 1, 2, 1, 3])
        self.assertEqual([p.breaks_bedrock for p in self.cfg.pickaxes], [False] * 5 + [True])


class EndTests(MinerBase):
    def test_bottom_bonus_ends_the_round(self):
        cfg = make_cfg(lambda r: r["pickaxes"][5].update(durability=1000))
        res = self.mine(blank(cfg), VOID, bet=2, eng=MinerEngine(cfg, FixedRNG()))
        self.assertTrue(res.reached_bottom)
        self.assertEqual(res.reason, "bottom")
        self.assertEqual(res.depth, 60)
        self.assertEqual(res.bottom_win, D(100) * 2)
        self.assertEqual(res.total_win, D(200))
        self.assertEqual(res.events[-1]["e"], "bottom")

    def test_bottom_bonus_is_not_multiplied_by_the_pickaxe(self):
        cfg = make_cfg(lambda r: r["pickaxes"][5].update(durability=1000))
        res = self.mine(blank(cfg), VOID, eng=MinerEngine(cfg, FixedRNG()))
        self.assertEqual(res.bottom_win, 100)

    def test_not_reaching_the_bottom_pays_no_bonus(self):
        res = self.mine(blank(self.cfg), DIAMOND)
        self.assertFalse(res.reached_bottom)
        self.assertEqual(res.bottom_win, 0)

    def test_max_win_cap_ends_the_round_immediately(self):
        cfg = make_cfg(lambda r: r["ore_pay"].update(coal=6000))
        mine = blank(cfg)
        mine[2][4] = "stone:coal"
        mine[3][4] = "stone:gold"
        res = self.mine(mine, WOOD, bet=2, eng=MinerEngine(cfg, FixedRNG()))
        self.assertTrue(res.capped)
        self.assertEqual(res.reason, "cap")
        self.assertEqual(res.total_win, D(5000) * 2)
        self.assertEqual(res.depth, 3)  # nothing is mined after the cap
        self.assertEqual(res.events[-1]["e"], "cap")

    def test_cap_applies_across_ores_and_the_bottom_bonus(self):
        cfg = make_cfg(lambda r: (r["pickaxes"][5].update(durability=1000), r.update(bottom_bonus=Decimal(6000))))
        mine = blank(cfg)
        mine[2][4] = "stone:voidcrystal"
        res = self.mine(mine, VOID, eng=MinerEngine(cfg, FixedRNG()))
        self.assertEqual(res.total_win, 5000)
        self.assertTrue(res.capped)
        self.assertEqual(res.ore_win + res.tnt_win + res.bottom_win, 5000)

    def test_cap_limits_tnt_payouts(self):
        cfg = make_cfg(lambda r: r["ore_pay"].update(coal=3000))
        mine = blank(cfg)
        mine[2][4] = "tnt"
        mine[3][3] = mine[3][4] = mine[3][5] = "stone:coal"
        res = self.mine(mine, WOOD, eng=MinerEngine(cfg, FixedRNG()))
        self.assertEqual(res.total_win, 5000)
        self.assertEqual(res.tnt_win, 5000)

    def test_round_result_stores_the_whole_mine_and_replays_exactly(self):
        eng = MinerEngine(self.cfg, SeededRNG(21))
        checked = 0
        for _ in range(400):
            r = eng.play(1)
            if not r.start_tier:
                continue
            self.assertEqual((len(r.mine), len(r.mine[0])), (60, 9))
            self.assertTrue(any(cell != "air" for row in r.mine for cell in row))  # stored pre-mining
            again = MinerEngine(self.cfg, ReplayRNG(r.rng_choices)).mine_round(1, r.start_tier, r.mine, r.start_col)
            self.assertEqual(again.to_dict(), r.to_dict())
            checked += 1
        self.assertGreater(checked, 150)

    def test_win_buckets_always_add_up(self):
        eng = MinerEngine(self.cfg, SeededRNG(8))
        for _ in range(500):
            r = eng.play(1)
            self.assertEqual(r.ore_win + r.tnt_win + r.bottom_win, r.total_win)
            self.assertLessEqual(r.total_win, 5000)


class ConfigTests(unittest.TestCase):
    def test_draw_must_sum_to_100(self):
        with self.assertRaises(MinerConfigError):
            make_cfg(lambda r: r["draw"].update(NONE=41))

    def test_layers_must_cover_all_rows(self):
        with self.assertRaises(MinerConfigError):
            make_cfg(lambda r: r["layers"][2]["rows"].__setitem__(1, 20))

    def test_unknown_ore_rejected(self):
        with self.assertRaises(MinerConfigError):
            make_cfg(lambda r: r["layers"][2]["ores"].update(ruby=0.1))

    def test_bedrock_cannot_fill_a_row(self):
        with self.assertRaises(MinerConfigError):
            make_cfg(lambda r: r["bedrock"].update(max_per_row=9))

    def test_shipped_config_matches_the_spec(self):
        cfg = MinerConfig.load()
        self.assertEqual((cfg.width, cfg.depth), (9, 60))
        self.assertEqual([(l.first_row, l.last_row, l.hardness) for l in cfg.layers],
                         [(0, 0, 1), (1, 1, 1), (2, 24, 1), (25, 49, 2), (50, 59, 3)])
        self.assertEqual(cfg.bottom_bonus, 100)
        self.assertEqual(cfg.max_win_multiplier, 5000)
        self.assertEqual(cfg.tnt_radius, 1)


class SessionAndSimulatorTests(unittest.TestCase):
    def test_session_settles_bet_and_win_on_the_shared_wallet(self):
        wallet = Wallet(100)
        s = MinerSession("miner", wallet, SeededRNG(4))
        for _ in range(60):
            before = wallet.balance
            out = s.play(1)
            r = out["result"]
            self.assertEqual(wallet.balance, before - 1 + Decimal(r["total_win"]))
            self.assertEqual(out["balance"], str(wallet.balance))
        self.assertEqual(len(s.history.rounds), 60)

    def test_nothing_round_costs_the_bet(self):
        wallet = Wallet(10)
        s = MinerSession("miner", wallet, FixedRNG(0))
        out = s.play(1)
        self.assertEqual(out["result"]["drawn"], "NONE")
        self.assertEqual(wallet.balance, 9)

    def test_insufficient_funds(self):
        s = MinerSession("miner", Wallet(0.5), SeededRNG(1))
        with self.assertRaises(InsufficientFunds):
            s.play(1)

    def test_casino_shares_the_wallet_with_the_slots(self):
        casino = Casino(SeededRNG(2))
        self.assertIs(casino.game("miner").wallet, casino.game("fruits").wallet)
        before = casino.wallet.balance
        out = casino.game("miner").play(1)
        self.assertEqual(casino.wallet.balance, before - 1 + Decimal(out["result"]["total_win"]))

    def test_simulator_report(self):
        cfg = MinerConfig.load()
        rep = simulate_miner(cfg, 6000, workers=1, seed=3)
        again = simulate_miner(cfg, 6000, workers=1, seed=3)
        self.assertEqual(rep["total_rtp"], again["total_rtp"])
        self.assertAlmostEqual(rep["total_rtp"], rep["ore_rtp"] + rep["tnt_rtp"] + rep["bottom_rtp"], places=9)
        self.assertAlmostEqual(sum(rep["tier_share"].values()), 1.0, places=9)
        self.assertAlmostEqual(rep["nothing_rate"], rep["tier_share"]["NONE"])
        self.assertEqual(sum(rep["buckets"].values()), 6000)
        text = format_report(rep)
        for word in ("TOTAL RTP", "TNT pays", "bottom bonus", "'Nothing' rate", "Upgrade frequency", "Avg depth"):
            self.assertIn(word, text)
