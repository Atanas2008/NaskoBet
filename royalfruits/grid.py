"""Grid: rows x reels symbol matrix."""
from typing import List, Sequence


class Grid:
    def __init__(self, rows: List[List[str]]):
        self.rows = [list(r) for r in rows]

    @staticmethod
    def from_stops(reelset, stops: Sequence[int]) -> "Grid":
        cols = [reelset.window(i, s) for i, s in enumerate(stops)]
        return Grid([[cols[c][r] for c in range(len(cols))] for r in range(reelset.rows)])

    @property
    def num_rows(self) -> int:
        return len(self.rows)

    @property
    def num_reels(self) -> int:
        return len(self.rows[0])

    def at(self, reel: int, row: int) -> str:
        return self.rows[row][reel]

    def column(self, reel: int) -> List[str]:
        return [r[reel] for r in self.rows]

    def copy(self) -> "Grid":
        return Grid(self.rows)

    def fill_reel(self, reel: int, symbol: str) -> None:
        for r in self.rows:
            r[reel] = symbol

    def positions_of(self, symbol: str):
        return [(c, r) for r in range(self.num_rows) for c in range(self.num_reels)
                if self.rows[r][c] == symbol]

    def to_lists(self) -> List[List[str]]:
        return [list(r) for r in self.rows]
