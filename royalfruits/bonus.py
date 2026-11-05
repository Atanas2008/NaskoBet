"""Free-spins bonus state (Royal Cats). One BonusState per player, saved with the session."""
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class BonusState:
    bet_per_line: Decimal            # locked to the triggering spin
    multiplier: Decimal
    spins_left: int
    total_awarded: int               # initial award + retriggers (bounded by maxTotalSpins)
    spins_played: int = 0
    total_win: Decimal = Decimal(0)  # accumulated bonus win (multiplied and capped)
    trigger_win: Decimal = Decimal(0)  # win of the triggering spin; counts toward the round cap
    retriggers: int = 0
    capped: bool = False
    ended: bool = False              # no spins left; summary / gamble still pending

    def to_dict(self) -> dict:
        return {"bet_per_line": str(self.bet_per_line), "multiplier": str(self.multiplier),
                "spins_left": self.spins_left, "total_awarded": self.total_awarded,
                "spins_played": self.spins_played, "total_win": str(self.total_win),
                "trigger_win": str(self.trigger_win), "retriggers": self.retriggers,
                "capped": self.capped, "ended": self.ended}

    @staticmethod
    def from_dict(d: dict) -> "BonusState":
        return BonusState(
            bet_per_line=Decimal(d["bet_per_line"]), multiplier=Decimal(d["multiplier"]),
            spins_left=int(d["spins_left"]), total_awarded=int(d["total_awarded"]),
            spins_played=int(d["spins_played"]), total_win=Decimal(d["total_win"]),
            trigger_win=Decimal(d["trigger_win"]), retriggers=int(d["retriggers"]),
            capped=bool(d["capped"]), ended=bool(d["ended"]))
