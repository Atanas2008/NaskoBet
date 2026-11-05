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


def exact_trigger_distribution(config: Config) -> dict:
    """Exact distribution of the number of scatter-B symbols on the base grid (reels are independent,
    so the per-reel window counts are convolved). Returns {count: Fraction}."""
    from fractions import Fraction
    dist = {0: Fraction(1)}
    for strip in config.strips:
        n = len(strip)
        per = {}
        for stop in range(n):
            c = sum(strip[(stop + r) % n] == config.scatter_b for r in range(config.rows))
            per[c] = per.get(c, 0) + Fraction(1, n)
        nxt = {}
        for a, pa in dist.items():
            for b, pb in per.items():
                nxt[a + b] = nxt.get(a + b, 0) + pa * pb
        dist = nxt
    return dist


def exact_trigger_stats(config: Config) -> dict:
    """Exact free-spins trigger probability, award split and scatter-B pay RTP of the base game."""
    from fractions import Fraction
    from .config import FreeSpinSettings
    fs = config.free_spins
    split, p_trigger, scatter_rtp = {}, Fraction(0), Fraction(0)
    for n, p in exact_trigger_distribution(config).items():
        usable = [k for k in config.scatter_b_pay if k <= n]
        if usable:
            scatter_rtp += p * Fraction(config.scatter_b_pay[max(usable)])
        award = FreeSpinSettings.award_for(fs.awards, n) if fs else 0
        if award:
            p_trigger += p
            split[award] = split.get(award, Fraction(0)) + p
    return {"p_trigger": p_trigger, "award_probabilities": split, "scatter_b_rtp": scatter_rtp}


def _worker(args):
    raw, n, seed, symbol_attr = args
    cfg = Config.from_dict(raw)
    eng = GameEngine(cfg, SeededRNG(seed))
    one = Decimal(1)
    s = dict(n=0, sum=0.0, sumsq=0.0, scatter=0.0, wild=0.0, hits=0, max=0.0, capped=0,
             expanded=0, buckets=[0] * 7, by_symbol={},
             total_sum=0.0, total_sumsq=0.0, fs_sum=0.0, triggers=0, fs_spins=0, fs_max=0.0,
             fs_retriggered=0, fs_retriggers=0, fs_capped=0, awards={})
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
        bonus_m = 0.0
        if r.free_spins_awarded:
            s["triggers"] += 1
            s["awards"][r.free_spins_awarded] = s["awards"].get(r.free_spins_awarded, 0) + 1
            b = eng.start_bonus(r)
            while b.spins_left > 0:
                eng.free_spin(b)
            bonus_m = float(b.total_win / r.total_bet)
            s["fs_sum"] += bonus_m
            s["fs_max"] = max(s["fs_max"], bonus_m)
            s["fs_spins"] += b.spins_played
            s["fs_retriggers"] += b.retriggers
            s["fs_retriggered"] += b.retriggers > 0
            s["fs_capped"] += b.capped
        s["total_sum"] += m + bonus_m
        s["total_sumsq"] += (m + bonus_m) ** 2
    return s


def _merge(parts):
    out = parts[0]
    for p in parts[1:]:
        for k in ("n", "sum", "sumsq", "scatter", "wild", "hits", "capped", "expanded", "total_sum",
                  "total_sumsq", "fs_sum", "triggers", "fs_spins", "fs_retriggered", "fs_retriggers", "fs_capped"):
            out[k] += p[k]
        out["max"] = max(out["max"], p["max"])
        out["fs_max"] = max(out["fs_max"], p["fs_max"])
        out["buckets"] = [a + b for a, b in zip(out["buckets"], p["buckets"])]
        for k, v in p["by_symbol"].items():
            out["by_symbol"][k] = out["by_symbol"].get(k, 0.0) + v
        for k, v in p["awards"].items():
            out["awards"][k] = out["awards"].get(k, 0) + v
    return out


def _jackpot_contribution(config: Config) -> float:
    j = config.jackpot
    if not j or not j.get("enabled", True):
        return 0.0
    return float(sum(Decimal(str(v["contribution_pct"])) for v in j["levels"].values()) / 100)


def simulate(config: Config, spins=1_000_000, workers=4, seed=None) -> dict:
    """Monte Carlo over `spins` paid base spins using `workers` processes. All figures are per total bet.
    When the game has a free-spins bonus every triggered bonus is played out inside the simulation."""
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
    rep = {
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
        "has_free_spins": bool(config.free_spins),
    }
    if config.free_spins:
        tmean = s["total_sum"] / n
        tsd = math.sqrt(max(s["total_sumsq"] / n - tmean * tmean, 0.0))
        thalf = 1.96 * tsd / math.sqrt(n)
        trig = s["triggers"]
        fs_rtp = s["fs_sum"] / n
        # Free spins also feed/trigger the jackpot at the nominal bet, so its contribution scales with them.
        jackpot = _jackpot_contribution(config) * (1 + s["fs_spins"] / n)
        rep.update({
            "jackpot_rtp_separate": jackpot, "jackpot_rtp": jackpot,
            "base_line_rtp": mean - s["scatter"] / n,
            "free_spins_rtp": fs_rtp,
            "game_rtp": mean + fs_rtp,                      # base + free spins, jackpot excluded
            "total_rtp": mean + fs_rtp + jackpot,
            "total_rtp_ci95": (tmean + jackpot - thalf, tmean + jackpot + thalf),
            "bonus_triggers": trig,
            "bonus_trigger_frequency": trig / n,
            "bonus_one_in": (n / trig) if trig else None,
            "bonus_award_split": dict(sorted(s["awards"].items())),
            "avg_bonus_win_x": (s["fs_sum"] / trig) if trig else 0.0,
            "largest_bonus_win_x": s["fs_max"],
            "retrigger_rate": (s["fs_retriggered"] / trig) if trig else 0.0,
            "avg_retriggers_per_bonus": (s["fs_retriggers"] / trig) if trig else 0.0,
            "avg_bonus_spins": (s["fs_spins"] / trig) if trig else 0.0,
            "bonus_capped": s["fs_capped"],
        })
    return rep


def format_report(rep: dict) -> str:
    lo, hi = rep["rtp_ci95"]
    lines = [f"Spins:               {rep['spins']:,} (seed {rep['seed']})"]
    if rep.get("has_free_spins"):
        tlo, thi = rep["total_rtp_ci95"]
        one_in = rep["bonus_one_in"]
        lines += [
            "RTP breakdown (share of total bet):",
            f"  Base game lines:   {rep['base_line_rtp']:.4%}   (incl. wild expansion {rep['wild_expansion_rtp']:.4%})",
            f"  Scatter pays:      {rep['scatter_rtp']:.4%}   (base game)",
            f"  Base RTP:          {rep['base_rtp']:.4%}   (95% CI {lo:.4%} .. {hi:.4%})",
            f"  Free spins RTP:    {rep['free_spins_rtp']:.4%}",
            f"  Base + free spins: {rep['game_rtp']:.4%}   (jackpot excluded)",
            f"  Jackpot RTP:       {rep['jackpot_rtp']:.4%}   (contributions, incl. free-spin bets)",
            f"  TOTAL RTP:         {rep['total_rtp']:.4%}   (95% CI {tlo:.4%} .. {thi:.4%})",
            "Free spins bonus:",
            f"  Trigger frequency: {rep['bonus_trigger_frequency']:.4%}"
            + (f"  = 1 in {one_in:,.1f} spins" if one_in else "  (no triggers)"),
            f"  Award split:       {rep['bonus_award_split']}",
            f"  Avg bonus win:     {rep['avg_bonus_win_x']:.2f}x bet   (largest {rep['largest_bonus_win_x']:.2f}x, capped bonuses {rep['bonus_capped']})",
            f"  Retrigger rate:    {rep['retrigger_rate']:.2%} of bonuses  (avg {rep['avg_retriggers_per_bonus']:.3f} per bonus)",
            f"  Avg spins/bonus:   {rep['avg_bonus_spins']:.2f}",
        ]
    else:
        lines += [
            f"Base RTP:            {rep['base_rtp']:.4%}  (95% CI {lo:.4%} .. {hi:.4%})",
            f"  scatter RTP:       {rep['scatter_rtp']:.4%}",
            f"  wild expansion:    {rep['wild_expansion_rtp']:.4%}",
        ]
    lines += [
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
    ap = argparse.ArgumentParser(description="Royal Fruits / Royal Cats Monte Carlo simulator")
    ap.add_argument("--spins", type=int, default=1_000_000)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--config")
    a = ap.parse_args(argv)
    cfg = Config.load(a.config) if a.config else Config.load()
    print(format_report(simulate(cfg, a.spins, a.workers, a.seed)))
    if cfg.free_spins:
        ex = exact_trigger_stats(cfg)
        p = float(ex["p_trigger"])
        print(f"Exact (analytic):    trigger 1 in {1 / p:,.2f} spins, scatter pay RTP {float(ex['scatter_b_rtp']):.4%}, "
              f"award split { {k: f'1 in {1 / float(v):,.0f}' for k, v in sorted(ex['award_probabilities'].items())} }")


if __name__ == "__main__":
    main()
