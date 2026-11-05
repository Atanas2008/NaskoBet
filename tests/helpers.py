import copy
from decimal import Decimal

from royalfruits.config import Config, load_raw
from royalfruits.engine import GameEngine
from royalfruits.grid import Grid
from royalfruits.rng import SeededRNG

D = Decimal


def real_config():
    return Config.load()


def tiny_raw():
    """Tiny known config: strips of 3 symbols each (243 combinations)."""
    raw = copy.deepcopy(load_raw())
    raw["strip_length_limits"] = {"min": 1, "max": 80}
    raw["reel_strips"] = [
        ["CHERRY", "LEMON", "STAR"],
        ["CHERRY", "WILD", "DOLLAR"],
        ["CHERRY", "LEMON", "STAR"],
        ["WILD", "LEMON", "DOLLAR"],
        ["CHERRY", "LEMON", "STAR"],
    ]
    return raw


def grid_of(rows):
    return Grid(rows)


def engine(cfg=None, seed=1):
    return GameEngine(cfg or real_config(), SeededRNG(seed))


def filler(sym="LEMON"):
    return [[sym] * 5 for _ in range(3)]
