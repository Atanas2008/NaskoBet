import unittest
from decimal import Decimal

from royalfruits.gamble import Gamble, GambleError
from royalfruits.history import GameHistory
from royalfruits.jackpot import Jackpot
from royalfruits.rng import SeededRNG
from royalfruits.tuner import suggest_adjustments
from royalfruits.wallet import InsufficientFunds, Wallet

from .helpers import D, engine, real_config


class FixedRNG:
    def __init__(self, values):
        self.values = list(values)

    def randbelow(self, n):
        return self.values.pop(0) % n


class GambleTests(unittest.TestCase):
    def test_win_doubles_loss_zeroes(self):
        g = Gamble(FixedRNG([0]), 10)
        self.assertTrue(g.pick("red"))
        self.assertEqual(g.amount, 20)
        g = Gamble(FixedRNG([1]), 10)
        self.assertFalse(g.pick("red"))
        self.assertEqual(g.collect(), 0)

    def test_collect_any_time(self):
        g = Gamble(FixedRNG([0]), 10)
        g.pick("red")
        self.assertEqual(g.collect(), 20)
        with self.assertRaises(GambleError):
            g.pick("red")

    def test_limits(self):
        with self.assertRaises(GambleError):
            Gamble(SeededRNG(1), 101, max_amount=100)
        g = Gamble(FixedRNG([0, 0, 0]), 10, max_amount=100, max_steps=2)
        g.pick("red")
        g.pick("red")
        self.assertFalse(g.can_gamble())
        g = Gamble(FixedRNG([0]), 60, max_amount=100)
        self.assertFalse(g.can_gamble())  # doubling would exceed max

    def test_exact_fair(self):
        self.assertEqual(Gamble.expected_return_factor(), 1)

    def test_statistical_fairness(self):
        rng = SeededRNG(42)
        n, total = 200000, 0
        for _ in range(n):
            g = Gamble(rng, 1)
            g.pick("red")
            total += g.collect()
        self.assertAlmostEqual(float(total) / n, 1.0, delta=0.01)


class JackpotTests(unittest.TestCase):
    def setUp(self):
        self.cfg = real_config().jackpot

    def test_contribution_and_seed_reset(self):
        j = Jackpot(self.cfg, SeededRNG(1))
        j.contribute(10)
        self.assertEqual(j.pool["CLUBS"], D(50) + D(10) * D("0.5") / 100)
        j.award("CLUBS")
        self.assertEqual(j.pool["CLUBS"], D(50))

    def test_trigger_proportional_to_bet(self):
        j = Jackpot(self.cfg, SeededRNG(1))
        self.assertEqual(j.trigger_probability(100), j.trigger_probability(1) * 100)

    def test_pick_a_card_ends_at_three_of_a_kind(self):
        j = Jackpot(self.cfg, SeededRNG(5))
        for _ in range(200):
            level, cards = j.pick_a_card()
            self.assertEqual(cards.count(level), 3)
            self.assertTrue(all(cards.count(c) < 3 for c in set(cards) if c != level))
            self.assertEqual(cards[-1], level)

    def test_trigger_independent_of_spin_result(self):
        # the trigger only consumes the jackpot RNG; spin results are untouched
        e1 = engine(seed=3)
        e2 = engine(seed=3)
        j = Jackpot(self.cfg, SeededRNG(99))
        for _ in range(100):
            j.play(10)
        self.assertEqual(e1.spin(1).stops, e2.spin(1).stops)

    def test_rtp_contribution_reported_separately(self):
        self.assertEqual(Jackpot(self.cfg, SeededRNG(1)).rtp_contribution(), D("0.014"))


class WalletHistoryTests(unittest.TestCase):
    def test_wallet(self):
        w = Wallet(10)
        w.debit(4)
        w.credit("1.5")
        self.assertEqual(w.balance, D("7.5"))
        with self.assertRaises(InsufficientFunds):
            w.debit(100)
        self.assertEqual(len(w.ledger), 2)

    def test_history(self):
        h = GameHistory()
        e = engine()
        e1 = h.record(e.spin(1))
        self.assertEqual(e1["round"], 1)
        self.assertIn("stops", e1)
        self.assertEqual(len(h.rounds), 1)


class TunerTests(unittest.TestCase):
    def test_suggest(self):
        cfg = real_config()
        rep = {"base_rtp": 1.0, "rtp_by_line_symbol": {"SEVEN": 0.3, "CHERRY": 0.1}}
        s = suggest_adjustments(cfg, rep, 0.962)
        self.assertTrue(s)
        self.assertEqual(s[0]["replace"], "SEVEN")
        self.assertEqual(suggest_adjustments(cfg, {"base_rtp": 0.962, "rtp_by_line_symbol": {}}, 0.962), [])


if __name__ == "__main__":
    unittest.main()
