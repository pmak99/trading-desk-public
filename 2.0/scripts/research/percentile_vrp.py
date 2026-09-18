"""Percentile VRP vs ratio VRP, offline.

Ratio VRP is implied / mean(prior absolute moves). The denominator is a mean
over a fat-tailed sample of at most 12 observations (analyzer.py:88, floored at
4 by config.py) -- the least robust estimator available. This asks whether
P(realised < implied), the percentile of the prior distribution the market is
pricing at, orders events better.

Offline only. No migration, no live logging, no /ab-status entry unless it
clears the pre-registered bar below.

This SUPERSEDES the median/winsorised-denominator idea: a percentile is
invariant to mean-vs-median by construction, so that change becomes moot if
this lands. It also sidesteps a trap -- median < mean on fat tails, so a
median denominator would *lower* the VRP denominator and inflate every ratio,
loosening the gate in the same dangerous direction as the duplicate-row bug.

Stdlib only, matching ab_evaluate_live.py.
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from research.events import load_events
from research.placebo import top_k_crush

# --- Pre-registered criteria. Fixed BEFORE the first run; do not tune. -------
TOP_K_PCT = 20
BOOTSTRAP_N = 20000
SEED = 7
MIN_EFFECT_PTS = 3.0   # same bar as ab_evaluate_live.py's VRP test
# PASS requires BOTH: percentile top-K crush beats ratio top-K crush by
# >= MIN_EFFECT_PTS, AND the bootstrap 95% CI lower bound is above zero.
# The CI condition is what stops a large-but-noisy difference from counting.


def percentile_rank(implied: float, prior_moves: list[float]) -> float:
    """Fraction of prior absolute moves strictly below the implied move."""
    if not prior_moves:
        raise ValueError("percentile_rank needs at least one prior move")
    return sum(1 for m in prior_moves if m < implied) / len(prior_moves)


def resolution_pts(n: float) -> float:
    """Finest percentile step resolvable from n observations, in points.

    Reported alongside every estimate so the figure is not read as more
    precise than the sample supports: at n=12 the step is ~8.3 points, so
    two events differing by less than that are not really distinguishable.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    return 100 / n


def bootstrap_delta(events, n_boot=BOOTSTRAP_N, seed=SEED):
    """95% CI on (percentile top-K crush - ratio top-K crush)."""
    rng = random.Random(seed)
    n = len(events)
    m = max(1, round(n * TOP_K_PCT / 100))
    diffs = []
    for _ in range(n_boot):
        s = [events[rng.randrange(n)] for _ in range(n)]
        tp = sorted(s, key=lambda e: -e["pctile"])[:m]
        tr = sorted(s, key=lambda e: -e["vrp"])[:m]
        diffs.append(
            sum(e["crushed"] for e in tp) / m - sum(e["crushed"] for e in tr) / m
        )
    diffs.sort()
    return (
        diffs[int(0.025 * len(diffs))],
        diffs[int(0.975 * len(diffs))],
        sum(1 for d in diffs if d <= 0) / len(diffs),
    )


def verdict(events: list[dict]) -> dict:
    for e in events:
        e["pctile"] = percentile_rank(e["straddle"], e["prior_moves"])
    ratio_rate, m = top_k_crush(events, "vrp", TOP_K_PCT)
    pct_rate, _ = top_k_crush(events, "pctile", TOP_K_PCT)
    lo, hi, p_le0 = bootstrap_delta(events)
    gap_pts = 100 * (pct_rate - ratio_rate)
    mean_n = sum(len(e["prior_moves"]) for e in events) / len(events)
    return {
        "n_events": len(events),
        "top_k": m,
        "ratio_crush_pct": 100 * ratio_rate,
        "pctile_crush_pct": 100 * pct_rate,
        "gap_pts": gap_pts,
        "ci_lo_pts": 100 * lo,
        "ci_hi_pts": 100 * hi,
        "p_le0": p_le0,
        "mean_resolution_pts": resolution_pts(mean_n),
        "passes": gap_pts >= MIN_EFFECT_PTS and lo > 0,
    }


if __name__ == "__main__":
    v = verdict(load_events(baseline="intraday"))
    print(f"events                 : {v['n_events']}")
    print(f"top {TOP_K_PCT}% size            : {v['top_k']}")
    print(f"ratio-VRP crush        : {v['ratio_crush_pct']:.1f}%")
    print(f"percentile-VRP crush   : {v['pctile_crush_pct']:.1f}%")
    print(f"gap                    : {v['gap_pts']:+.1f} pts "
          f"(need >= {MIN_EFFECT_PTS})")
    print(f"bootstrap 95% CI       : [{v['ci_lo_pts']:+.1f}, {v['ci_hi_pts']:+.1f}] pts")
    print(f"P(delta <= 0)          : {v['p_le0']:.3f}")
    print(f"mean percentile step   : {v['mean_resolution_pts']:.1f} pts "
          f"-- estimates are no finer than this")
    print(f"VERDICT                : {'PASS' if v['passes'] else 'FAIL'}")
