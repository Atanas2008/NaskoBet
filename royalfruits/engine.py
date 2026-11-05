"""GameEngine: spin -> expand -> evaluate lines -> scatters -> cap -> result.

Games whose config has a free-spins bonus (Royal Cats) also get: scatter-B awards on the base game,
`start_bonus` and `free_spin`. Every free spin is a fresh independent spin on the free-spin reels
(a second engine built from the derived config); nothing about a bonus is decided in advance.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Tuple

from .bonus import BonusState
from .config import Config, FreeSpinSettings
from .grid import Grid
from .line_evaluator import LineEvaluator, LineWin
from .reelset import ReelSet
from .rng import SecureRNG
from .scatter_evaluator import ScatterEvaluator, ScatterWin
from .wild_expansion import expand

ONE = Decimal(1)


@dataclass
class SpinResult:
    stops: Tuple[int, ...]
    bet_per_line: Decimal
    total_bet: Decimal
    grid_before: List[List[str]]
    grid_after: List[List[str]]
    line_wins: List[LineWin]
    scatter_wins: List[ScatterWin]
    expanded_reels: List[int]          # zero-based
    line_win: Decimal                  # after expansion
    line_win_no_expansion: Decimal     # what lines would have paid without expansion
    scatter_win: Decimal
    uncapped_win: Decimal
    total_win: Decimal
    expanded: bool
    capped: bool
    # --- free spins (only meaningful when the game has the bonus) ---
    bonus_enabled: bool = False
    free_spins_awarded: int = 0        # spins awarded (trigger) or added (retrigger) by this spin
    free_spins_left: int = 0
    bonus_multiplier: Decimal = ONE    # win multiplier applied to this spin (x3 in free spins)
    bonus_total_win: Decimal = Decimal(0)
    is_free_spin: bool = False

    def to_dict(self) -> dict:
        m = self.bonus_multiplier
        out = {
            "stops": list(self.stops),
            "bet_per_line": str(self.bet_per_line),
            "total_bet": str(self.total_bet),
            "grid_before": self.grid_before,
            "grid_after": self.grid_after,
            "line_wins": [{"line": w.line_index + 1, "symbol": w.symbol, "count": w.count,
                           "positions": [list(p) for p in w.positions],
                           "win": str(w.multiplier * self.bet_per_line * m)} for w in self.line_wins],
            "scatter_wins": [{"symbol": w.symbol, "count": w.count,
                              "positions": [list(p) for p in w.positions],
                              "win": str(w.multiplier * self.total_bet * m)} for w in self.scatter_wins],
            "expanded_reels": [r + 1 for r in self.expanded_reels],
            "total_win": str(self.total_win),
            "expanded": self.expanded,
            "capped": self.capped,
        }
        if self.bonus_enabled:
            out.update({"is_free_spin": self.is_free_spin, "free_spins_awarded": self.free_spins_awarded,
                        "free_spins_left": self.free_spins_left, "bonus_multiplier": str(self.bonus_multiplier),
                        "bonus_total_win": str(self.bonus_total_win)})
        return out


class GameEngine:
    def __init__(self, config: Config, rng=None):
        self.cfg = config
        self.rng = rng if rng is not None else SecureRNG()
        self.reelset = ReelSet(config.strips, config.rows)
        self.lines = LineEvaluator(config)
        self.scatters = ScatterEvaluator(config)
        fs = config.free_spins
        self.fs_engine = GameEngine(fs.config, self.rng) if fs else None

    def check_bet(self, bet_per_line) -> Decimal:
        b = Decimal(str(bet_per_line))
        if b not in self.cfg.bet_levels:
            raise ValueError(f"invalid bet level {bet_per_line}")
        return b

    def spin(self, bet_per_line) -> SpinResult:
        bet = self.check_bet(bet_per_line)
        return self.evaluate_stops(self.reelset.spin(self.rng), bet)

    def evaluate_stops(self, stops, bet_per_line) -> SpinResult:
        cfg = self.cfg
        bet = Decimal(str(bet_per_line))
        total_bet = bet * cfg.num_lines
        before = Grid.from_stops(self.reelset, stops)
        first = self.lines.evaluate(before)
        after, reels = expand(cfg, before, first)
        wins = self.lines.evaluate(after) if reels else first
        line_win = sum((w.multiplier for w in wins), Decimal(0)) * bet
        line_win0 = sum((w.multiplier for w in first), Decimal(0)) * bet
        scat = self.scatters.evaluate(before)
        scatter_win = sum((w.multiplier for w in scat), Decimal(0)) * total_bet
        uncapped = line_win + scatter_win
        cap = cfg.max_win_multiplier * total_bet
        capped = uncapped > cap
        fs = cfg.free_spins
        awarded = 0
        if fs:
            scatters = sum(w.count for w in scat if w.symbol == cfg.scatter_b)
            awarded = FreeSpinSettings.award_for(fs.awards, scatters)
        return SpinResult(
            stops=tuple(stops), bet_per_line=bet, total_bet=total_bet,
            grid_before=before.to_lists(), grid_after=after.to_lists(),
            line_wins=wins, scatter_wins=scat, expanded_reels=reels,
            line_win=line_win, line_win_no_expansion=line_win0, scatter_win=scatter_win,
            uncapped_win=uncapped, total_win=min(uncapped, cap),
            expanded=bool(reels), capped=capped,
            bonus_enabled=bool(fs), free_spins_awarded=awarded, free_spins_left=awarded)

    # ---- free spins -------------------------------------------------------------------------
    def start_bonus(self, trigger: SpinResult) -> BonusState:
        fs = self.cfg.free_spins
        if not fs or trigger.free_spins_awarded <= 0:
            raise ValueError("spin did not trigger free spins")
        return BonusState(
            bet_per_line=trigger.bet_per_line, multiplier=fs.multiplier,
            spins_left=trigger.free_spins_awarded, total_awarded=trigger.free_spins_awarded,
            trigger_win=trigger.total_win)

    def free_spin(self, bonus: BonusState) -> SpinResult:
        """Play one free spin at the bet locked by the trigger. Mutates `bonus`. Wins are multiplied;
        trigger win + bonus wins are capped at max_win_multiplier x total bet, which ends the bonus."""
        fs = self.cfg.free_spins
        if not fs or bonus.spins_left <= 0:
            raise ValueError("no free spins left")
        r = self.fs_engine.spin(bonus.bet_per_line)
        bonus.spins_left -= 1
        bonus.spins_played += 1
        mult = fs.multiplier
        uncapped = r.uncapped_win * mult
        cap = self.cfg.max_win_multiplier * r.total_bet
        room = max(cap - bonus.trigger_win - bonus.total_win, Decimal(0))
        win = min(uncapped, room)
        capped = bonus.trigger_win + bonus.total_win + win >= cap
        bonus.total_win += win

        added = 0
        if capped:
            bonus.spins_left = 0
            bonus.capped = True
        else:
            n = sum(w.count for w in r.scatter_wins if w.symbol == self.cfg.scatter_b)
            wanted = FreeSpinSettings.award_for(fs.retrigger_awards, n)
            added = max(min(wanted, fs.max_total_spins - bonus.total_awarded), 0)
            if added:
                bonus.total_awarded += added
                bonus.spins_left += added
                bonus.retriggers += 1
        if bonus.spins_left == 0:
            bonus.ended = True

        r.line_win *= mult
        r.line_win_no_expansion *= mult
        r.scatter_win *= mult
        r.uncapped_win = uncapped
        r.total_win = win
        r.capped = capped
        r.bonus_enabled = True
        r.bonus_multiplier = mult
        r.is_free_spin = True
        r.free_spins_awarded = added
        r.free_spins_left = bonus.spins_left
        r.bonus_total_win = bonus.total_win
        return r
