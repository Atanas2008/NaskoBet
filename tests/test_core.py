import unittest
from decimal import Decimal
from fractions import Fraction

from royalfruits.config import Config, ConfigError
from royalfruits.engine import GameEngine
from royalfruits.grid import Grid
from royalfruits.line_evaluator import LineEvaluator
from royalfruits.reelset import ReelSet
from royalfruits.rng import SecureRNG, SeededRNG
from royalfruits.scatter_evaluator import ScatterEvaluator
from royalfruits.simulator import exact_rtp, simulate
from royalfruits.wild_expansion import expand

from .helpers import D, engine, grid_of, real_config, tiny_raw


class ConfigTests(unittest.TestCase):
    def test_loads_and_has_10_lines(self):
        cfg = real_config()
        self.assertEqual(cfg.num_lines, 10)
        self.assertEqual(len(cfg.bet_levels), 10)

    def test_wild_only_on_reels_2_to_4_in_strips(self):
        cfg = real_config()
        for i, strip in enumerate(cfg.strips):
            self.assertEqual("WILD" in strip, i in (1, 2, 3))

    def test_star_only_on_1_3_5_in_strips(self):
        cfg = real_config()
        for i, strip in enumerate(cfg.strips):
            self.assertEqual("STAR" in strip, i in (0, 2, 4))

    def test_wild_on_wrong_reel_rejected(self):
        raw = tiny_raw()
        raw["reel_strips"][0][0] = "WILD"
        with self.assertRaises(ConfigError):
            Config.from_dict(raw)

    def test_star_on_wrong_reel_rejected(self):
        raw = tiny_raw()
        raw["reel_strips"][1][0] = "STAR"
        with self.assertRaises(ConfigError):
            Config.from_dict(raw)

    def test_strip_length_limits(self):
        raw = tiny_raw()
        raw["strip_length_limits"] = {"min": 30, "max": 80}
        with self.assertRaises(ConfigError):
            Config.from_dict(raw)

    def test_stacks_exist(self):
        cfg = real_config()
        found = False
        for strip in cfg.strips:
            s = list(strip) + list(strip[:2])
            found |= any(a == b == c and a in ("SEVEN", "WATERMELON")
                         for a, b, c in zip(s, s[1:], s[2:]))
        self.assertTrue(found)


class RngReelTests(unittest.TestCase):
    def test_seeded_reproducible(self):
        a, b = SeededRNG(5), SeededRNG(5)
        self.assertEqual([a.randbelow(100) for _ in range(20)], [b.randbelow(100) for _ in range(20)])

    def test_secure_in_range(self):
        r = SecureRNG()
        self.assertTrue(all(0 <= r.randbelow(7) < 7 for _ in range(100)))

    def test_stops_and_window_wrap(self):
        rs = ReelSet([["A", "B", "C"]] * 2, rows=3)
        self.assertEqual(rs.window(0, 2), ["C", "A", "B"])
        stops = rs.spin(SeededRNG(1))
        self.assertTrue(all(0 <= s < 3 for s in stops))
        g = Grid.from_stops(rs, (0, 1))
        self.assertEqual(g.rows, [["A", "B"], ["B", "C"], ["C", "A"]])


class LineTests(unittest.TestCase):
    def setUp(self):
        self.cfg = real_config()
        self.ev = LineEvaluator(self.cfg)

    def grid(self, mid):
        rows = [["PLUM"] * 5, list(mid), ["ORANGE"] * 5]
        # break accidental lines on top/bottom rows
        rows[0] = ["PLUM", "ORANGE", "PLUM", "ORANGE", "PLUM"]
        rows[2] = ["ORANGE", "PLUM", "ORANGE", "PLUM", "ORANGE"]
        return grid_of(rows)

    def test_cherry_pays_from_two(self):
        w = self.ev.evaluate(self.grid(["CHERRY", "CHERRY", "LEMON", "GRAPES", "SEVEN"]))
        self.assertEqual([(x.symbol, x.count) for x in w if x.line_index == 0], [("CHERRY", 2)])

    def test_others_need_three(self):
        w = self.ev.evaluate(self.grid(["LEMON", "LEMON", "CHERRY", "GRAPES", "SEVEN"]))
        self.assertEqual([x for x in w if x.line_index == 0], [])

    def test_wild_substitutes_and_has_no_own_pay(self):
        w = self.ev.evaluate(self.grid(["SEVEN", "WILD", "WILD", "LEMON", "SEVEN"]))
        w0 = [x for x in w if x.line_index == 0][0]
        self.assertEqual((w0.symbol, w0.count), ("SEVEN", 3))
        w = self.ev.evaluate(self.grid(["WILD"] * 5))
        self.assertEqual([x for x in w if x.line_index == 0], [])

    def test_wild_does_not_substitute_scatter(self):
        w = self.ev.evaluate(self.grid(["STAR", "WILD", "WILD", "WILD", "WILD"]))
        self.assertEqual([x for x in w if x.line_index == 0], [])

    def test_highest_win_per_line_only(self):
        # wild prefix with no real symbol has no pay; only one (the best) win is reported per line
        w = self.ev.evaluate(self.grid(["WILD", "WILD", "WILD", "CHERRY", "SEVEN"]))
        w0 = [x for x in w if x.line_index == 0]
        self.assertEqual(len(w0), 1)
        self.assertEqual((w0[0].symbol, w0[0].count), ("CHERRY", 4))
        self.assertEqual(w0[0].multiplier, self.cfg.line_pay["CHERRY"][4])

    def test_left_to_right_only(self):
        w = self.ev.evaluate(self.grid(["LEMON", "CHERRY", "LEMON", "LEMON", "LEMON"]))
        self.assertEqual([x for x in w if x.line_index == 0], [])


class ExpansionTests(unittest.TestCase):
    def test_participating_wild_expands_whole_reel_only_on_2_4(self):
        eng = engine()
        rows = [["PLUM", "ORANGE", "PLUM", "ORANGE", "PLUM"],
                ["SEVEN", "WILD", "SEVEN", "LEMON", "PLUM"],
                ["ORANGE", "PLUM", "ORANGE", "PLUM", "ORANGE"]]
        g = grid_of(rows)
        wins = eng.lines.evaluate(g)
        after, reels = expand(eng.cfg, g, wins)
        self.assertEqual(reels, [1])
        self.assertEqual(after.column(1), ["WILD"] * 3)
        self.assertEqual(g.at(1, 0), "ORANGE")  # original untouched

    def test_non_participating_wild_does_not_expand(self):
        eng = engine()
        rows = [["PLUM", "ORANGE", "PLUM", "WILD", "PLUM"],
                ["LEMON", "CHERRY", "GRAPES", "LEMON", "PLUM"],
                ["ORANGE", "PLUM", "ORANGE", "PLUM", "ORANGE"]]
        g = grid_of(rows)
        _, reels = expand(eng.cfg, g, eng.lines.evaluate(g))
        self.assertEqual(reels, [])

    def test_engine_reevaluates_after_expansion(self):
        cfg = real_config()
        eng = GameEngine(cfg, SeededRNG(1))
        # find stops giving an expansion that increases the win
        found = None
        rng = SeededRNG(3)
        for _ in range(20000):
            r = eng.evaluate_stops(eng.reelset.spin(rng), 1)
            if r.expanded and r.line_win > r.line_win_no_expansion:
                found = r
                break
        self.assertIsNotNone(found)
        for reel in found.expanded_reels:
            self.assertEqual([row[reel] for row in found.grid_after], ["WILD"] * 3)
            self.assertIn(reel, (1, 2, 3))


class ScatterTests(unittest.TestCase):
    def setUp(self):
        self.cfg = real_config()
        self.ev = ScatterEvaluator(self.cfg)

    def base(self):
        return [["LEMON"] * 5 for _ in range(3)]

    def test_star_pays_on_three_reels(self):
        r = self.base()
        r[0][0] = r[1][2] = r[2][4] = "STAR"
        w = self.ev.evaluate(grid_of(r))
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0].count, 3)
        self.assertEqual({p[0] for p in w[0].positions}, {0, 2, 4})
        self.assertEqual(w[0].multiplier, self.cfg.scatter_a_pay[3])

    def test_two_stars_pay_nothing(self):
        r = self.base()
        r[0][0] = r[1][2] = "STAR"
        self.assertEqual(self.ev.evaluate(grid_of(r)), [])

    def test_star_on_non_star_reel_ignored(self):
        r = self.base()
        r[0][0] = r[1][2] = "STAR"
        r[1][1] = "STAR"  # reel 2 is not a star reel
        self.assertEqual(self.ev.evaluate(grid_of(r)), [])

    def test_dollar_anywhere_3_4_5(self):
        for n in (3, 4, 5):
            r = self.base()
            for i in range(n):
                r[i % 3][i] = "DOLLAR"
            w = self.ev.evaluate(grid_of(r))
            self.assertEqual(w[0].multiplier, self.cfg.scatter_b_pay[n])
        r = self.base()
        r[0][0] = r[1][1] = "DOLLAR"
        self.assertEqual(self.ev.evaluate(grid_of(r)), [])


class EngineTests(unittest.TestCase):
    def test_invalid_bet(self):
        with self.assertRaises(ValueError):
            engine().spin(3)

    def test_scatter_paid_times_total_bet(self):
        eng = engine()
        r = eng.evaluate_stops((0, 0, 0, 0, 0), 2)
        self.assertEqual(r.total_bet, D(20))
        self.assertEqual(r.total_win, r.line_win + r.scatter_win)

    def test_cap(self):
        raw = tiny_raw()
        raw["max_win_multiplier"] = D("0.5")
        raw["paytable"]["line"]["CHERRY"]["5"] = D(10000)
        cfg = Config.from_dict(raw)
        eng = GameEngine(cfg, SeededRNG(1))
        r = eng.evaluate_stops((0, 0, 0, 1, 0), 1)  # cherry across wild... all cherry + wild
        self.assertTrue(r.capped)
        self.assertEqual(r.total_win, D("0.5") * r.total_bet)
        self.assertGreater(r.uncapped_win, r.total_win)

    def test_spin_depends_only_on_rng(self):
        a = GameEngine(real_config(), SeededRNG(9))
        b = GameEngine(real_config(), SeededRNG(9))
        for _ in range(50):
            self.assertEqual(a.spin(1).stops, b.spin(1).stops)

    def test_result_structure(self):
        d = engine().spin(1).to_dict()
        for k in ("stops", "grid_before", "grid_after", "line_wins", "scatter_wins",
                  "total_win", "expanded", "capped"):
            self.assertIn(k, d)


class RtpTests(unittest.TestCase):
    def test_exact_rtp_tiny_config_matches_hand_calc_and_cycle(self):
        cfg = Config.from_dict(tiny_raw())
        res = exact_rtp(cfg)
        self.assertEqual(res["combinations"], 243)
        # independent recomputation by brute force through a fresh engine
        import itertools
        eng = GameEngine(cfg, SeededRNG(0))
        tot = Fraction(0)
        for stops in itertools.product(range(3), repeat=5):
            r = eng.evaluate_stops(stops, 1)
            tot += Fraction(r.total_win) / Fraction(r.total_bet)
        self.assertEqual(res["rtp"], tot / 243)
        self.assertGreater(res["rtp"], 0)

    def test_monte_carlo_matches_exact_on_tiny_config(self):
        cfg = Config.from_dict(tiny_raw())
        exact = float(exact_rtp(cfg)["rtp"])
        rep = simulate(cfg, 60000, workers=1, seed=11)
        self.assertAlmostEqual(rep["base_rtp"], exact, delta=4 * (rep["rtp_ci95"][1] - rep["rtp_ci95"][0]))
        self.assertEqual(sum(rep["buckets"].values()), 60000)


if __name__ == "__main__":
    unittest.main()
