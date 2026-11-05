"""Simulator: exact full-cycle RTP (small configs) and multi-process Monte Carlo."""
import itertools
import math
import secrets
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal

from .config import Config
from .engine import GameEngine
from .rng import SeededRNG

BUCKETS = ["0x", "0-1x", "1-5x", "5-20x", "20-100x", "100-1000x", "1000x+"]


def bucket_index(m: float) -> int:
    if m == 0:
        return 0
    for i, edge in enumerate((1, 5, 20, 100, 1000), start=1):
        if m < edge:
            return i
    return 6


def exact_rtp(config: Config):
    """Full-cycle enumeration of every stop combination. Feasible only for small strips
    (the product of the strip lengths). Returns dict with exact Fractions-as-Decimal."""
    from fractions import Fraction
    engine = GameEngine(config, SeededRNG(0))
    one = Decimal(1)
    total = Fraction(0)
    scatter = Fraction(0)
    n = 0
    for stops in itertools.product(*[range(len(s)) for s in config.strips]):
        r = engine.evaluate_stops(stops, one)
        total += Fraction(r.total_win / r.total_bet)
        scatter += Fraction(r.scatter_win / r.total_bet)
        n += 1
    return {"combinations": n, "rtp": total / n, "scatter_rtp": scatter / n}


def _worker(args):
    raw, n, seed, symbol_attr = args
    cfg = Config.from_dict(raw)
    eng = GameEngine(cfg, SeededRNG(seed))
    one = Decimal(1)
    s = dict(n=0, sum=0.0, sumsq=0.0, scatter=0.0, wild=0.0, hits=0, max=0.0, capped=0,
             expanded=0, buckets=[0] * 7, by_symbol={})
    for _ in range(n):
        r = eng.spin(one)
        m = float(r.total_win / r.total_bet)
        s["n"] += 1
        s["sum"] += m
        s["sumsq"] += m * m
        s["scatter"] += float(r.scatter_win / r.total_bet)
        s["wild"] += float((r.line_win - r.line_win_no_expansion) / r.total_bet)
        s["hits"] += m > 0
        s["max"] = max(s["max"], m)
        s["capped"] += r.capped
        s["expanded"] += r.expanded
        s["buckets"][bucket_index(m)] += 1
        for w in r.line_wins:
            s["by_symbol"][w.symbol] = s["by_symbol"].get(w.symbol, 0.0) + float(w.multiplier / 10)
    return s


def _merge(parts):
    out = parts[0]
    for p in parts[1:]:
        for k in ("n", "sum", "sumsq", "scatter", "wild", "hits", "capped", "expanded"):
            out[k] += p[k]
        out["max"] = max(out["max"], p["max"])
        out["buckets"] = [a + b for a, b in zip(out["buckets"], p["buckets"])]
        for k, v in p["by_symbol"].items():
            out["by_symbol"][k] = out["by_symbol"].get(k, 0.0) + v
    return out


def simulate(config: Config, spins=1_000_000, workers=4, seed=None) -> dict:
    """Monte Carlo over `spins` spins using `workers` processes. All figures are per total bet."""
    seed = secrets.randbits(63) if seed is None else seed
    per = spins // workers
    jobs = [(config.raw, per + (spins - per * workers if i == 0 else 0), seed + i, None)
            for i in range(workers)]
    if workers == 1:
        parts = [_worker(jobs[0])]
    else:
        with ProcessPoolExecutor(workers) as ex:
            parts = list(ex.map(_worker, jobs))
    s = _merge(parts)
    n = s["n"]
    mean = s["sum"] / n
    var = max(s["sumsq"] / n - mean * mean, 0.0)
    sd = math.sqrt(var)
    half = 1.96 * sd / math.sqrt(n)
    return {
        "spins": n, "seed": seed,
        "base_rtp": mean,
        "scatter_rtp": s["scatter"] / n,
        "wild_expansion_rtp": s["wild"] / n,
        "hit_frequency": s["hits"] / n,
        "expansion_frequency": s["expanded"] / n,
        "std_dev": sd,
        "volatility_index": 1.96 * sd,
        "max_win_observed": s["max"],
        "capped_spins": s["capped"],
        "rtp_ci95": (mean - half, mean + half),
        "buckets": dict(zip(BUCKETS, s["buckets"])),
        "rtp_by_line_symbol": {k: v / n for k, v in s["by_symbol"].items()},
        "jackpot_rtp_separate": None,
    }


def format_report(rep: dict) -> str:
    lo, hi = rep["rtp_ci95"]
    lines = [
        f"Spins:               {rep['spins']:,} (seed {rep['seed']})",
        f"Base RTP:            {rep['base_rtp']:.4%}  (95% CI {lo:.4%} .. {hi:.4%})",
        f"  scatter RTP:       {rep['scatter_rtp']:.4%}",
        f"  wild expansion:    {rep['wild_expansion_rtp']:.4%}",
        f"Hit frequency:       {rep['hit_frequency']:.4%}",
        f"Expansion frequency: {rep['expansion_frequency']:.4%}",
        f"Std deviation:       {rep['std_dev']:.3f}  Volatility index (1.96 sd): {rep['volatility_index']:.3f}",
        f"Max win observed:    {rep['max_win_observed']:.2f}x  (capped spins: {rep['capped_spins']})",
        "Win distribution:",
    ]
    for k, v in rep["buckets"].items():
        lines.append(f"  {k:>10}: {v / rep['spins']:.4%}")
    return "\n".join(lines)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Royal Fruits Monte Carlo simulator")
    ap.add_argument("--spins", type=int, default=1_000_000)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--config")
    a = ap.parse_args(argv)
    cfg = Config.load(a.config) if a.config else Config.load()
    print(format_report(simulate(cfg, a.spins, a.workers, a.seed)))


if __name__ == "__main__":
    main()
