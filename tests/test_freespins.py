import copy
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from royalfruits.casino import CONFIG_DIR, Casino
from royalfruits.config import Config, load_raw
from royalfruits.engine import GameEngine
from royalfruits.gamble import GambleError
from royalfruits.rng import SeededRNG
from royalfruits.simulator import exact_trigger_stats, simulate

D = Decimal


class ScriptRNG:
    """Returns the scripted values first (stops), then falls back to a seeded RNG."""

    def __init__(self, script=(), seed=0):
        self.script = list(script)
        self.fallback = SeededRNG(seed)

    def randbelow(self, n):
        if self.script:
            v = self.script.pop(0)
            assert 0 <= v < n, (v, n)
            return v
        return self.fallback.randbelow(n)


def cats_cfg(patch=None):
    raw = copy.deepcopy(load_raw(CONFIG_DIR / "cats.json"))
    if patch:
        patch(raw)
    return Config.from_dict(raw)


def stops_with_scatters(cfg, n):
    """Stops that put exactly `n` scatter-B symbols on the grid (one per reel, reels 1..n)."""
    out = []
    for reel, strip in enumerate(cfg.strips):
        L = len(strip)
        windows = [[strip[(s + r) % L] for r in range(cfg.rows)] for s in range(L)]
        want = reel < n
        out.append(next(s for s, w in enumerate(windows) if (w.count(cfg.scatter_b) == 1) == want
                        and (want or cfg.scatter_b not in w)))
    return out


def trigger_script(cfg, n=3):
    return stops_with_scatters(cfg, n)


def fs_script(cfg, n):
    return stops_with_scatters(cfg.free_spins.config, n)


class ConfigTests(unittest.TestCase):
    def test_cats_has_free_spins_config(self):
        cfg = cats_cfg()
        fs = cfg.free_spins
        self.assertEqual(fs.awards, {3: 10, 4: 15, 5: 20})
        self.assertEqual(fs.retrigger_awards, {3: 10, 4: 15, 5: 20})
        self.assertEqual((fs.multiplier, fs.max_total_spins), (3, 50))
        used = {s for strip in fs.config.strips for s in strip}
        self.assertTrue(used <= {"PAW", "GINGER", "GRAY", "BLACK", "WHITE", "SIAMESE", "GOLDEN", "KINGCAT", "YARN"})
        for i, strip in enumerate(fs.config.strips):  # King Cat only on reels 2-4 in the bonus reels too
            self.assertEqual("KINGCAT" in strip, i in (1, 2, 3))
        self.assertIn("freeSpinReels", cfg.raw)
        self.assertIn("freeSpinPaytable", cfg.raw)

    def test_royal_fruits_has_no_free_spins(self):
        cfg = Config.load(CONFIG_DIR / "game.json")
        self.assertIsNone(cfg.free_spins)
        self.assertNotIn("freeSpinReels", cfg.raw)

    def test_bonus_reels_may_not_use_unknown_symbols(self):
        def patch(raw):
            raw["freeSpinReels"][0][0] = "STAR"
        with self.assertRaises(Exception):
            cats_cfg(patch)

    def test_trigger_frequency_target(self):
        one_in = 1 / float(exact_trigger_stats(cats_cfg())["p_trigger"])
        self.assertGreaterEqual(one_in, 150)
        self.assertLessEqual(one_in, 250)


class AwardTests(unittest.TestCase):
    def setUp(self):
        self.cfg = cats_cfg()
        self.eng = GameEngine(self.cfg, SeededRNG(1))

    def test_3_4_5_yarn_award_10_15_20_spins_plus_scatter_pay(self):
        for n, spins in ((3, 10), (4, 15), (5, 20)):
            r = self.eng.evaluate_stops(stops_with_scatters(self.cfg, n), 1)
            self.assertEqual(r.free_spins_awarded, spins, n)
            self.assertEqual(r.scatter_win, self.cfg.scatter_b_pay[n] * r.total_bet)  # normal pay still awarded
            self.assertGreaterEqual(r.total_win, r.scatter_win)

    def test_two_yarn_award_nothing(self):
        r = self.eng.evaluate_stops(stops_with_scatters(self.cfg, 2), 1)
        self.assertEqual(r.free_spins_awarded, 0)

    def test_royal_fruits_dollars_only_pay(self):
        cfg = Config.load(CONFIG_DIR / "game.json")
        eng = GameEngine(cfg, SeededRNG(1))
        for n in (3, 4, 5):
            r = eng.evaluate_stops(stops_with_scatters(cfg, n), 1)
            self.assertEqual(r.free_spins_awarded, 0)
            self.assertGreaterEqual(r.scatter_win, cfg.scatter_b_pay[n] * r.total_bet)
            self.assertNotIn("free_spins_awarded", r.to_dict())  # fruits results are unchanged


class BonusFlowTests(unittest.TestCase):
    def setUp(self):
        self.cfg = cats_cfg()
        self.casino = Casino(ScriptRNG(trigger_script(self.cfg), seed=3), configs={"cats": self.cfg})
        self.game = self.casino.games["cats"]

    def trigger(self, bet=1):
        out = self.game.spin(bet)
        self.assertEqual(out["free_spins_awarded"], 10)
        return out

    def test_trigger_response_and_locked_bet(self):
        start = self.casino.wallet.balance
        out = self.trigger(2)
        self.assertEqual((out["is_free_spin"], out["free_spins_left"], out["free_spins_total"]), (False, 10, 10))
        self.assertEqual((out["bonus_multiplier"], out["bonus_win"], out["bonus_ended"]), ("3", "0", False))
        self.assertEqual(out["bonus"]["bet_per_line"], "2")
        spent = Decimal(2) * 10
        credited = Decimal(out["result"]["total_win"])  # the trigger spin pays its YARN scatter at once
        jackpot = Decimal(out["jackpot_win"]["prize"]) if "jackpot_win" in out else 0
        self.assertEqual(self.casino.wallet.balance, start - spent + credited + jackpot)
        self.assertEqual(self.game.pending, 0)
        self.assertFalse(out["can_gamble"])

    def test_free_spins_are_not_charged_and_use_the_locked_bet(self):
        self.trigger(2)
        for i in range(1, 6):
            before = self.casino.wallet.balance
            out = self.game.spin(100)                       # the bet argument is ignored
            self.assertTrue(out["is_free_spin"])
            self.assertEqual(out["result"]["bet_per_line"], "2")
            self.assertEqual(out["result"]["total_bet"], "20")
            jackpot = Decimal(out["jackpot_win"]["prize"]) if "jackpot_win" in out else 0
            self.assertEqual(self.casino.wallet.balance, before + jackpot)  # nothing deducted, wins are held
            self.assertEqual(out["free_spins_left"], 10 - i + out["free_spins_total"] - 10)
            self.assertEqual(out["bonus"]["spins_played"], i)

    def test_wins_are_multiplied_by_three(self):
        eng = GameEngine(self.cfg, SeededRNG(9))
        base = eng.fs_engine
        checked = 0
        for seed in range(30):
            e = GameEngine(self.cfg, SeededRNG(seed))
            bonus = e.start_bonus(e.evaluate_stops(stops_with_scatters(self.cfg, 3), 1))
            r = e.free_spin(bonus)
            plain = base.evaluate_stops(r.stops, 1)         # the same stops without the bonus multiplier
            self.assertEqual(r.uncapped_win, plain.uncapped_win * 3)
            self.assertEqual(r.total_win, plain.total_win * 3)
            self.assertEqual(r.bonus_multiplier, 3)
            self.assertEqual(r.to_dict()["bonus_multiplier"], "3")
            checked += r.total_win > 0
        self.assertGreater(checked, 3)

    def test_multiplier_applies_only_in_free_spins(self):
        eng = GameEngine(self.cfg, SeededRNG(4))
        for _ in range(300):
            r = eng.spin(1)
            self.assertEqual(r.bonus_multiplier, 1)
            self.assertFalse(r.is_free_spin)

    def test_gamble_disabled_during_free_spins(self):
        self.trigger()
        self.game.spin(1)
        self.assertFalse(self.game.state()["can_gamble"])
        with self.assertRaises(GambleError):
            self.game.pick("red")

    def play_out(self, bet="0.1"):
        out = self.game.spin(bet)
        while not out["bonus_ended"]:
            out = self.game.spin(bet)
        return out

    def test_bonus_ends_and_the_total_can_be_gambled_once(self):
        for seed in range(1, 80):
            casino = Casino(ScriptRNG(trigger_script(self.cfg), seed=seed), configs={"cats": self.cfg})
            game = casino.games["cats"]
            game.spin("0.1")
            out = game.spin("0.1")
            while not out["bonus_ended"]:
                out = game.spin("0.1")
            total = Decimal(out["bonus_win"])
            if 0 < total <= 100:
                break
        else:
            self.fail("no suitable bonus found")
        self.assertEqual(out["free_spins_left"], 0)
        self.assertEqual(game.pending, total)
        self.assertTrue(out["can_gamble"])
        balance = casino.wallet.balance
        res = game.pick("red")
        self.assertFalse(game.state()["can_gamble"])        # one gamble only
        if res["gamble_won"]:
            self.assertEqual(game.pending, total * 2)
            with self.assertRaises(GambleError):
                game.pick("red")
            game.collect()
            self.assertEqual(casino.wallet.balance, balance + total * 2)
        else:
            self.assertEqual(casino.wallet.balance, balance)
        self.assertIsNone(game.bonus)

    def test_a_big_bonus_total_above_the_normal_gamble_limit_can_still_be_gambled(self):
        self.trigger()
        self.game.bonus.spins_left = 1
        self.game.bonus.total_win = Decimal(500)       # far above gamble.max_amount (100)
        out = self.game.spin(1)
        self.assertTrue(out["bonus_ended"])
        self.assertGreaterEqual(self.game.pending, 500)
        self.assertTrue(out["can_gamble"])
        self.assertTrue(self.game.pick("red")["gamble_won"] in (True, False))

    def test_collecting_without_gamble_pays_the_bonus_total_and_ends_the_bonus(self):
        for seed in range(1, 80):
            casino = Casino(ScriptRNG(trigger_script(self.cfg), seed=seed), configs={"cats": self.cfg})
            game = casino.games["cats"]
            game.spin("0.1")
            out = game.spin("0.1")
            while not out["bonus_ended"]:
                out = game.spin("0.1")
            if Decimal(out["bonus_win"]) > 0:
                break
        before = casino.wallet.balance
        game.collect()
        self.assertEqual(casino.wallet.balance, before + Decimal(out["bonus_win"]))
        self.assertIsNone(game.bonus)
        state = game.state()
        self.assertEqual((state["free_spins_left"], state["bonus_ended"], state["bonus"]), (0, False, None))
        # the next spin is a normal, charged spin again
        spin = game.spin(1)
        self.assertFalse(spin["is_free_spin"])

    def test_royal_fruits_charges_every_spin_and_has_no_bonus_fields(self):
        fruits = self.casino.games["fruits"]
        before = self.casino.wallet.balance
        out = fruits.spin(1)
        self.assertNotIn("has_free_spins", out)
        self.assertNotIn("bonus", out)
        self.assertNotIn("is_free_spin", out)
        jackpot = Decimal(out["jackpot_win"]["prize"]) if "jackpot_win" in out else 0
        self.assertEqual(self.casino.wallet.balance, before - 10 + jackpot)

    def test_other_games_keep_working_and_do_not_end_the_bonus(self):
        self.trigger()
        fruits = self.casino.game("fruits")           # switching game
        fruits.spin(1)
        self.assertIsNotNone(self.game.bonus)
        self.assertTrue(self.game.bonus_running)
        self.assertEqual(self.game.state()["free_spins_left"], 10)


class RetriggerTests(unittest.TestCase):
    def setUp(self):
        self.cfg = cats_cfg()

    def bonus_with_yarn_spin(self, yarn, total_awarded=10, spins_left=10):
        rng = ScriptRNG(fs_script(self.cfg, yarn), seed=2)
        eng = GameEngine(self.cfg, rng)
        trigger = eng.evaluate_stops(stops_with_scatters(self.cfg, 3), 1)
        bonus = eng.start_bonus(trigger)
        bonus.total_awarded, bonus.spins_left = total_awarded, spins_left
        return eng, bonus

    def test_3_4_5_yarn_retrigger_10_15_20(self):
        for yarn, extra in ((3, 10), (4, 15), (5, 20)):
            eng, bonus = self.bonus_with_yarn_spin(yarn)
            r = eng.free_spin(bonus)
            self.assertEqual(r.free_spins_awarded, extra, yarn)
            self.assertEqual(bonus.spins_left, 10 - 1 + extra)
            self.assertEqual(bonus.total_awarded, 10 + extra)
            self.assertEqual(bonus.retriggers, 1)
            self.assertFalse(bonus.ended)

    def test_two_yarn_do_not_retrigger(self):
        eng, bonus = self.bonus_with_yarn_spin(2)
        r = eng.free_spin(bonus)
        self.assertEqual((r.free_spins_awarded, bonus.spins_left), (0, 9))

    def test_total_is_capped_at_fifty(self):
        eng, bonus = self.bonus_with_yarn_spin(3, total_awarded=45, spins_left=3)
        r = eng.free_spin(bonus)
        self.assertEqual(r.free_spins_awarded, 5)                 # only 5 of the 10 fit under the cap
        self.assertEqual(bonus.total_awarded, 50)
        self.assertEqual(bonus.spins_left, 3 - 1 + 5)
        eng, bonus = self.bonus_with_yarn_spin(5, total_awarded=50, spins_left=4)
        r = eng.free_spin(bonus)
        self.assertEqual((r.free_spins_awarded, bonus.spins_left, bonus.total_awarded), (0, 3, 50))

    def test_retrigger_pays_the_yarn_scatter_times_three(self):
        eng, bonus = self.bonus_with_yarn_spin(3)
        r = eng.free_spin(bonus)
        yarn = [w for w in r.scatter_wins if w.symbol == "YARN"][0]
        self.assertEqual(yarn.multiplier, self.cfg.free_spins.config.scatter_b_pay[3])
        self.assertGreaterEqual(r.total_win, yarn.multiplier * r.total_bet * 3)

    def test_the_bonus_ends_when_the_last_spin_is_played(self):
        eng, bonus = self.bonus_with_yarn_spin(0, total_awarded=10, spins_left=1)
        eng.rng.script = fs_script(self.cfg, 0)
        r = eng.free_spin(bonus)
        self.assertEqual((bonus.spins_left, bonus.ended, r.free_spins_left), (0, True, 0))
        with self.assertRaises(ValueError):
            eng.free_spin(bonus)

    def test_max_win_cap_ends_the_bonus(self):
        cfg = cats_cfg(lambda raw: raw.update(max_win_multiplier=Decimal("0.5")))
        for seed in range(200):
            eng = GameEngine(cfg, SeededRNG(seed))
            bonus = eng.start_bonus(eng.evaluate_stops(stops_with_scatters(cfg, 3), 1))
            while bonus.spins_left > 0:
                eng.free_spin(bonus)
            cap = Decimal("0.5") * 10
            self.assertLessEqual(bonus.trigger_win + bonus.total_win, cap)
            if bonus.capped:
                self.assertEqual(bonus.trigger_win + bonus.total_win, cap)
                self.assertTrue(bonus.ended)
                return
        self.fail("cap never reached")


class PersistenceTests(unittest.TestCase):
    def test_reload_mid_bonus_resumes_with_all_state(self):
        cfg = cats_cfg()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            casino = Casino(ScriptRNG(trigger_script(cfg), seed=6), configs={"cats": cfg}, save_path=path)
            game = casino.games["cats"]
            game.spin(2)
            for _ in range(3):
                game.spin(2)
            casino.save()
            saved = game.state()

            again = Casino(ScriptRNG(seed=7), configs={"cats": cfg}, save_path=path)
            g2 = again.games["cats"]
            restored = g2.state()
            for key in ("free_spins_left", "free_spins_total", "bonus_win", "bonus_multiplier", "bonus_ended", "bonus",
                        "is_free_spin", "balance"):
                self.assertEqual(restored[key], saved[key], key)
            self.assertEqual(restored["bonus"]["bet_per_line"], "2")
            before = again.wallet.balance
            out = g2.spin(1)                                  # resumes: free spin, locked bet, no charge
            self.assertTrue(out["is_free_spin"])
            self.assertEqual(out["result"]["bet_per_line"], "2")
            jackpot = Decimal(out["jackpot_win"]["prize"]) if "jackpot_win" in out else 0
            self.assertEqual(again.wallet.balance, before + jackpot)

    def test_finished_bonus_summary_and_gamble_survive_a_reload(self):
        cfg = cats_cfg()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            for seed in range(1, 80):
                casino = Casino(ScriptRNG(trigger_script(cfg), seed=seed), configs={"cats": cfg}, save_path=path)
                game = casino.games["cats"]
                game.spin("0.1")
                out = game.spin("0.1")
                while not out["bonus_ended"]:
                    out = game.spin("0.1")
                if 0 < Decimal(out["bonus_win"]) <= 100:
                    break
            casino.save()
            again = Casino(ScriptRNG(seed=1), configs={"cats": cfg}, save_path=path)
            state = again.games["cats"].state()
            self.assertTrue(state["bonus_ended"])
            self.assertEqual(state["pending"], out["bonus_win"])
            self.assertTrue(state["can_gamble"])

    def test_corrupt_save_starts_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            path.write_text("{not json", encoding="utf-8")
            casino = Casino(SeededRNG(1), save_path=path)
            self.assertEqual(casino.wallet.balance, 1000)
            self.assertIsNone(casino.games["cats"].bonus)


class SimulatorTests(unittest.TestCase):
    def test_cats_report_includes_the_bonus(self):
        rep = simulate(cats_cfg(), 8000, workers=1, seed=5)
        self.assertTrue(rep["has_free_spins"])
        self.assertAlmostEqual(rep["total_rtp"], rep["base_rtp"] + rep["free_spins_rtp"] + rep["jackpot_rtp"], places=9)
        self.assertAlmostEqual(rep["game_rtp"], rep["base_rtp"] + rep["free_spins_rtp"], places=9)
        for key in ("bonus_one_in", "avg_bonus_win_x", "largest_bonus_win_x", "retrigger_rate", "scatter_rtp"):
            self.assertIn(key, rep)

    def test_fruits_report_has_no_bonus_section(self):
        rep = simulate(Config.load(CONFIG_DIR / "game.json"), 2000, workers=1, seed=5)
        self.assertFalse(rep["has_free_spins"])
        self.assertNotIn("free_spins_rtp", rep)
