"""Gamble: fair red/black 50/50 double-or-nothing."""
from decimal import Decimal
from fractions import Fraction

COLORS = ("red", "black")


class GambleError(Exception):
    pass


class Gamble:
    def __init__(self, rng, amount, max_amount=100, max_steps=5):
        self.rng = rng
        self.amount = Decimal(str(amount))
        self.max_amount = Decimal(str(max_amount))
        self.max_steps = int(max_steps)
        self.steps = 0
        self.finished = False
        if self.amount <= 0:
            raise GambleError("nothing to gamble")
        if self.amount > self.max_amount:
            raise GambleError(f"win {self.amount} exceeds max gamble amount {self.max_amount}")

    def can_gamble(self) -> bool:
        return (not self.finished and self.steps < self.max_steps
                and self.amount * 2 <= self.max_amount)

    def pick(self, color: str) -> bool:
        """Guess a colour. Returns True if won (amount doubled), False if lost (amount 0)."""
        if color not in COLORS:
            raise GambleError("colour must be 'red' or 'black'")
        if not self.can_gamble():
            raise GambleError("gamble not available")
        card = COLORS[self.rng.randbelow(2)]
        self.steps += 1
        if card == color:
            self.amount *= 2
            return True
        self.amount = Decimal(0)
        self.finished = True
        return False

    def collect(self) -> Decimal:
        self.finished = True
        return self.amount

    @staticmethod
    def expected_return_factor() -> Fraction:
        """Exact RTP of one gamble step: 1/2 * 2 = 1 (i.e. 100%)."""
        return Fraction(1, 2) * 2
