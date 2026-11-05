"""RNG: injectable randomness. Default is the OS CSPRNG; SeededRNG is for tests/simulation."""
import random
import secrets


class SecureRNG:
    """Cryptographically secure RNG (OS entropy)."""

    def __init__(self):
        self._r = secrets.SystemRandom()

    def randbelow(self, n: int) -> int:
        return secrets.randbelow(n)


class SeededRNG:
    """Deterministic RNG (Mersenne Twister). NOT for real-money play."""

    def __init__(self, seed=None):
        self._r = random.Random(seed)

    def randbelow(self, n: int) -> int:
        return self._r.randrange(n)
