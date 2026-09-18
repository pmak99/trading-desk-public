#!/usr/bin/env python3
"""Power of the live VRP-denominator A/B at its pre-registered gate.

Question: when ab_evaluate_live.py reaches MIN_EVENTS graded reactions, how often
does its PRIMARY test say MIGRATE if close-baseline VRP really selects more crush
than intraday by what the universe backtest measured? A gate that cannot pass
under the effect it exists to confirm turns "keep intraday" into the default
outcome rather than a finding. Descriptive: this decides nothing, and it changes
no pre-registered criterion.

The live decision, as ab_evaluate_live.vrp_decision makes it: MIGRATE iff
n >= MIN_EVENTS, top-TOP_K_PCT crush (close minus intraday, points) >=
MIN_EFFECT_PTS, and the Holm-adjusted one-sided p <= ALPHA_ONE_SIDED, p being the
share of bootstrap differences <= 0 with both rankings on one resample. The Holm
family is two tests (term structure is the other), so VRP's adjusted p lies
between p itself (term p near 0) and 2p (term p not smaller, the usual case).
Power is printed at both ends.

Two models of the sampling noise:

 1. Universe (simulated end to end). The matched backtest events -- 12-quarter
    lookback, raw VRP ranks, crushed = stored straddle > |close move|, as
    ab_test_vrp_baseline.py builds them -- are the population, and their top-K
    delta is the true effect. Each of DRAWS draws takes n events with replacement
    and runs the decision with BOOTSTRAP_N inner resamples.
 2. Live (normal approximation, labelled so). The backtest's top 20% crushes
    ~90% of the time; graded live reactions crush ~65%, and a rate nearer 0.5 is
    noisier. The delta's SD at n comes from resampling the graded live reactions;
    power at the universe effect is then Phi(effect/SD - z). It is the model of
    the conditions the gate will actually see.

Read-only: one SELECT per backtest baseline (research/events.py) and the live
evaluator's own load().

Usage (from 2.0/, with 2.0/.env exported):
    ./venv/bin/python scripts/research/ab_vrp_power.py
"""

import sys
from pathlib import Path
from statistics import NormalDist

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
import ab_evaluate_live as live  # noqa: E402
from ab_test_vrp_baseline import LOOKBACK_QUARTERS, match_events  # noqa: E402

from research.events import load_events  # noqa: E402

DRAWS = 400
BOOTSTRAP_N = 1000
SEED = 7
SIZES = (222, 300, 600, 1200, 2400, 4800)
TARGET_POWER = 0.80


def top_k_delta(vi: np.ndarray, vc: np.ndarray, crush: np.ndarray, pct: int = live.TOP_K_PCT) -> np.ndarray:
    """Top-pct% crush under close ranking minus under intraday, in points, per row.

    Rows are samples, columns events. The top m are the m largest VRPs; m is
    ab_evaluate_live.top_k_crush's round(n * pct / 100), at least 1.
    """
    n = vi.shape[-1]
    m = max(1, round(n * pct / 100))

    def rate(v: np.ndarray) -> np.ndarray:
        top = np.argpartition(-v, m - 1, axis=-1)[..., :m]
        return np.take_along_axis(crush, top, axis=-1).mean(axis=-1)

    return 100.0 * (rate(vc) - rate(vi))


def migrates(delta: float, p: float, holm_factor: int) -> bool:
    """ab_evaluate_live.vrp_decision's MIGRATE, past the sample-size floor."""
    return delta >= live.MIN_EFFECT_PTS and min(1.0, holm_factor * p) <= live.ALPHA_ONE_SIDED


def draw_decisions(vi, vc, crush, n, rng, draws=DRAWS, boots=BOOTSTRAP_N):
    """(delta, p) of the live test on `draws` samples of n events from the population."""
    out = []
    size = len(vi)
    for _ in range(draws):
        idx = rng.integers(0, size, n)
        svi, svc, scr = vi[idx], vc[idx], crush[idx]
        delta = float(top_k_delta(svi, svc, scr))
        b = rng.integers(0, n, (boots, n))
        diffs = top_k_delta(svi[b], svc[b], scr[b])
        out.append((delta, float(np.mean(diffs <= 0))))
    return out


def power(decisions, holm_factor: int) -> float:
    return sum(migrates(d, p, holm_factor) for d, p in decisions) / len(decisions)


def normal_power(effect: float, sd: float, alpha: float) -> float:
    """P(an estimate of `effect` with SD `sd` clears a one-sided level-alpha test)."""
    z = NormalDist().inv_cdf(1 - alpha)
    return 1 - NormalDist().cdf(z - effect / sd)


def resampled_sd(vi, vc, crush, n, rng, boots=4000) -> float:
    """SD of the top-K delta over samples of n drawn from these events."""
    idx = rng.integers(0, len(vi), (boots, n))
    return float(np.std(top_k_delta(vi[idx], vc[idx], crush[idx]), ddof=1))


def main() -> int:
    rng = np.random.default_rng(SEED)

    events = match_events(
        load_events(baseline="intraday", lookback=LOOKBACK_QUARTERS),
        load_events(baseline="close", lookback=LOOKBACK_QUARTERS),
    )
    u_vi = np.array([e["vrp_intraday"] for e in events])
    u_vc = np.array([e["vrp_close"] for e in events])
    u_cr = np.array([e["crushed"] for e in events], dtype=float)
    effect = float(top_k_delta(u_vi, u_vc, u_cr))

    known = live.load()["vrp"].graded
    l_vi = np.array([e["vi"] for e in known])
    l_vc = np.array([e["vc"] for e in known])
    l_cr = np.array([e["crush"] for e in known], dtype=float)

    alpha = live.ALPHA_ONE_SIDED
    print("VRP-denominator A/B: power of the live gate (descriptive; decides nothing)")
    print(f"gate: n >= {live.MIN_EVENTS}, top {live.TOP_K_PCT}% delta >= +{live.MIN_EFFECT_PTS} pts, "
          f"Holm-adjusted one-sided p <= {alpha}")
    print(f"universe: {len(events)} matched backtest events, top {live.TOP_K_PCT}% crush close minus "
          f"intraday = {effect:+.1f} pts (the effect assumed true)")
    print(f"live: {len(known)} graded reactions, base crush {100 * l_cr.mean():.1f}%, "
          f"observed delta {float(top_k_delta(l_vi, l_vc, l_cr)):+.1f} pts")
    print()
    print(f"1. universe model -- {DRAWS} draws x {BOOTSTRAP_N} bootstrap resamples, seed {SEED}")
    print(f"   {'n':>5}  {'P(MIGRATE) Holm 2p':>19}  {'P(MIGRATE) p alone':>19}  {'delta SD':>9}")
    for n in SIZES:
        decisions = draw_decisions(u_vi, u_vc, u_cr, n, rng)
        sd = float(np.std([d for d, _ in decisions], ddof=1))
        print(f"   {n:>5}  {power(decisions, 2):>18.1%}  {power(decisions, 1):>18.1%}  {sd:>8.2f}")
    print()
    print("2. live model -- delta SD from resampling graded live reactions; power at the universe")
    print("   effect by normal approximation (the +3-pt floor ignored, so these are ceilings)")
    print(f"   {'n':>5}  {'delta SD':>9}  {'power Holm 2p':>14}  {'power p alone':>14}")
    sd300 = None
    for n in SIZES:
        sd = resampled_sd(l_vi, l_vc, l_cr, n, rng)
        sd300 = sd if n == live.MIN_EVENTS else sd300
        print(f"   {n:>5}  {sd:>9.2f}  {normal_power(effect, sd, alpha / 2):>13.1%}  "
              f"{normal_power(effect, sd, alpha):>13.1%}")
    z = NormalDist().inv_cdf(1 - alpha / 2) + NormalDist().inv_cdf(TARGET_POWER)
    n_needed = live.MIN_EVENTS * (sd300 * z / effect) ** 2
    print(f"   {TARGET_POWER:.0%} power at {effect:+.1f} pts under live noise (Holm 2p) needs "
          f"~{n_needed:,.0f} graded reactions (SD scaled as 1/sqrt(n) from n={live.MIN_EVENTS})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
