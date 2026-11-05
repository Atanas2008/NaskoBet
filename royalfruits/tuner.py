"""Tuner: heuristic suggestions of reel strip changes to reach a target RTP.

Uses the per-symbol RTP attribution from a simulation. If RTP is too high, suggests
replacing symbols of the highest-contributing pay symbol with the lowest-paying one
(on reels 3-5 first, where stacks matter most); if too low, the reverse. The
suggestion is a starting point - re-simulate after applying it.
"""
import math


def suggest_adjustments(config, report, target=0.962, tolerance=0.002):
    rtp = report["base_rtp"]
    if abs(rtp - target) <= tolerance:
        return []
    by_sym = report["rtp_by_line_symbol"]
    pay_order = [s for s in config.line_symbols]  # low -> high
    too_high = rtp > target
    # line RTP scales roughly with (share of symbol)^k for k~3 reels; use that to size the change
    needed = abs(target - rtp) / max(rtp, 1e-9)
    suggestions = []
    if too_high:
        src = max(by_sym, key=by_sym.get) if by_sym else pay_order[-1]
        dst = pay_order[0]
    else:
        src = pay_order[0]
        dst = pay_order[-1] if not by_sym else min(by_sym, key=by_sym.get)
    for reel in (4, 3, 2):  # reels 5,4,3 (zero-based)
        strip = config.strips[reel]
        have = strip.count(src)
        if src == dst or have == 0 and too_high:
            continue
        count = max(1, round(have * needed / 3)) if too_high else max(1, round(needed * len(strip) / 20))
        suggestions.append({
            "reel": reel + 1,
            "replace": src, "with": dst, "positions": count,
            "note": f"RTP {rtp:.3%} -> target {target:.3%}: "
                    f"{'lower' if too_high else 'raise'} by changing {count} {src} to {dst}",
        })
    return suggestions
