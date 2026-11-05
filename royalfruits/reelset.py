"""ReelSet: strips and independent random stops."""
from typing import List, Sequence, Tuple


class ReelSet:
    def __init__(self, strips: Sequence[Sequence[str]], rows: int = 3):
        self.strips = tuple(tuple(s) for s in strips)
        self.rows = rows

    def spin(self, rng) -> Tuple[int, ...]:
        """One independent uniform stop per reel; depends on nothing but the RNG."""
        return tuple(rng.randbelow(len(s)) for s in self.strips)

    def window(self, reel: int, stop: int) -> List[str]:
        strip = self.strips[reel]
        return [strip[(stop + r) % len(strip)] for r in range(self.rows)]
