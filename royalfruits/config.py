"""Config: loading and validation of the JSON game configuration."""
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "game.json"


class ConfigError(ValueError):
    pass


def _strip_comments(obj):
    if isinstance(obj, dict):
        return {k: _strip_comments(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_comments(v) for v in obj]
    return obj


def load_raw(path=DEFAULT_CONFIG_PATH) -> dict:
    """Load JSON with exact Decimal numbers (no float money errors)."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh, parse_float=Decimal)


@dataclass(frozen=True)
class FreeSpinSettings:
    """Free-spins bonus: a derived Config (same symbols, freeSpinReels strips, freeSpinPaytable) plus the rules."""
    config: "Config"
    awards: Dict[int, int]            # scatter-B count -> free spins (trigger)
    retrigger_awards: Dict[int, int]  # scatter-B count during free spins -> extra spins
    multiplier: Decimal
    max_total_spins: int
    auto_delay_ms: int
    gamble_steps: int                 # gamble steps allowed on the total bonus win
    gamble_max_amount: Optional[Decimal] = None   # largest bonus total that may be gambled (None = gamble.max_amount)

    @staticmethod
    def award_for(table: Dict[int, int], count: int) -> int:
        usable = [k for k in table if k <= count]
        return table[max(usable)] if usable else 0


@dataclass(frozen=True)
class Config:
    raw: dict
    reels: int
    rows: int
    bet_levels: Tuple[Decimal, ...]
    max_win_multiplier: Decimal
    line_symbols: Tuple[str, ...]
    wild: str
    scatter_a: Optional[str]          # optional second scatter (None = game has only scatter B)
    scatter_b: str
    wild_reels: Tuple[int, ...]       # zero-based reel indices
    scatter_a_reels: Tuple[int, ...]  # zero-based reel indices
    paylines: Tuple[Tuple[int, ...], ...]
    line_pay: Dict[str, Dict[int, Decimal]]
    scatter_a_pay: Dict[int, Decimal]
    scatter_b_pay: Dict[int, Decimal]
    strips: Tuple[Tuple[str, ...], ...]
    gamble: dict
    jackpot: dict
    free_spins: Optional[FreeSpinSettings] = None   # None = the game has no bonus

    @property
    def num_lines(self) -> int:
        return len(self.paylines)

    @staticmethod
    def load(path=DEFAULT_CONFIG_PATH) -> "Config":
        return Config.from_dict(load_raw(path))

    @staticmethod
    def _build_free_spins(d: dict) -> Optional[FreeSpinSettings]:
        if "freeSpinReels" not in d:
            return None
        derived = {k: v for k, v in d.items()
                   if k not in ("freeSpinReels", "freeSpinPaytable", "freeSpinSettings", "jackpot", "gamble")}
        derived["reel_strips"] = d["freeSpinReels"]
        pay = dict(d["paytable"])
        pay["line"] = d["freeSpinPaytable"]["line"]
        pay["scatter_b"] = d["freeSpinPaytable"]["scatter_b"]
        derived["paytable"] = pay
        st = d["freeSpinSettings"]
        awards = {int(k): int(v) for k, v in st["awards"].items()}
        retrig = {int(k): int(v) for k, v in st.get("retriggerAwards", st["awards"]).items()}
        fs = FreeSpinSettings(
            config=Config.from_dict(derived), awards=awards, retrigger_awards=retrig,
            multiplier=Decimal(str(st["multiplier"])), max_total_spins=int(st["maxTotalSpins"]),
            auto_delay_ms=int(st.get("autoDelayMs", 1200)), gamble_steps=int(st.get("bonusGambleSteps", 1)),
            gamble_max_amount=(Decimal(str(st["bonusGambleMaxAmount"])) if "bonusGambleMaxAmount" in st else None))
        if fs.multiplier < 1 or fs.max_total_spins < max(awards.values()):
            raise ConfigError("invalid freeSpinSettings (multiplier >= 1, maxTotalSpins >= largest award)")
        return fs

    @staticmethod
    def from_dict(raw: dict) -> "Config":
        d = _strip_comments(raw)
        try:
            sym = d["symbols"]
            pay = d["paytable"]
            cfg = Config(
                raw=raw,
                reels=int(d["reels"]),
                rows=int(d["rows"]),
                bet_levels=tuple(Decimal(str(b)) for b in d["bet_levels"]),
                max_win_multiplier=Decimal(str(d["max_win_multiplier"])),
                line_symbols=tuple(sym["line"]),
                wild=sym["wild"],
                scatter_a=sym.get("scatter_a"),
                scatter_b=sym["scatter_b"],
                wild_reels=tuple(r - 1 for r in d["wild_reels"]),
                scatter_a_reels=tuple(r - 1 for r in d.get("scatter_a_reels", [])),
                paylines=tuple(tuple(l) for l in d["paylines"]),
                line_pay={s: {int(k): Decimal(str(v)) for k, v in t.items()}
                          for s, t in pay["line"].items()},
                scatter_a_pay={int(k): Decimal(str(v)) for k, v in pay.get("scatter_a", {}).items()},
                scatter_b_pay={int(k): Decimal(str(v)) for k, v in pay["scatter_b"].items()},
                strips=tuple(tuple(s) for s in d["reel_strips"]),
                gamble=d.get("gamble", {}),
                jackpot=d.get("jackpot", {}),
                free_spins=Config._build_free_spins(d),
            )
        except KeyError as exc:
            raise ConfigError(f"missing config key: {exc}") from exc
        cfg._validate(d.get("strip_length_limits", {"min": 30, "max": 80}))
        return cfg

    def _validate(self, limits):
        if len(self.paylines) != 10:
            raise ConfigError("exactly 10 paylines are required")
        for line in self.paylines:
            if len(line) != self.reels or any(not 0 <= r < self.rows for r in line):
                raise ConfigError(f"invalid payline {line}")
        if len(self.strips) != self.reels:
            raise ConfigError("one strip per reel required")
        known = set(self.line_symbols) | {self.wild, self.scatter_a, self.scatter_b}
        for i, strip in enumerate(self.strips):
            if not limits["min"] <= len(strip) <= limits["max"]:
                raise ConfigError(f"reel {i + 1} strip length {len(strip)} outside {limits}")
            for s in strip:
                if s not in known:
                    raise ConfigError(f"unknown symbol {s} on reel {i + 1}")
                if s == self.wild and i not in self.wild_reels:
                    raise ConfigError(f"wild is only allowed on reels {[r + 1 for r in self.wild_reels]}")
                if s == self.scatter_a and i not in self.scatter_a_reels:
                    raise ConfigError(f"scatter A is only allowed on reels {[r + 1 for r in self.scatter_a_reels]}")
        for s in self.line_symbols:
            if s not in self.line_pay:
                raise ConfigError(f"no paytable entry for {s}")
