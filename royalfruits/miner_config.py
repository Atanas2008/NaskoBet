"""MinerConfig: loading and validation of the Deep Miner JSON configuration."""
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Tuple

DEFAULT_MINER_PATH = Path(__file__).resolve().parent.parent / "config" / "miner.json"
RESOLUTION = 10 ** 9      # probability resolution of one RNG roll
AIR = "air"               # a mined (empty) cell
BEDROCK = "bedrock"
TABLE = "table"
TNT = "tnt"
BLOCKS = ("grass", "dirt", "stone", "deepstone", "voidstone")


class MinerConfigError(ValueError):
    pass


def _strip_comments(obj):
    if isinstance(obj, dict):
        return {k: _strip_comments(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_comments(v) for v in obj]
    return obj


def _prob(p: Decimal) -> int:
    return int(p * RESOLUTION)


@dataclass(frozen=True)
class Pickaxe:
    id: str
    name: str
    durability: int
    ore_multiplier: Decimal
    breaks_bedrock: bool


@dataclass(frozen=True)
class Layer:
    name: str
    first_row: int
    last_row: int
    block: str
    hardness: int
    ores: Dict[str, Decimal]


@dataclass(frozen=True)
class MinerConfig:
    raw: dict
    title: str
    width: int
    depth: int
    bet_levels: Tuple[Decimal, ...]
    max_win_multiplier: Decimal
    bottom_bonus: Decimal
    draw: Tuple[Tuple[str, Decimal], ...]     # ("NONE", pct), then the tiers in order
    pickaxes: Tuple[Pickaxe, ...]             # index = tier - 1
    layers: Tuple[Layer, ...]
    ore_pay: Dict[str, Decimal]
    table_rows_per: Decimal
    table_min_row: int
    tnt_rows_per: Decimal
    tnt_min_row: int
    tnt_radius: int
    tnt_destroys_bedrock: bool
    bedrock_per_layer: Dict[str, int]
    bedrock_max_per_row: int

    @staticmethod
    def load(path=DEFAULT_MINER_PATH) -> "MinerConfig":
        with open(path, "r", encoding="utf-8") as fh:
            return MinerConfig.from_dict(json.load(fh, parse_float=Decimal))

    @staticmethod
    def from_dict(raw: dict) -> "MinerConfig":
        d = _strip_comments(raw)
        try:
            cfg = MinerConfig(
                raw=raw, title=d.get("title", "Deep Miner"), width=int(d["width"]), depth=int(d["depth"]),
                bet_levels=tuple(Decimal(str(b)) for b in d["bet_levels"]),
                max_win_multiplier=Decimal(str(d["max_win_multiplier"])),
                bottom_bonus=Decimal(str(d["bottom_bonus"])),
                draw=tuple((k, Decimal(str(v))) for k, v in d["draw"].items()),
                pickaxes=tuple(Pickaxe(p["id"], p["name"], int(p["durability"]), Decimal(str(p["ore_multiplier"])),
                                       bool(p["breaks_bedrock"])) for p in d["pickaxes"]),
                layers=tuple(Layer(l["name"], int(l["rows"][0]), int(l["rows"][1]), l["block"], int(l["hardness"]),
                                   {k: Decimal(str(v)) for k, v in l["ores"].items()}) for l in d["layers"]),
                ore_pay={k: Decimal(str(v)) for k, v in d["ore_pay"].items()},
                table_rows_per=Decimal(str(d["crafting_table"]["rows_per"])),
                table_min_row=int(d["crafting_table"]["min_row"]),
                tnt_rows_per=Decimal(str(d["tnt"]["rows_per"])),
                tnt_min_row=int(d["tnt"]["min_row"]),
                tnt_radius=int(d["tnt"]["radius"]),
                tnt_destroys_bedrock=bool(d["tnt"].get("destroys_bedrock", False)),
                bedrock_per_layer={k: int(v) for k, v in d["bedrock"]["per_layer"].items()},
                bedrock_max_per_row=int(d["bedrock"]["max_per_row"]),
            )
        except (KeyError, IndexError) as exc:
            raise MinerConfigError(f"missing or malformed config key: {exc}") from exc
        cfg._validate()
        return cfg

    def _validate(self):
        if sum(w for _, w in self.draw) != 100:
            raise MinerConfigError("draw weights must sum to 100")
        if [k for k, _ in self.draw] != ["NONE"] + [p.id for p in self.pickaxes]:
            raise MinerConfigError("draw must list NONE followed by every pickaxe id in tier order")
        row = 0
        for l in self.layers:
            if l.first_row != row or l.last_row < l.first_row or l.block not in BLOCKS:
                raise MinerConfigError(f"layer {l.name}: rows must be contiguous, block one of {BLOCKS}")
            if l.hardness < 1:
                raise MinerConfigError(f"layer {l.name}: hardness must be >= 1")
            for ore in l.ores:
                if ore not in self.ore_pay:
                    raise MinerConfigError(f"layer {l.name}: unknown ore {ore}")
            row = l.last_row + 1
        if row != self.depth:
            raise MinerConfigError("layers must cover rows 0..depth-1")
        for l in self.layers:
            per_cell = sum(l.ores.values()) + 1 / self.table_rows_per / self.width + 1 / self.tnt_rows_per / self.width
            if per_cell >= 1:
                raise MinerConfigError(f"layer {l.name}: feature probabilities exceed 1")
        if not 1 <= self.bedrock_max_per_row < self.width:
            raise MinerConfigError("bedrock.max_per_row must leave at least one free cell per row")
        if set(self.bedrock_per_layer) - {l.name for l in self.layers}:
            raise MinerConfigError("bedrock.per_layer names an unknown layer")
        if any(self.pickaxes[i].durability <= 0 for i in range(len(self.pickaxes))):
            raise MinerConfigError("durability must be positive")

    # ---- derived lookup tables used by the generator and the mining engine ----
    @property
    def draw_ints(self) -> List[Tuple[str, int]]:
        """Cumulative draw thresholds out of 100 * 10^4 (exact for percentages with <= 4 decimals)."""
        out, acc = [], 0
        for k, w in self.draw:
            acc += int(w * 10_000)
            out.append((k, acc))
        return out

    def layer_of(self, row: int) -> Layer:
        for l in self.layers:
            if l.first_row <= row <= l.last_row:
                return l
        raise IndexError(row)

    def row_roll_table(self, row: int) -> List[Tuple[int, str]]:
        """Cumulative (threshold, content) list for one cell roll in `row` (out of RESOLUTION).
        Contents: 'table', 'tnt' or an ore name. A roll above the last threshold is a plain block."""
        layer = self.layer_of(row)
        table = []
        acc = 0
        if row >= self.table_min_row:
            acc += _prob(1 / self.table_rows_per / self.width)
            table.append((acc, TABLE))
        if row >= self.tnt_min_row:
            acc += _prob(1 / self.tnt_rows_per / self.width)
            table.append((acc, TNT))
        for ore, p in layer.ores.items():
            acc += _prob(p)
            table.append((acc, ore))
        return table
