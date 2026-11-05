"""Deep Miner: pickaxe draw, mine generation and the mining engine.

The whole outcome of a round comes from the RNG only: the pickaxe draw, the generated mine, the
starting column and (rarely) the side chosen when bedrock deflects the pickaxe. Nothing depends on the
balance or on previous rounds. The complete mine and every RNG choice made while mining are stored in
the result, so a round can be replayed and audited (see `ReplayRNG`).

Mining rules (all numbers come from the config):
  * The pickaxe starts in a random column above the surface and falls one block at a time.
  * Entering a block costs the host layer's hardness in durability. An ore is paid at once
    (ore_pay x bet x pickaxe ore multiplier). The block that takes durability to 0 is still mined
    and paid, then the pickaxe breaks.
  * Crafting table: upgrade one tier (not past the last) and refill durability; costs nothing.
  * TNT: destroys a (2*radius+1)^2 area around itself, collects every ore inside and chain-explodes
    other TNT in range; costs nothing. Bedrock survives unless tnt.destroys_bedrock.
  * Bedrock below the pickaxe: unbreakable (a pickaxe with breaks_bedrock mines it like a normal
    block of that layer). Otherwise it slides into a random free side cell of the same row (not
    bedrock, not already visited on that row) and carries on down; if neither side is free it breaks.
  * Entering the last row pays bottom_bonus x bet and ends the round. The total is capped at
    max_win_multiplier x bet, which also ends the round.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import List, Optional

from .miner_config import AIR, BEDROCK, RESOLUTION, TABLE, TNT, MinerConfig
from .rng import SecureRNG

ZERO = Decimal(0)


class ReplayRNG:
    """Feeds back the choices recorded in a RoundResult (`rng_choices`) to replay mining."""

    def __init__(self, choices):
        self._it = iter(choices)

    def randbelow(self, n: int) -> int:
        v = next(self._it)
        if not 0 <= v < n:
            raise ValueError("recorded choice out of range")
        return v


class MineGenerator:
    def __init__(self, cfg: MinerConfig):
        self.cfg = cfg
        self.block = [cfg.layer_of(r).block for r in range(cfg.depth)]
        self.rolls = []
        for r in range(cfg.depth):
            b = self.block[r]
            self.rolls.append([(t, c if c in (TABLE, TNT) else f"{b}:{c}") for t, c in cfg.row_roll_table(r)])

    def generate(self, rng) -> List[List[str]]:
        """A fresh depth x width mine. Cells: a block name ('stone'), an ore in its host block
        ('stone:coal'), 'table', 'tnt' or 'bedrock'."""
        cfg, w = self.cfg, self.cfg.width
        mine = []
        for r in range(cfg.depth):
            plain, tbl = self.block[r], self.rolls[r]
            if not tbl:
                mine.append([plain] * w)
                continue
            top = tbl[-1][0]
            row = []
            for _ in range(w):
                x = rng.randbelow(RESOLUTION)
                if x >= top:
                    row.append(plain)
                    continue
                for thr, cell in tbl:
                    if x < thr:
                        row.append(cell)
                        break
            mine.append(row)
        for layer in cfg.layers:
            want = cfg.bedrock_per_layer.get(layer.name, 0)
            span = layer.last_row - layer.first_row + 1
            placed = tries = 0
            while placed < want and tries < 1000:
                tries += 1
                r = layer.first_row + rng.randbelow(span)
                c = rng.randbelow(w)
                row = mine[r]
                if row[c] == BEDROCK or row.count(BEDROCK) >= cfg.bedrock_max_per_row:
                    continue
                row[c] = BEDROCK
                placed += 1
        return mine


@dataclass
class RoundResult:
    bet: Decimal
    drawn: str                      # "NONE" or a pickaxe id
    start_tier: int                 # 0 = no pickaxe, else 1-based tier
    end_tier: int = 0
    mine: Optional[List[List[str]]] = None      # the mine as generated (before mining)
    start_col: Optional[int] = None
    events: list = field(default_factory=list)
    rng_choices: list = field(default_factory=list)
    durability_left: int = 0
    depth: int = 0                  # deepest row reached, 1-based (0 = none)
    upgrades: int = 0
    ore_win: Decimal = ZERO
    tnt_win: Decimal = ZERO
    bottom_win: Decimal = ZERO
    total_win: Decimal = ZERO
    capped: bool = False
    reached_bottom: bool = False
    reason: str = "none"            # none | broken | trapped | bottom | cap

    def to_dict(self) -> dict:
        return {
            "bet": str(self.bet), "drawn": self.drawn, "start_tier": self.start_tier, "end_tier": self.end_tier,
            "mine": self.mine, "start_col": self.start_col, "events": self.events,
            "rng_choices": self.rng_choices, "durability_left": self.durability_left, "depth": self.depth,
            "upgrades": self.upgrades, "ore_win": str(self.ore_win), "tnt_win": str(self.tnt_win),
            "bottom_win": str(self.bottom_win), "total_win": str(self.total_win), "capped": self.capped,
            "reached_bottom": self.reached_bottom, "reason": self.reason,
        }


class _Round:
    """Mutable state of one mining run (kept out of the engine so engines stay reusable)."""

    def __init__(self, eng, bet, tier, mine, start_col, record):
        self.eng, self.cfg, self.bet, self.rec = eng, eng.cfg, bet, record
        self.grid = [row[:] for row in mine]
        self.tier = tier
        self.dur = self.cfg.pickaxes[tier - 1].durability
        self.ore = self.tnt = self.bottom = ZERO
        self.capped = False
        self.events = []
        self.choices = []
        self.upgrades = 0
        self.row, self.col = -1, start_col
        self.max_row = -1

    # -- accounting --
    @property
    def total(self):
        return self.ore + self.tnt + self.bottom

    def add(self, kind, amount):
        """Add `amount` (x bet) to a win bucket without exceeding the round cap."""
        amount = min(amount, self.cfg.max_win_multiplier - self.total)
        if kind == "ore":
            self.ore += amount
        elif kind == "tnt":
            self.tnt += amount
        else:
            self.bottom += amount
        if self.total >= self.cfg.max_win_multiplier:
            self.capped = True
        return amount

    def mult(self):
        return self.cfg.pickaxes[self.tier - 1].ore_multiplier

    def emit(self, **ev):
        if self.rec:
            self.events.append(ev)

    # -- cell resolution --
    def enter(self, r, c):
        cfg = self.cfg
        self.row, self.col = r, c
        if r > self.max_row:
            self.max_row = r
        cell = self.grid[r][c]
        if cell == AIR:
            self.emit(e="move", r=r, c=c, dur=self.dur)
        elif cell == TABLE:
            self.grid[r][c] = AIR
            upgraded = self.tier < len(cfg.pickaxes)
            if upgraded:
                self.tier += 1
                self.dur = cfg.pickaxes[self.tier - 1].durability
                self.upgrades += 1
            self.emit(e="table", r=r, c=c, upgraded=upgraded, tier=self.tier, dur=self.dur)
        elif cell == TNT:
            self.explode(r, c)
        else:
            ore = cell.split(":")[1] if ":" in cell else None
            self.grid[r][c] = AIR
            self.dur -= self.eng.hardness[r]
            win = ZERO
            if ore:
                win = self.add("ore", cfg.ore_pay[ore] * self.mult())
            self.emit(e="dig", r=r, c=c, block=cell.split(":")[0], ore=ore, cost=self.eng.hardness[r],
                      dur=max(self.dur, 0), win=str(win * self.bet))

    def explode(self, r, c):
        cfg, rad = self.cfg, self.cfg.tnt_radius
        grid = self.grid
        grid[r][c] = AIR
        queue, destroyed, ores = [(r, c)], [[r, c]], []
        total = ZERO
        while queue:
            tr, tc = queue.pop()
            for y in range(max(tr - rad, 0), min(tr + rad, cfg.depth - 1) + 1):
                for x in range(max(tc - rad, 0), min(tc + rad, cfg.width - 1) + 1):
                    cell = grid[y][x]
                    if cell == AIR or (cell == BEDROCK and not cfg.tnt_destroys_bedrock):
                        continue
                    grid[y][x] = AIR
                    destroyed.append([y, x])
                    if cell == TNT:
                        queue.append((y, x))
                    elif ":" in cell:
                        ore = cell.split(":")[1]
                        win = self.add("tnt", cfg.ore_pay[ore] * self.mult())
                        total += win
                        ores.append({"r": y, "c": x, "ore": ore, "win": str(win * self.bet)})
        self.emit(e="tnt", r=r, c=c, cells=destroyed, ores=ores, win=str(total * self.bet), dur=self.dur)

    def run(self) -> str:
        cfg, grid = self.cfg, self.grid
        depth, width = cfg.depth, cfg.width
        pk = lambda: cfg.pickaxes[self.tier - 1]
        visited = set()
        while True:
            nr = self.row + 1
            r, c = self.row, self.col
            if r >= 0 and nr < depth and grid[nr][c] == BEDROCK and not pk().breaks_bedrock:
                visited.add(c)
                opts = [x for x in (c - 1, c + 1) if 0 <= x < width and x not in visited and grid[r][x] != BEDROCK]
                if not opts:
                    self.emit(e="break", r=r, c=c, reason="trapped")
                    return "trapped"
                pick = self.eng.rng.randbelow(len(opts)) if len(opts) > 1 else 0
                if len(opts) > 1:
                    self.choices.append(pick)
                nc = opts[pick]
                self.emit(e="slide", r=r, c=c, to=nc)
                visited.add(nc)
                self.enter(r, nc)
            else:
                visited = {c}
                self.enter(nr, c)
            if self.capped:
                self.emit(e="cap")
                return "cap"
            if self.row == depth - 1:
                self.add("bottom", cfg.bottom_bonus)
                self.emit(e="bottom", r=self.row, c=self.col, bonus=str(self.bottom * self.bet), dur=max(self.dur, 0))
                return "bottom"
            if self.dur <= 0:
                self.emit(e="break", r=self.row, c=self.col, reason="durability")
                return "broken"


class MinerEngine:
    def __init__(self, config: MinerConfig, rng=None, record_events=True):
        self.cfg = config
        self.rng = rng if rng is not None else SecureRNG()
        self.record_events = record_events
        self.generator = MineGenerator(config)
        self.hardness = [config.layer_of(r).hardness for r in range(config.depth)]
        self._draw = config.draw_ints

    def check_bet(self, bet) -> Decimal:
        b = Decimal(str(bet))
        if b not in self.cfg.bet_levels:
            raise ValueError(f"invalid bet level {bet}")
        return b

    def draw(self) -> int:
        """Pickaxe draw: 0 = nothing, otherwise the 1-based tier."""
        x = self.rng.randbelow(1_000_000)
        for i, (_, thr) in enumerate(self._draw):
            if x < thr:
                return i
        raise AssertionError("draw weights do not cover the range")

    def play(self, bet) -> RoundResult:
        bet = self.check_bet(bet)
        tier = self.draw()
        if tier == 0:
            return RoundResult(bet=bet, drawn="NONE", start_tier=0)
        mine = self.generator.generate(self.rng)
        start_col = self.rng.randbelow(self.cfg.width)
        return self.mine_round(bet, tier, mine, start_col)

    def mine_round(self, bet, tier, mine, start_col) -> RoundResult:
        """Run the mining phase on a given mine (also used by tests and replays)."""
        bet = Decimal(str(bet))
        rd = _Round(self, bet, tier, mine, start_col, self.record_events)
        reason = rd.run()
        return RoundResult(
            bet=bet, drawn=self.cfg.pickaxes[tier - 1].id, start_tier=tier, end_tier=rd.tier,
            mine=mine, start_col=start_col, events=rd.events, rng_choices=rd.choices,
            durability_left=max(rd.dur, 0), depth=rd.max_row + 1, upgrades=rd.upgrades,
            ore_win=rd.ore * bet, tnt_win=rd.tnt * bet, bottom_win=rd.bottom * bet,
            total_win=rd.total * bet, capped=rd.capped, reached_bottom=rd.row == self.cfg.depth - 1,
            reason=reason)
