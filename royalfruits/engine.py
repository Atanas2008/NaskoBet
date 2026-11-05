"""GameEngine: spin -> expand -> evaluate lines -> scatters -> cap -> result."""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Tuple

from .config import Config
from .grid import Grid
from .line_evaluator import LineEvaluator, LineWin
from .reelset import ReelSet
from .rng import SecureRNG
from .scatter_evaluator import ScatterEvaluator, ScatterWin
from .wild_expansion import expand


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

    def to_dict(self) -> dict:
        return {
            "stops": list(self.stops),
            "bet_per_line": str(self.bet_per_line),
            "total_bet": str(self.total_bet),
            "grid_before": self.grid_before,
            "grid_after": self.grid_after,
            "line_wins": [{"line": w.line_index + 1, "symbol": w.symbol, "count": w.count,
                           "positions": [list(p) for p in w.positions],
                           "win": str(w.multiplier * self.bet_per_line)} for w in self.line_wins],
            "scatter_wins": [{"symbol": w.symbol, "count": w.count,
                              "positions": [list(p) for p in w.positions],
                              "win": str(w.multiplier * self.total_bet)} for w in self.scatter_wins],
            "expanded_reels": [r + 1 for r in self.expanded_reels],
            "total_win": str(self.total_win),
            "expanded": self.expanded,
            "capped": self.capped,
        }


class GameEngine:
    def __init__(self, config: Config, rng=None):
        self.cfg = config
        self.rng = rng if rng is not None else SecureRNG()
        self.reelset = ReelSet(config.strips, config.rows)
        self.lines = LineEvaluator(config)
        self.scatters = ScatterEvaluator(config)

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
        return SpinResult(
            stops=tuple(stops), bet_per_line=bet, total_bet=total_bet,
            grid_before=before.to_lists(), grid_after=after.to_lists(),
            line_wins=wins, scatter_wins=scat, expanded_reels=reels,
            line_win=line_win, line_win_no_expansion=line_win0, scatter_win=scatter_win,
            uncapped_win=uncapped, total_win=min(uncapped, cap),
            expanded=bool(reels), capped=capped)
