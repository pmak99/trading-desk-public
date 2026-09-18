"""Small-sample VRP denominators, offline (audit 4.4).

Ratio VRP divides the implied move by the mean of the prior absolute moves.
MIN_HISTORICAL_QUARTERS is 4, so a name with 4-7 quarters of history is
tradeable on a mean of 4-7 fat-tailed observations -- the noisiest the system
accepts. This asks whether a denominator built to be robust at that sample size
orders those events better than the raw mean does, at the same selection rate.

Offline only. Nothing live changes unless a pre-registered verdict below reads
ADOPT; see ADOPTION.

THE TWO CANDIDATES (both fixed before the first run)
----------------------------------------------------
PRIMARY -- conservative VRP, `ucb`: implied / the one-sided 90% upper confidence
bound of the mean prior move, mean + t(0.90, n-1) * sd / sqrt(n). A short or
dispersed history gets a larger denominator and a lower VRP, so the adjustment
moves in the SAFE direction -- unlike a median or winsorised mean, which lower
the denominator of a fat-tailed sample and inflate VRP in the same direction as
the duplicate-row bug (docs/RESEARCH_RESULTS_2026-09.md section 2, which is also
why neither is a candidate here).

SECONDARY -- shrinkage, `shrunk`: the mean prior move shrunk toward a universe
prior with SHRINK_PSEUDO_QUARTERS pseudo-quarters, (n * mean + k * S * U) / (n + k),
where S is this event's straddle and U the universe mean of (mean prior move /
straddle). U uses predictors only -- no outcome enters it -- so computing it once
over the whole sample carries no outcome look-ahead. With k = 4 a 4-quarter history
is half prior and a 12-quarter one a quarter, the same n/(n+4) curve as
ConsistencyAnalyzerEnhanced's sample factor. For a fixed n this is monotone in
raw VRP, so inside a stratum it reorders events only across history depths.

Winsorised and median denominators are deliberately not run (see above), and no
other confidence level, k or prior is tried.

METRIC, SAMPLE, CORRECTION
--------------------------
Sample: research.events.load_events(baseline="intraday") -- the intraday
convention live when this ran, 12-quarter lookback, 4-quarter floor, every
event carrying a real straddle. Stratum SMALL = 4-7 prior quarters; CONTROL =
8 or more. Depth and calendar time are confounded in this sample (history
starts ~2022, so a 4-quarter event is an early one); the control stratum is
reported so an effect that is really universe-wide, or an era effect, is not
read as a small-sample one.

Metric: top-20% crush rate within the stratum, candidate ranking minus raw
ranking, in points. Crush is the shared core's: straddle > |close move|, a
short-straddle proxy, never spread P&L. Paired bootstrap over events
(BOOTSTRAP_N resamples, SEED), one-sided p = P(delta <= 0), which is exactly
"95% CI lower bound > 0" (ab_evaluate_live.ci_and_p). PRIMARY and SECONDARY
are one Holm family at ALPHA_ONE_SIDED.

ADOPTION
--------
An estimator's verdict, in the SMALL stratum:
  FAIL           delta < MIN_EFFECT_PTS or Holm p > ALPHA_ONE_SIDED.
  UNIVERSE-WIDE  it passes, and the CONTROL stratum passes on its own terms
                 too (delta >= MIN_EFFECT_PTS, CI lower bound > 0). Not a
                 small-sample effect: nothing is adopted under this registration.
  HARMS CONTROL  it passes, but the CONTROL CI lower bound is at or below
                 HARM_BOUND_PTS: adoption would change deep-history names too
                 and a 3-point loss there cannot be ruled out. Not adopted.
  ADOPT          it passes and neither of the above. If both estimators ADOPT,
                 the PRIMARY is the one implemented.

AUC, mean net edge, the pooled top-20% and tier flips are printed as
description and decide nothing.

Stdlib only, matching the rest of research/.
"""

import heapq
import math
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from ab_evaluate_live import ci_and_p, holm  # noqa: E402

from research.events import load_events, net_edge  # noqa: E402
from src.domain.vrp_tier import vrp_tier  # noqa: E402

# --- Pre-registered criteria. Fixed BEFORE the first run; do not tune. -------
SMALL_MIN_QUARTERS = 4       # MIN_HISTORICAL_QUARTERS: the shallowest tradeable history
CONTROL_MIN_QUARTERS = 8     # the audit's "noisy below 8 quarters"; SMALL is 4-7
TOP_K_PCT = 20               # same selectivity as placebo, percentile_vrp, ab_evaluate_live
BOOTSTRAP_N = 20000
SEED = 7
MIN_EFFECT_PTS = 3.0         # same bar as percentile_vrp and the live VRP A/B
ALPHA_ONE_SIDED = 0.025      # = "bootstrap 95% CI lower bound > 0"
HARM_BOUND_PTS = -3.0        # CONTROL CI lower bound must sit above this to adopt
UCB_CONFIDENCE = 0.90        # one-sided level of the PRIMARY's upper bound
SHRINK_PSEUDO_QUARTERS = 4   # k of the SECONDARY
PRIMARY = "ucb"
SECONDARY = "shrunk"

# Student t quantiles at UCB_CONFIDENCE, by degrees of freedom. The lookback is
# 12 quarters, so df never exceeds 11. A table rather than scipy (research/ is
# stdlib only); scripts/tests/test_small_sample_vrp.py checks every entry against a
# numerical integration of the t density.
T_QUANTILE_90 = {
    1: 3.077684, 2: 1.885618, 3: 1.637744, 4: 1.533206, 5: 1.475884, 6: 1.439756,
    7: 1.414924, 8: 1.396815, 9: 1.383029, 10: 1.372184, 11: 1.363430,
}

def ucb_mean(moves: list[float]) -> float:
    """One-sided UCB_CONFIDENCE upper confidence bound of the mean |move|."""
    n = len(moves)
    if n < 2:
        raise ValueError("an upper confidence bound needs at least two moves")
    if n - 1 not in T_QUANTILE_90:
        raise ValueError(f"no t quantile tabulated for {n - 1} degrees of freedom")
    return statistics.mean(moves) + T_QUANTILE_90[n - 1] * statistics.stdev(moves) / math.sqrt(n)


def ucb_vrp(straddle: float, moves: list[float]) -> float:
    return straddle / ucb_mean(moves)


def universe_prior(events: list[dict]) -> float:
    """U: mean over events of (mean prior |move| / straddle). Predictors only."""
    return statistics.mean(e["mean_prior"] / e["straddle"] for e in events)


def shrunk_mean(straddle: float, moves: list[float], u: float,
                k: int = SHRINK_PSEUDO_QUARTERS) -> float:
    n = len(moves)
    return (n * statistics.mean(moves) + k * straddle * u) / (n + k)


def shrunk_vrp(straddle: float, moves: list[float], u: float,
               k: int = SHRINK_PSEUDO_QUARTERS) -> float:
    return straddle / shrunk_mean(straddle, moves, u, k)


def tier(vrp: float) -> str:
    return vrp_tier(vrp, 1.8, 1.4, 1.2).name   # the intraday ladder this study ran on


def stratum(e: dict) -> str | None:
    n = len(e["prior_moves"])
    if n >= CONTROL_MIN_QUARTERS:
        return "control"
    if n >= SMALL_MIN_QUARTERS:
        return "small"
    return None


def annotate(events: list[dict]) -> float:
    """Add `ucb` and `shrunk` to every event; return the universe prior U."""
    u = universe_prior(events)
    for e in events:
        e["ucb"] = ucb_vrp(e["straddle"], e["prior_moves"])
        e["shrunk"] = shrunk_vrp(e["straddle"], e["prior_moves"], u)
    return u


def top_k(events: list[dict], key: str, pct: int = TOP_K_PCT) -> list[dict]:
    """The top pct% by key. Ties keep input order, so a resample is deterministic."""
    m = max(1, round(len(events) * pct / 100))
    return heapq.nlargest(m, events, key=lambda e: e[key])


def crush_rate(events: list[dict]) -> float:
    return sum(e["crushed"] for e in events) / len(events)


def paired_bootstrap(events, keys=(PRIMARY, SECONDARY), n_boot=BOOTSTRAP_N, seed=SEED):
    """{key: [top-K crush(key) - top-K crush(vrp)]} over the same resamples."""
    rng = random.Random(seed)
    n = len(events)
    diffs = {k: [] for k in keys}
    for _ in range(n_boot):
        s = [events[rng.randrange(n)] for _ in range(n)]
        base = crush_rate(top_k(s, "vrp"))
        for k in keys:
            diffs[k].append(crush_rate(top_k(s, k)) - base)
    return diffs


def auc(events: list[dict], key: str) -> float:
    """P(a crushed event outranks a non-crushed one), ties counted half."""
    ranked = sorted(events, key=lambda e: e[key])
    ranks, i = {}, 0
    while i < len(ranked):
        j = i
        while j + 1 < len(ranked) and ranked[j + 1][key] == ranked[i][key]:
            j += 1
        for idx in range(i, j + 1):
            ranks[id(ranked[idx])] = (i + j) / 2 + 1
        i = j + 1
    pos = [e for e in events if e["crushed"]]
    n_pos, n_neg = len(pos), len(events) - len(pos)
    if not n_pos or not n_neg:
        return float("nan")
    rank_sum = sum(ranks[id(e)] for e in pos)
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def stratum_stats(events: list[dict], n_boot=BOOTSTRAP_N, seed=SEED) -> dict:
    diffs = paired_bootstrap(events, n_boot=n_boot, seed=seed)
    top_raw = top_k(events, "vrp")
    out = {
        "n": len(events),
        "top_k": len(top_raw),
        "base_crush_pct": 100 * crush_rate(events),
        "raw_crush_pct": 100 * crush_rate(top_raw),
        "raw_auc": auc(events, "vrp"),
        "raw_net_edge": statistics.mean(net_edge(e["edge"], e["straddle"]) for e in top_raw),
    }
    for key in (PRIMARY, SECONDARY):
        top = top_k(events, key)
        lo, hi, p = ci_and_p(diffs[key])
        out[key] = {
            "crush_pct": 100 * crush_rate(top),
            "delta": 100 * (crush_rate(top) - crush_rate(top_raw)),
            "lo": 100 * lo,
            "hi": 100 * hi,
            "p_le0": p,
            "auc": auc(events, key),
            "net_edge": statistics.mean(net_edge(e["edge"], e["straddle"]) for e in top),
            "overlap": len({id(e) for e in top} & {id(e) for e in top_raw}) / len(top_raw),
            "tier_flips": sum(tier(e["vrp"]) != tier(e[key]) for e in events) / len(events),
        }
    return out


def decide(small: dict, control: dict, p_adj: float) -> str:
    """One estimator's verdict. See ADOPTION in the module docstring."""
    if small["delta"] < MIN_EFFECT_PTS or p_adj > ALPHA_ONE_SIDED:
        return "FAIL"
    if control["delta"] >= MIN_EFFECT_PTS and control["lo"] > 0:
        return "UNIVERSE-WIDE"
    if control["lo"] <= HARM_BOUND_PTS:
        return "HARMS CONTROL"
    return "ADOPT"


def verdict(events: list[dict], n_boot=BOOTSTRAP_N, seed=SEED) -> dict:
    u = annotate(events)
    small = [e for e in events if stratum(e) == "small"]
    control = [e for e in events if stratum(e) == "control"]
    s = stratum_stats(small, n_boot, seed)
    c = stratum_stats(control, n_boot, seed)
    adj = holm({PRIMARY: s[PRIMARY]["p_le0"], SECONDARY: s[SECONDARY]["p_le0"]})
    pooled_raw = crush_rate(top_k(events, "vrp"))
    return {
        "n_events": len(events),
        "universe_prior": u,
        "small": s,
        "control": c,
        "holm": adj,
        "decisions": {k: decide(s[k], c[k], adj[k]) for k in (PRIMARY, SECONDARY)},
        "pooled": {
            "raw_crush_pct": 100 * pooled_raw,
            **{k: 100 * (crush_rate(top_k(events, k)) - pooled_raw) for k in (PRIMARY, SECONDARY)},
        },
        "dates": {
            name: (min(e["earnings_date"] for e in grp), max(e["earnings_date"] for e in grp),
                   sorted(e["earnings_date"] for e in grp)[len(grp) // 2])
            for name, grp in (("small", small), ("control", control))
        },
    }


def _print_stratum(label, st_, dates):
    lo_d, hi_d, med_d = dates
    print(f"\n{label}: {st_['n']} events, {lo_d} -> {hi_d} (median {med_d}), "
          f"top {TOP_K_PCT}% = {st_['top_k']}, base crush {st_['base_crush_pct']:.1f}%")
    print(f"  {'ranking':8s} {'top-K crush':>11s} {'delta pts':>9s} {'95% CI':>16s} "
          f"{'P(d<=0)':>8s} {'AUC':>6s} {'net edge':>8s} {'overlap':>7s} {'tier flips':>10s}")
    print(f"  {'raw vrp':8s} {st_['raw_crush_pct']:10.1f}% {'':>9s} {'':>16s} {'':>8s} "
          f"{st_['raw_auc']:6.3f} {st_['raw_net_edge']:+8.2f}")
    for key in (PRIMARY, SECONDARY):
        d = st_[key]
        print(f"  {key:8s} {d['crush_pct']:10.1f}% {d['delta']:+9.1f} "
              f"[{d['lo']:+6.1f}, {d['hi']:+6.1f}] {d['p_le0']:8.3f} {d['auc']:6.3f} "
              f"{d['net_edge']:+8.2f} {100 * d['overlap']:6.1f}% {100 * d['tier_flips']:9.1f}%")


if __name__ == "__main__":
    v = verdict(load_events(baseline="intraday"))
    print("SMALL-SAMPLE VRP DENOMINATORS (audit 4.4) -- straddle proxy, net of costs")
    print(f"pre-registered: PRIMARY={PRIMARY} (t {UCB_CONFIDENCE:.2f} one-sided UCB), "
          f"SECONDARY={SECONDARY} (k={SHRINK_PSEUDO_QUARTERS}), top {TOP_K_PCT}%, "
          f"bootstrap {BOOTSTRAP_N} seed {SEED}")
    print(f"  pass: delta >= +{MIN_EFFECT_PTS} pts and Holm p <= {ALPHA_ONE_SIDED} in 4-"
          f"{CONTROL_MIN_QUARTERS - 1} quarters; control CI lower > {HARM_BOUND_PTS} pts; "
          f"universe-wide if control passes too")
    print(f"events {v['n_events']}; universe prior U = {v['universe_prior']:.4f} "
          f"(mean prior move / straddle)")
    _print_stratum(f"SMALL (4-{CONTROL_MIN_QUARTERS - 1} quarters)", v["small"], v["dates"]["small"])
    _print_stratum(f"CONTROL ({CONTROL_MIN_QUARTERS}+ quarters)", v["control"], v["dates"]["control"])
    p = v["pooled"]
    print(f"\npooled, descriptive: raw top-{TOP_K_PCT}% crush {p['raw_crush_pct']:.1f}%; "
          f"{PRIMARY} {p[PRIMARY]:+.1f} pts, {SECONDARY} {p[SECONDARY]:+.1f} pts")
    print("net edge = mean net_edge() of the top-K, straddle pts; tier flips = units, not a test")
    print()
    for key in (PRIMARY, SECONDARY):
        role = "PRIMARY" if key == PRIMARY else "SECONDARY"
        print(f"{role} {key}: Holm p = {v['holm'][key]:.3f} -> {v['decisions'][key]}")
