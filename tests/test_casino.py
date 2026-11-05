import http.client
import json
import threading
import unittest
from decimal import Decimal
from http.server import ThreadingHTTPServer
from pathlib import Path

from royalfruits import web
from royalfruits.casino import CONFIG_DIR, GAMES, Casino, UnknownGame
from royalfruits.config import Config
from royalfruits.engine import GameEngine
from royalfruits.gamble import GambleError
from royalfruits.line_evaluator import LineEvaluator
from royalfruits.rng import SeededRNG
from royalfruits.scatter_evaluator import ScatterEvaluator
from royalfruits.wallet import InsufficientFunds
from royalfruits.wild_expansion import expand

from .helpers import D, filler, grid_of

ROOT = Path(__file__).resolve().parent.parent
FRUITS = {"CHERRY", "LEMON", "ORANGE", "PLUM", "GRAPES", "WATERMELON", "SEVEN", "WILD", "STAR", "DOLLAR"}
CATS = {"PAW", "GINGER", "GRAY", "BLACK", "WHITE", "SIAMESE", "GOLDEN", "KINGCAT", "YARN"}


def cats_config():
    return Config.load(CONFIG_DIR / "cats.json")


class CatsConfigTests(unittest.TestCase):
    def setUp(self):
        self.cfg = cats_config()

    def test_uses_only_cat_symbols_and_no_star(self):
        symbols = {s for strip in self.cfg.strips for s in strip}
        self.assertTrue(symbols <= CATS)
        self.assertTrue(CATS <= symbols)
        self.assertIsNone(self.cfg.scatter_a)
        self.assertEqual(self.cfg.wild, "KINGCAT")
        self.assertEqual(self.cfg.scatter_b, "YARN")

    def test_king_cat_only_on_reels_2_to_4(self):
        for i, strip in enumerate(self.cfg.strips):
            self.assertEqual("KINGCAT" in strip, i in (1, 2, 3))

    def test_paw_pays_from_two_and_golden_is_top_symbol(self):
        self.assertIn(2, self.cfg.line_pay["PAW"])
        top = max(self.cfg.line_pay, key=lambda s: self.cfg.line_pay[s][5])
        self.assertEqual(top, "GOLDEN")

    def test_has_free_spins_config(self):
        self.assertIsNotNone(self.cfg.free_spins)
        self.assertIn("freeSpinReels", self.cfg.raw)

    def test_king_cat_expands_when_part_of_a_win(self):
        rows = filler("GRAY")
        rows[1] = ["PAW", "KINGCAT", "PAW", "GINGER", "GRAY"]
        grid = grid_of(rows)
        wins = LineEvaluator(self.cfg).evaluate(grid)
        new, reels = expand(self.cfg, grid, wins)
        self.assertEqual(reels, [1])
        self.assertEqual(new.column(1), ["KINGCAT"] * 3)

    def test_king_cat_substitutes_but_not_for_yarn(self):
        rows = filler("GRAY")
        rows[1] = ["YARN", "KINGCAT", "YARN", "GINGER", "BLACK"]
        wins = LineEvaluator(self.cfg).evaluate(grid_of(rows))
        self.assertFalse([w for w in wins if w.symbol == "YARN"])

    def test_yarn_pays_anywhere(self):
        rows = filler("GRAY")
        rows[0][0] = rows[2][2] = rows[1][4] = "YARN"
        wins = ScatterEvaluator(self.cfg).evaluate(grid_of(rows))
        self.assertEqual(len(wins), 1)
        self.assertEqual(wins[0].symbol, "YARN")
        self.assertEqual(wins[0].multiplier, self.cfg.scatter_b_pay[3])
        rows[0][0] = "GRAY"
        self.assertEqual(ScatterEvaluator(self.cfg).evaluate(grid_of(rows)), [])

    def test_symbol_sets_never_mix_in_play(self):
        for gid, allowed in (("fruits", FRUITS), ("cats", CATS)):
            eng = GameEngine(Config.load(CONFIG_DIR / GAMES[gid]["config"]), SeededRNG(5))
            for _ in range(1500):
                r = eng.spin(1)
                seen = {s for row in r.grid_before + r.grid_after for s in row}
                self.assertTrue(seen <= allowed, (gid, seen - allowed))

    def test_every_symbol_has_an_svg(self):
        for gid, info in GAMES.items():
            folder = ROOT / info["assets"].strip("/")
            self.assertTrue((folder / info["icon"]).is_file())
            if info["kind"] != "slot":
                continue
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            cfg = Config.load(CONFIG_DIR / info["config"])
            used = {s for strip in cfg.strips for s in strip}
            self.assertTrue(used <= set(manifest), (gid, used - set(manifest)))
            for name, file in manifest.items():
                self.assertTrue((folder / file).is_file(), file)

    def test_fruits_game_is_unchanged_original(self):
        cfg = Config.load(CONFIG_DIR / "game.json")
        self.assertEqual(cfg.scatter_a, "STAR")
        self.assertEqual(cfg.line_pay["SEVEN"][5], D(2000))
        self.assertIsNone(cfg.free_spins)


def first_winning_casino():
    for seed in range(500):
        casino = Casino(SeededRNG(seed))
        out = casino.game("fruits").spin(1)
        if Decimal(out["result"]["total_win"]) > 0 and "jackpot_win" not in out:
            return casino, Decimal(out["result"]["total_win"])
    raise AssertionError("no winning seed found")


class CasinoTests(unittest.TestCase):
    def test_two_games_share_one_wallet(self):
        casino = Casino(SeededRNG(3))
        self.assertIs(casino.game("fruits").wallet, casino.game("cats").wallet)
        self.assertIsNot(casino.game("fruits").engine, casino.game("cats").engine)
        self.assertNotEqual(casino.game("fruits").cfg.strips, casino.game("cats").cfg.strips)
        a = casino.game("fruits").spin(1)
        b = casino.game("cats").spin(1)
        total = Decimal(1000) - 20 + Decimal(a["result"]["total_win"]) + Decimal(b["result"]["total_win"])
        for out in (a, b):
            if "jackpot_win" in out:
                total += Decimal(out["jackpot_win"]["prize"])
        self.assertEqual(Decimal(casino.lobby()["balance"]), total)

    def test_spin_in_other_game_credits_pending_win_first(self):
        casino, win = first_winning_casino()
        self.assertEqual(casino.wallet.balance, 1000 - 10)
        casino.game("cats")  # switching games credits the uncollected fruits win
        self.assertEqual(casino.wallet.balance, 1000 - 10 + win)
        self.assertEqual(casino.game("fruits").pending, 0)

    def test_gamble_is_per_game(self):
        casino, win = first_winning_casino()
        fruits, cats = casino.games["fruits"], casino.games["cats"]
        self.assertLessEqual(win, 100)
        self.assertTrue(fruits.state()["can_gamble"])
        self.assertFalse(cats.state()["can_gamble"])
        with self.assertRaises(GambleError):
            cats.pick("red")
        self.assertEqual(fruits.pending, win)

    def test_each_game_has_its_own_jackpot_pools(self):
        casino = Casino(SeededRNG(8))
        casino.game("fruits").spin(1)
        self.assertGreater(casino.games["fruits"].jp.pool["CLUBS"], casino.games["cats"].jp.pool["CLUBS"])

    def test_unknown_game_and_insufficient_funds(self):
        casino = Casino(SeededRNG(1), balance=5)
        with self.assertRaises(UnknownGame):
            casino.game("poker")
        for gid in GAMES:
            game = casino.game(gid)
            with self.assertRaises(InsufficientFunds):
                # Deep Miner's bet is the whole stake, so use the top bet level
                (game.play(100) if hasattr(game, "play") else game.spin(1))

    def test_lobby_lists_all_games(self):
        lobby = Casino(SeededRNG(1)).lobby()
        self.assertEqual([g["id"] for g in lobby["games"]], ["fruits", "cats", "miner"])
        self.assertEqual([g["title"] for g in lobby["games"]], ["Royal Fruits", "Royal Cats", "Deep Miner"])
        self.assertEqual([g["path"] for g in lobby["games"]], ["/fruits", "/cats", "/miner"])


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        web.CASINO = Casino()  # in-memory: tests never touch the real session.json
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def call(self, method, path, body=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request(method, path, json.dumps(body) if body is not None else None,
                  {"Content-Type": "application/json"})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_pages_and_assets(self):
        for path in ("/", "/fruits", "/cats", "/miner", "/app.js", "/lobby.js", "/style.css", "/miner.js", "/miner.css",
                     "/assets/mine/p3_iron.svg", "/assets/symbols/manifest.json",
                     "/assets/symbols/cats/c08_king_cat_wild.svg"):
            self.assertEqual(self.call("GET", path)[0], 200, path)
        self.assertEqual(self.call("GET", "/nope")[0], 404)
        self.assertEqual(self.call("GET", "/assets/../README.md")[0], 404)

    def test_api_per_game(self):
        self.call("POST", "/api/reset", {})
        status, data = self.call("POST", "/api/cats/spin", {"bet": 1})
        self.assertEqual(status, 200)
        out = json.loads(data)
        self.assertEqual(out["game"], "cats")
        for key in ("is_free_spin", "free_spins_awarded", "free_spins_left", "free_spins_total", "bonus_multiplier",
                    "bonus_win", "bonus_ended"):
            self.assertIn(key, out)
        self.assertTrue({s for row in out["result"]["grid_before"] for s in row} <= CATS)
        status, data = self.call("POST", "/api/fruits/spin", {"bet": 1})
        out = json.loads(data)
        self.assertEqual(out["game"], "fruits")
        self.assertTrue({s for row in out["result"]["grid_before"] for s in row} <= FRUITS)
        self.assertEqual(self.call("GET", "/api/poker/state")[0], 404)
        self.assertEqual(self.call("POST", "/api/cats/spin", {"bet": 3})[0], 400)
        self.assertEqual(self.call("POST", "/api/cats/gamble", {"color": "red"})[0], 400)
        lobby = json.loads(self.call("GET", "/api/lobby")[1])
        self.assertEqual([g["id"] for g in lobby["games"]], ["fruits", "cats", "miner"])

    def test_miner_api_and_shared_balance(self):
        self.call("POST", "/api/reset", {})
        for _ in range(60):
            status, data = self.call("POST", "/api/miner/play", {"bet": 1})
            self.assertEqual(status, 200)
            out = json.loads(data)
            if out["result"]["start_tier"]:
                break
        res = out["result"]
        self.assertEqual(len(res["mine"]), 60)
        self.assertEqual(len(res["mine"][0]), 9)
        self.assertTrue(res["events"])
        lobby = json.loads(self.call("GET", "/api/lobby")[1])
        self.assertEqual(lobby["balance"], out["balance"])
        self.assertEqual(self.call("POST", "/api/miner/spin", {"bet": 1})[0], 404)
        self.assertEqual(self.call("POST", "/api/miner/gamble", {"color": "red"})[0], 404)
        self.assertEqual(self.call("POST", "/api/cats/play", {"bet": 1})[0], 404)
        self.assertEqual(self.call("POST", "/api/miner/play", {"bet": 3})[0], 400)
