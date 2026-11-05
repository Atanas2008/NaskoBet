"""LineEvaluator: left-to-right payline evaluation, highest win per line only."""
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Tuple


@dataclass(frozen=True)
class LineWin:
    line_index: int
    symbol: str
    count: int
    positions: Tuple[Tuple[int, int], ...]  # (reel, row)
    multiplier: Decimal                      # x bet per line


class LineEvaluator:
    def __init__(self, config):
        self.cfg = config

    def evaluate(self, grid) -> List[LineWin]:
        cfg = self.cfg
        wins = []
        for idx, line in enumerate(cfg.paylines):
            cells = [grid.at(c, line[c]) for c in range(cfg.reels)]
            best = None
            for cand in cfg.line_symbols:
                n = 0
                while n < cfg.reels and cells[n] in (cand, cfg.wild):
                    n += 1
                # the run must contain at least one real symbol (wild has no own pay)
                if n == 0 or all(c == cfg.wild for c in cells[:n]):
                    continue
                mult = cfg.line_pay[cand].get(n)
                if mult is None:
                    continue
                if best is None or mult > best[0]:
                    best = (mult, cand, n)
            if best:
                mult, cand, n = best
                wins.append(LineWin(idx, cand, n, tuple((c, line[c]) for c in range(n)), mult))
        return wins
