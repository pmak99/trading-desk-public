"""Placebo test: does the ranking procedure manufacture an apparent edge?

Shuffles which realised outcome belongs to which event's implied move, then
re-ranks. If a shuffled run reproduces the real top-K crush rate, the apparent
selection edge is an artefact of the procedure rather than the signal.

Reassignment, not resampling: the marginal outcome distribution is preserved
exactly and each event keeps its own VRP, so the ONLY thing destroyed is the
pairing between them. That is what isolates "the signal predicts" from
"top-K of anything looks good because the base rate is high."

A negative result here is decision-relevant and must be written up either way.
Stdlib only, matching ab_evaluate_live.py.
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from research.events import load_events

# --- Pre-registered criteria. Fixed BEFORE the first run; do not tune. -------
# Written and committed ahead of seeing any result, matching how the
# term-structure and VRP-denominator tests in ab_evaluate_live.py are gated.
# Adjusting these after seeing an outcome would turn the test into a search
# for a favourable answer.
TOP_K_PCT = 20        # same selectivity as ab_evaluate_live.py
N_SHUFFLES = 1000
SEED = 7
MIN_GAP_PTS = 5.0     # real must beat the placebo mean by >= 5 points
# PASS requires BOTH: gap >= MIN_GAP_PTS, AND the real top-K crush rate sits
# above the 97.5th percentile of the shuffled distribution (a one-sided 2.5%
# test). Either alone is too weak -- a large gap with a wide null is not
# evidence, and clearing a narrow null by 0.5 points is not material.


def shuffle_outcomes(events: list[dict], seed: int) -> list[dict]:
    """Reassign outcomes across events, keeping each event's own VRP.

    Returns new dicts; the input is not mutated.
    """
    rng = random.Random(seed)
    outcomes = [(e["actual"], e["crushed"], e["edge"]) for e in events]
    rng.shuffle(outcomes)
    shuffled = []
    for e, (actual, crushed, edge) in zip(events, outcomes, strict=True):
        d = dict(e)
        d["actual"], d["crushed"], d["edge"] = actual, crushed, edge
        shuffled.append(d)
    return shuffled


def top_k_crush(events: list[dict], key: str, pct: int = TOP_K_PCT):
    m = max(1, round(len(events) * pct / 100))
    top = sorted(events, key=lambda e: -e[key])[:m]
    return sum(e["crushed"] for e in top) / m, m


def placebo_verdict(events: list[dict]) -> dict:
    real, m = top_k_crush(events, "vrp")
    rates = [
        top_k_crush(shuffle_outcomes(events, seed=SEED + i), "vrp")[0]
        for i in range(N_SHUFFLES)
    ]
    rates.sort()
    p975 = rates[int(0.975 * len(rates))]
    mean_placebo = sum(rates) / len(rates)
    gap_pts = 100 * (real - mean_placebo)
    return {
        "n_events": len(events),
        "top_k": m,
        "real_crush_pct": 100 * real,
        "placebo_mean_pct": 100 * mean_placebo,
        "placebo_p975_pct": 100 * p975,
        "gap_pts": gap_pts,
        "passes": gap_pts >= MIN_GAP_PTS and real > p975,
    }


if __name__ == "__main__":
    v = placebo_verdict(load_events(baseline="intraday"))
    print(f"events              : {v['n_events']}")
    print(f"top {TOP_K_PCT}% size         : {v['top_k']}")
    print(f"real crush          : {v['real_crush_pct']:.1f}%")
    print(f"placebo mean        : {v['placebo_mean_pct']:.1f}%")
    print(f"placebo 97.5th pct  : {v['placebo_p975_pct']:.1f}%")
    print(f"gap                 : {v['gap_pts']:+.1f} pts "
          f"(need >= {MIN_GAP_PTS})")
    print(f"VERDICT             : {'PASS' if v['passes'] else 'FAIL'}")
