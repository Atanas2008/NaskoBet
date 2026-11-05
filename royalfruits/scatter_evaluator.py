"""ScatterEvaluator: Star (reels 1,3,5) and Dollar (anywhere) scatters, paid x total bet.

Scatters are evaluated on the grid as it landed (before wild expansion), since
wilds never substitute for scatters.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Tuple


@dataclass(frozen=True)
class ScatterWin:
    symbol: str
    count: int
    positions: Tuple[Tuple[int, int], ...]
    multiplier: Decimal  # x total bet


class ScatterEvaluator:
    def __init__(self, config):
        self.cfg = config

    def evaluate(self, grid) -> List[ScatterWin]:
        cfg = self.cfg
        wins = []
        pos_a = [p for p in grid.positions_of(cfg.scatter_a) if p[0] in cfg.scatter_a_reels]
        reels_with_star = len({p[0] for p in pos_a})
        m = cfg.scatter_a_pay.get(reels_with_star)
        if m:
            wins.append(ScatterWin(cfg.scatter_a, reels_with_star, tuple(pos_a), m))
        pos_b = grid.positions_of(cfg.scatter_b)
        n = len(pos_b)
        if n:
            usable = [k for k in cfg.scatter_b_pay if k <= n]
            if usable:
                wins.append(ScatterWin(cfg.scatter_b, n, tuple(pos_b),
                                       cfg.scatter_b_pay[max(usable)]))
        return wins
