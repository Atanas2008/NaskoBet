"""Jackpot: 4-level progressive mystery jackpot with a pick-a-card mini-game.

The trigger is independent of the spin result and proportional to the bet.
Contributions are tracked separately from base game RTP.
"""
from decimal import Decimal


class Jackpot:
    def __init__(self, jconfig, rng):
        self.rng = rng
        self.levels = list(jconfig["levels"].keys())
        self.seed = {k: Decimal(str(v["seed"])) for k, v in jconfig["levels"].items()}
        self.pct = {k: Decimal(str(v["contribution_pct"])) for k, v in jconfig["levels"].items()}
        self.pool = dict(self.seed)
        self.weights = {k: int(jconfig["card_weights"][k]) for k in self.levels}
        self.p_per_unit = Decimal(str(jconfig["trigger_probability_per_unit_bet"]))
        self.enabled = bool(jconfig.get("enabled", True))
        self.resolution = 10 ** 9

    def contribute(self, total_bet):
        total_bet = Decimal(str(total_bet))
        for k in self.levels:
            self.pool[k] += total_bet * self.pct[k] / 100

    def trigger_probability(self, total_bet) -> Decimal:
        return min(Decimal(1), self.p_per_unit * Decimal(str(total_bet)))

    def triggered(self, total_bet) -> bool:
        if not self.enabled:
            return False
        threshold = int(self.trigger_probability(total_bet) * self.resolution)
        return self.rng.randbelow(self.resolution) < threshold

    def _draw_card(self) -> str:
        total = sum(self.weights.values())
        r = self.rng.randbelow(total)
        for k in self.levels:
            if r < self.weights[k]:
                return k
            r -= self.weights[k]
        raise AssertionError

    def pick_a_card(self):
        """Reveal cards until 3 of a suit. Returns (level, revealed cards)."""
        counts = {k: 0 for k in self.levels}
        revealed = []
        while True:
            c = self._draw_card()
            revealed.append(c)
            counts[c] += 1
            if counts[c] == 3:
                return c, revealed

    def award(self, level) -> Decimal:
        prize = self.pool[level]
        self.pool[level] = self.seed[level]
        return prize

    def play(self, total_bet):
        """Contribute, maybe trigger. Returns None or (level, prize, revealed)."""
        if not self.enabled:
            return None
        self.contribute(total_bet)
        if not self.triggered(total_bet):
            return None
        level, revealed = self.pick_a_card()
        return level, self.award(level), revealed

    def rtp_contribution(self) -> Decimal:
        """Long-run jackpot RTP (fraction of total bet). All contributions are paid out
        eventually; seeds are operator-funded and excluded. Reported separately from base RTP."""
        return sum(self.pct.values()) / 100
