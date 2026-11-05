"""Deep Miner Monte Carlo simulator: python -m royalfruits.miner_sim --rounds 10000000 --workers 16

Reuses the slot simulator infrastructure (seeded RNG, multi-process workers, win buckets) and plays
complete rounds - pickaxe draw, mine generation and mining - exactly as the game does.
"""
import math
import secrets
from concurrent.futures import ProcessPoolExecutor
from decimal import Decimal

from .miner import MinerEngine
from .miner_config import MinerConfig
from .rng import SeededRNG
from .simulator import BUCKETS, bucket_index

ADD_KEYS = ("n", "sum", "sumsq", "ore", "tnt", "bottom", "none", "mined", "depth", "upg_rounds", "upg_total",
            "bottom_rounds", "cap_rounds")


def _worker(args):
    raw, n, seed = args
    cfg = MinerConfig.from_dict(raw)
    eng = MinerEngine(cfg, SeededRNG(seed), record_events=False)
    one = Decimal(1)
    tiers = len(cfg.pickaxes) + 1
    s = dict.fromkeys(ADD_KEYS, 0)
    s.update(sum=0.0, sumsq=0.0, ore=0.0, tnt=0.0, bottom=0.0, max=0.0, buckets=[0] * 7,
             tier_n=[0] * tiers, tier_win=[0.0] * tiers, tier_depth=[0] * tiers, reasons={})
    for _ in range(n):
        r = eng.play(one)
        m = float(r.total_win)
        s["n"] += 1
        s["sum"] += m
        s["sumsq"] += m * m
        s["max"] = max(s["max"], m)
        s["buckets"][bucket_index(m)] += 1
        t = r.start_tier
        s["tier_n"][t] += 1
        s["tier_win"][t] += m
        if t == 0:
            s["none"] += 1
            continue
        s["mined"] += 1
        s["ore"] += float(r.ore_win)
        s["tnt"] += float(r.tnt_win)
        s["bottom"] += float(r.bottom_win)
        s["depth"] += r.depth
        s["tier_depth"][t] += r.depth
        s["upg_rounds"] += r.upgrades > 0
        s["upg_total"] += r.upgrades
        s["bottom_rounds"] += r.reached_bottom
        s["cap_rounds"] += r.capped
        s["reasons"][r.reason] = s["reasons"].get(r.reason, 0) + 1
    return s


def _merge(parts):
    out = parts[0]
    for p in parts[1:]:
        for k in ADD_KEYS:
            out[k] += p[k]
        out["max"] = max(out["max"], p["max"])
        for k in ("buckets", "tier_n", "tier_win", "tier_depth"):
            out[k] = [a + b for a, b in zip(out[k], p[k])]
        for k, v in p["reasons"].items():
            out["reasons"][k] = out["reasons"].get(k, 0) + v
    return out


def simulate_miner(config: MinerConfig, rounds=1_000_000, workers=4, seed=None) -> dict:
    """Monte Carlo over `rounds` paid rounds. All figures are per bet."""
    seed = secrets.randbits(63) if seed is None else seed
    per = rounds // workers
    jobs = [(config.raw, per + (rounds - per * workers if i == 0 else 0), seed + i) for i in range(workers)]
    if workers == 1:
        parts = [_worker(jobs[0])]
    else:
        with ProcessPoolExecutor(workers) as ex:
            parts = list(ex.map(_worker, jobs))
    s = _merge(parts)
    n, mined = s["n"], max(s["mined"], 1)
    mean = s["sum"] / n
    sd = math.sqrt(max(s["sumsq"] / n - mean * mean, 0.0))
    half = 1.96 * sd / math.sqrt(n)
    ids = ["NONE"] + [p.id for p in config.pickaxes]
    return {
        "rounds": n, "seed": seed,
        "total_rtp": mean, "rtp_ci95": (mean - half, mean + half),
        "ore_rtp": s["ore"] / n, "tnt_rtp": s["tnt"] / n, "bottom_rtp": s["bottom"] / n,
        "nothing_rate": s["none"] / n,
        "tier_share": {ids[i]: s["tier_n"][i] / n for i in range(len(ids))},
        "tier_rtp": {ids[i]: s["tier_win"][i] / n for i in range(len(ids))},
        "tier_avg_win": {ids[i]: (s["tier_win"][i] / s["tier_n"][i]) if s["tier_n"][i] else 0.0 for i in range(1, len(ids))},
        "tier_avg_depth": {ids[i]: (s["tier_depth"][i] / s["tier_n"][i]) if s["tier_n"][i] else 0.0 for i in range(1, len(ids))},
        "avg_depth_per_mine": s["depth"] / mined,
        "avg_depth_per_round": s["depth"] / n,
        "upgrade_rate_per_mine": s["upg_rounds"] / mined,
        "upgrades_per_mine": s["upg_total"] / mined,
        "bottom_rate": s["bottom_rounds"] / n,
        "bottom_rate_per_mine": s["bottom_rounds"] / mined,
        "capped_rounds": s["cap_rounds"],
        "end_reasons": {k: v / mined for k, v in sorted(s["reasons"].items())},
        "std_dev": sd, "max_win": s["max"],
        "buckets": dict(zip(BUCKETS, s["buckets"])),
    }


def format_report(rep: dict) -> str:
    lo, hi = rep["rtp_ci95"]
    lines = [
        f"Rounds:              {rep['rounds']:,} (seed {rep['seed']})",
        f"TOTAL RTP:           {rep['total_rtp']:.4%}   (95% CI {lo:.4%} .. {hi:.4%})",
        f"  ore pays:          {rep['ore_rtp']:.4%}",
        f"  TNT pays:          {rep['tnt_rtp']:.4%}   (ores destroyed by explosions)",
        f"  bottom bonus:      {rep['bottom_rtp']:.4%}",
        f"'Nothing' rate:      {rep['nothing_rate']:.4%}",
        "Rounds by drawn pickaxe (share | RTP contribution | avg win x bet | avg depth m):",
    ]
    for k, share in rep["tier_share"].items():
        if k == "NONE":
            lines.append(f"  {k:<8} {share:8.3%} | {rep['tier_rtp'][k]:8.3%}")
        else:
            lines.append(f"  {k:<8} {share:8.3%} | {rep['tier_rtp'][k]:8.3%} | {rep['tier_avg_win'][k]:8.3f} | {rep['tier_avg_depth'][k]:6.2f}")
    lines += [
        f"Avg depth reached:   {rep['avg_depth_per_mine']:.2f} m per mined round ({rep['avg_depth_per_round']:.2f} m per paid round)",
        f"Upgrade frequency:   {rep['upgrade_rate_per_mine']:.3%} of mined rounds ({rep['upgrades_per_mine']:.3f} upgrades per mined round)",
        f"Bottom reached:      {rep['bottom_rate']:.4%} of rounds ({rep['bottom_rate_per_mine']:.3%} of mined)",
        f"Round ends:          { {k: f'{v:.2%}' for k, v in rep['end_reasons'].items()} }",
        f"Std deviation:       {rep['std_dev']:.3f}",
        f"Max win:             {rep['max_win']:.2f}x  (capped rounds: {rep['capped_rounds']})",
        "Win distribution:",
    ]
    for k, v in rep["buckets"].items():
        lines.append(f"  {k:>10}: {v / rep['rounds']:.4%}")
    return "\n".join(lines)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Deep Miner Monte Carlo simulator")
    ap.add_argument("--rounds", type=int, default=1_000_000)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--config")
    a = ap.parse_args(argv)
    cfg = MinerConfig.load(a.config) if a.config else MinerConfig.load()
    print(format_report(simulate_miner(cfg, a.rounds, a.workers, a.seed)))


if __name__ == "__main__":
    main()
