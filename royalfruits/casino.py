"""Casino: three independent games (Royal Fruits, Royal Cats, Deep Miner) sharing one play-money wallet.

Each game has its own config, engine and RNG use; the two slots also have their own jackpot pools and
gamble state. A slot win that has not been collected yet is credited to the shared wallet before the
player spins again, switches game or returns to the lobby. Deep Miner pays at the end of every round.

Royal Cats (the only game whose config has free spins) keeps one BonusState per player. While
free_spins_left > 0 the spin endpoint plays a free spin at the bet locked by the triggering spin:
nothing is charged, wins are multiplied, gamble is off. When the bonus ends its total waits as a
pending win that may be gambled once. The whole casino state can be saved to / restored from a JSON
file so a reload (or a server restart) resumes a running bonus.
"""
import json
import os
from decimal import Decimal
from pathlib import Path

from .bonus import BonusState
from .config import Config
from .engine import GameEngine
from .gamble import Gamble, GambleError
from .history import GameHistory
from .jackpot import Jackpot
from .miner import MinerEngine
from .miner_config import MinerConfig
from .rng import SecureRNG
from .wallet import Wallet

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
START_BALANCE = 1000

# id -> kind, config file, symbol/sprite folder, icon shown in the lobby, page served at /<id>
GAMES = {
    "fruits": {"kind": "slot", "config": "game.json", "assets": "/assets/symbols/", "icon": "07_seven.svg",
               "page": "game.html"},
    "cats": {"kind": "slot", "config": "cats.json", "assets": "/assets/symbols/cats/", "icon": "c08_king_cat_wild.svg",
             "page": "game.html"},
    "miner": {"kind": "miner", "config": "miner.json", "assets": "/assets/mine/", "icon": "p3_iron.svg",
              "page": "miner.html"},
}


class UnknownGame(ValueError):
    pass


class GameSession:
    """One slot game: engine, jackpot, uncollected win, gamble and (if configured) the free-spins bonus."""

    def __init__(self, game_id, wallet, rng=None, config=None):
        self.id = game_id
        self.cfg = config or Config.load(CONFIG_DIR / GAMES[game_id]["config"])
        self.rng = rng or SecureRNG()
        self.wallet = wallet
        self.engine = GameEngine(self.cfg, self.rng)
        self.jp = Jackpot(self.cfg.jackpot, self.rng) if self.cfg.jackpot else None
        self.history = GameHistory()
        self.pending = Decimal(0)
        self.gamble = None
        self.bonus = None

    # ---- state ------------------------------------------------------------------------------
    def bonus_fields(self, result=None) -> dict:
        """Flat free-spins fields added to every Royal Cats response (nothing for games without the bonus)."""
        fs = self.cfg.free_spins
        if not fs:
            return {}
        b = self.bonus
        return {
            "has_free_spins": True,
            "is_free_spin": bool(result.is_free_spin) if result else bool(b and not b.ended),
            "free_spins_awarded": result.free_spins_awarded if result else 0,
            "free_spins_left": b.spins_left if b else 0,
            "free_spins_total": b.total_awarded if b else 0,
            "bonus_multiplier": str(b.multiplier if b else fs.multiplier),
            "bonus_win": str(b.total_win if b else 0),
            "bonus_ended": bool(b and b.ended),
            "bonus": b.to_dict() if b else None,
            "auto_delay_ms": fs.auto_delay_ms,
        }

    def state(self, result=None, **extra):
        g = self.gamble
        out = {
            "game": self.id,
            "title": self.cfg.raw.get("title", self.id),
            "balance": str(self.wallet.balance),
            "bet_levels": [str(b) for b in self.cfg.bet_levels],
            "num_lines": self.cfg.num_lines,
            "paylines": self.cfg.paylines,
            "pending": str(self.pending),
            "can_gamble": bool(g and g.can_gamble()),
            "jackpot": {k: str(v) for k, v in self.jp.pool.items()} if self.jp else {},
        }
        out.update(self.bonus_fields(result))
        if result is not None:
            out["result"] = result.to_dict()
        out.update(extra)
        return out

    @property
    def bonus_running(self) -> bool:
        return self.bonus is not None and not self.bonus.ended

    # ---- play -------------------------------------------------------------------------------
    def collect_pending(self):
        """Credit an uncollected win (a finished bonus total included); a running bonus is untouched."""
        if self.pending > 0:
            self.wallet.credit(self.gamble.collect() if self.gamble else self.pending,
                               "bonus" if self.bonus else "win")
        self.pending = Decimal(0)
        self.gamble = None
        if self.bonus and self.bonus.ended:
            self.bonus = None

    def _jackpot(self, r):
        if not self.jp:
            return {}
        j = self.jp.play(r.total_bet)
        if not j:
            return {}
        self.wallet.credit(j[1], "jackpot")
        return {"jackpot_win": {"level": j[0], "prize": str(j[1]), "cards": j[2]}}

    def _new_gamble(self, amount, max_steps=None, max_amount=None):
        gm = self.cfg.gamble
        try:
            return Gamble(self.rng, amount, gm.get("max_amount", 100) if max_amount is None else max_amount,
                          gm.get("max_steps", 5) if max_steps is None else max_steps)
        except GambleError:
            return None

    def spin(self, bet):
        if self.bonus_running:
            return self._free_spin()
        bet = self.engine.check_bet(bet)
        self.collect_pending()
        self.wallet.debit(bet * self.cfg.num_lines)
        r = self.engine.spin(bet)
        extra = self._jackpot(r)
        self.history.record(r, **extra)
        if r.free_spins_awarded:
            # The trigger spin's own win (YARN scatter pay included) is paid at once, without gamble.
            if r.total_win > 0:
                self.wallet.credit(r.total_win)
            self.bonus = self.engine.start_bonus(r)
        elif r.total_win > 0:
            self.pending = r.total_win
            self.gamble = self._new_gamble(r.total_win)
        return self.state(r, **extra)

    def _free_spin(self):
        """One free spin at the locked bet: no charge, wins x multiplier, gamble stays off."""
        r = self.engine.free_spin(self.bonus)
        extra = self._jackpot(r)
        self.history.record(r, **extra)
        if self.bonus.ended:
            self.pending = self.bonus.total_win
            fs = self.cfg.free_spins
            steps = fs.gamble_steps
            self.gamble = (self._new_gamble(self.pending, steps, fs.gamble_max_amount)
                           if self.pending > 0 and steps > 0 else None)
        return self.state(r, **extra)

    def collect(self):
        self.collect_pending()
        return self.state()

    def pick(self, color):
        if self.bonus_running:
            raise GambleError("gamble is not available during free spins")
        if not self.gamble:
            raise GambleError("nothing to gamble")
        won = self.gamble.pick(color)
        self.pending = self.gamble.amount
        if not won:
            self.gamble = None
            if self.bonus:
                self.bonus = None
        return self.state(gamble_won=won)

    # ---- persistence ------------------------------------------------------------------------
    def to_dict(self) -> dict:
        g = self.gamble
        return {
            "pending": str(self.pending),
            "bonus": self.bonus.to_dict() if self.bonus else None,
            "gamble": ({"amount": str(g.amount), "steps": g.steps, "finished": g.finished,
                        "max_amount": str(g.max_amount), "max_steps": g.max_steps} if g else None),
            "jackpot": {k: str(v) for k, v in self.jp.pool.items()} if self.jp else None,
        }

    def load_dict(self, d: dict):
        self.pending = Decimal(d["pending"])
        self.bonus = BonusState.from_dict(d["bonus"]) if d.get("bonus") else None
        if self.bonus and not self.cfg.free_spins:
            raise ValueError("bonus saved for a game without free spins")
        if d.get("gamble"):
            g = d["gamble"]
            self.gamble = Gamble(self.rng, g["amount"], g["max_amount"], g["max_steps"])
            self.gamble.steps = int(g["steps"])
            self.gamble.finished = bool(g["finished"])
        if self.jp and d.get("jackpot"):
            for k, v in d["jackpot"].items():
                self.jp.pool[k] = Decimal(v)


class MinerSession:
    """Deep Miner: one paid round = pickaxe draw + mine + mining, settled to the shared wallet at once."""

    def __init__(self, game_id, wallet, rng=None, config=None):
        self.id = game_id
        self.cfg = config or MinerConfig.load(CONFIG_DIR / GAMES[game_id]["config"])
        self.rng = rng or SecureRNG()
        self.wallet = wallet
        self.engine = MinerEngine(self.cfg, self.rng)
        self.history = GameHistory()

    def state(self, **extra):
        c = self.cfg
        return {
            "game": self.id,
            "title": c.title,
            "balance": str(self.wallet.balance),
            "bet_levels": [str(b) for b in c.bet_levels],
            "width": c.width,
            "depth": c.depth,
            "bottom_bonus": str(c.bottom_bonus),
            "pickaxes": [{"id": p.id, "name": p.name, "durability": p.durability,
                          "ore_multiplier": str(p.ore_multiplier), "breaks_bedrock": p.breaks_bedrock}
                         for p in c.pickaxes],
            "ore_pay": {k: str(v) for k, v in c.ore_pay.items()},
            **extra,
        }

    def collect_pending(self):
        """Nothing is ever pending: every round is paid when it ends."""

    def collect(self):
        return self.state()

    def play(self, bet):
        bet = self.engine.check_bet(bet)
        self.wallet.debit(bet)
        result = self.engine.play(bet)
        if result.total_win > 0:
            self.wallet.credit(result.total_win)
        self.history.record(result)
        return self.state(result=result.to_dict())

    def to_dict(self) -> dict:
        return {}

    def load_dict(self, d: dict):
        pass


class Casino:
    def __init__(self, rng=None, balance=START_BALANCE, configs=None, save_path=None):
        self.wallet = Wallet(balance)
        configs = configs or {}
        self.games = {gid: (MinerSession if info["kind"] == "miner" else GameSession)(gid, self.wallet, rng, configs.get(gid))
                      for gid, info in GAMES.items()}
        self.save_path = Path(save_path) if save_path else None
        if self.save_path and self.save_path.exists():
            self._load(balance)

    def game(self, game_id) -> GameSession:
        """Return a game for play; wins pending in the other game are credited first."""
        try:
            g = self.games[game_id]
        except KeyError:
            raise UnknownGame(f"unknown game '{game_id}'") from None
        for other in self.games.values():
            if other is not g:
                other.collect_pending()
        return g

    def collect_all(self):
        for g in self.games.values():
            g.collect_pending()

    def lobby(self):
        self.collect_all()
        return {
            "balance": str(self.wallet.balance),
            "games": [{"id": gid, "title": g.cfg.raw.get("title", gid), "path": f"/{gid}",
                       "icon": GAMES[gid]["assets"] + GAMES[gid]["icon"],
                       "bets": [str(g.cfg.bet_levels[0]), str(g.cfg.bet_levels[-1])]}
                      for gid, g in self.games.items()],
        }

    # ---- persistence ------------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {"balance": str(self.wallet.balance), "games": {gid: g.to_dict() for gid, g in self.games.items()}}

    def save(self):
        if not self.save_path:
            return
        tmp = self.save_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict()), encoding="utf-8")
        os.replace(tmp, self.save_path)

    def _load(self, default_balance):
        try:
            d = json.loads(self.save_path.read_text(encoding="utf-8"))
            self.wallet.balance = Decimal(d["balance"])
            for gid, g in self.games.items():
                g.load_dict(d["games"][gid])
        except (OSError, ValueError, KeyError, TypeError, ArithmeticError, GambleError):
            # A corrupt or incompatible save never blocks the casino: start from scratch.
            self.wallet.balance = Decimal(str(default_balance))
            for g in self.games.values():
                g.pending, g.gamble, g.bonus = Decimal(0), None, None
