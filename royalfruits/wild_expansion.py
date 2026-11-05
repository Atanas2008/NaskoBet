"""WildExpansion: wilds (reels 2-4) that take part in a win expand to fill the reel."""
from typing import List, Tuple


def reels_to_expand(config, grid, line_wins) -> List[int]:
    reels = set()
    for w in line_wins:
        for (reel, row) in w.positions:
            if reel in config.wild_reels and grid.at(reel, row) == config.wild:
                reels.add(reel)
    return sorted(reels)


def expand(config, grid, line_wins) -> Tuple["Grid", List[int]]:
    """Return (new grid, expanded reel indices). The input grid is not modified."""
    reels = reels_to_expand(config, grid, line_wins)
    out = grid.copy()
    for r in reels:
        out.fill_reel(r, config.wild)
    return out, reels
