"""Compound-risk recalibration on the new skew classifier (audit 4.5). Data-gated.

CLAUDE.md §6 quotes FULL compound risk at 44% crush / 9.7% average gap and
PARTIAL at 67% / 5.5%, from 15 bias_predictions outcomes in May 2026. Those rows
were read with ORATS r_slp_30 and the old same-strike skew classifier. The
classifier was replaced 2026-09-13 (25-delta risk reversal / ATM IV), so only
rows written after that day describe the signal the engine now uses. This
script re-measures the two groups on those rows and refuses to say anything
until each group is big enough to say it.

It changes no live threshold, whatever it prints. The 25-contract compound cap
(SizingContext.compound_risk_cap) is an engine rule; this measures the table
that justifies it.

DEFINITIONS -- the engine's, not re-derived
-------------------------------------------
Groups come from SizingContext itself (`classify`): NEITHER when
`compound_risk_active` is false; otherwise FULL when `bearish_fused_skew`, else
PARTIAL. That is CLAUDE.md's "FULL: bearish fused skew, or all 3 / PARTIAL: TRR
HIGH + alarm, skew not bearish". Inputs per row:
  - fused skew: bias_predictions.fused_bias (directional_bias when NULL, the
    analyzer's fallback);
  - sizing alarm: bias_predictions.sizing_alarm, the writer's Rule A/B flag,
    handed to SizingContext as an fcst reading at FCST_ERN_IV_THRESHOLD. While
    ORATS_ENABLED is false the alarm cannot fire, so PARTIAL cannot fill;
  - TRR: recomputed with tail_risk.trr_as_of from the gap moves strictly before
    the reaction, as rule_evidence does. The stored trr_level is not read: until
    2026-09-14 the writer computed it only after a VRP lookup that could never
    succeed (`calculate_vrp` does not exist), so it is NULL on every row from
    June 2026 to that fix, and recomputing reads every row the same way.

SAMPLE
------
bias_predictions rows with predicted_at at or after SAMPLE_START, each matched
to its historical_moves reaction by ab_evaluate_live.nearest_reaction. A row is
graded only if the prediction was taken before the reaction session's open
(ab_evaluate_live.is_pre_open) and a pre-open analysis_log row exists for the
same reaction. The implied move is that analysis row's, taking the one closest
in time to the prediction -- every writer takes its prediction beside its own
analysis_log row (trade.sh runs the prediction and analyze.py back to back; the
scan path logs its row, then predicts), so this pairs the two halves of one
run. One graded row per issuer reaction (the latest of the rows matched to it,
one per logged date or share class).

Until 2026-09-14 the table kept the LATEST prediction per (ticker, earnings_date),
so a re-run after the open overwrote the forecast with a post-open read, and
only trade.sh TICKER wrote one. Since then store_bias_prediction keeps the
first, refuses a write at or after the open by the same rule
(src/domain/reaction_session.py) and for an event not in earnings_calendar, and
every analysis path calls it -- scan, whisper, the bot and the digest too. The
test below is unchanged; only the rows reaching it are.

  crush    implied move > |close_move_pct|  (ab_evaluate_live's definition)
  avg gap  mean |gap_move_pct|              (CLAUDE.md's "Avg gap")

TEST AND GATE (fixed before any post-classifier row exists)
------------------------------------------------------------
Two comparisons, one Holm family at one-sided ALPHA_ONE_SIDED: FULL vs NEITHER
(PRIMARY) and PARTIAL vs NEITHER. Statistic: NEITHER crush minus group crush,
in points; stratified bootstrap, p = P(statistic <= 0), so p <= alpha is the
95% CI lower bound above zero. An unevaluable comparison keeps its seat at p = 1.

Minimum N: every group in a comparison needs MIN_N_PER_GROUP graded rows, below
which the script prints counts and "insufficient" and no rate at all. The floor
is a two-proportion power calculation (`required_n_per_group`): 80% power to
detect the May FULL figure, 44%, against a NEITHER rate of 70% at one-sided
alpha 0.0125 -- Holm's first step for two tests, which the PRIMARY faces if it is
the smaller p. The 70% is the live base crush rate measured by
ab_evaluate_live.py on 2026-09-14 (69.8%, n=222 graded reactions, the same crush
definition), not the ~80% universe backtest figure, which would have given 34
and an underpowered test on the population /analyze actually grades. Equal
group sizes are assumed, which errs toward more data.

Verdicts per comparison: INSUFFICIENT, LOWER (Holm p <= alpha: the group
crushes less than NEITHER), NOT SEPARABLE.

WHEN IT BECOMES RUNNABLE
------------------------
`forecast` takes the FORECAST_WEEKS before SAMPLE_START, counts rows that would
have been graded, and shares them out by group using the recomputed TRR, the
stored alarm and the stored fused skew. Those skew readings are the OLD
classifier's, so the group shares are a proxy. A group with no row in the window
is also projected at the rule-of-three 95% upper share, 3/n. The ceiling ignores
skew altogether -- the share of rows whose TRR and alarm leave room for the group
-- so it is the earliest date any classifier could reach the floor at the
window's logging rate. The forecast is description; no verdict reads it.

Usage:  ./venv/bin/python scripts/research/compound_risk_recalibration.py
Reads only. The target is the PG* environment (source 2.0/.env first).
"""

import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import NormalDist

sys.path.insert(0, str(Path(__file__).parent.parent))            # 2.0/scripts
from ab_evaluate_live import (  # noqa: E402
    ET,
    ci_and_p,
    holm,
    is_pre_open,
    issuer_of,
    nearest_reaction,
)

from common.constants import FCST_ERN_IV_THRESHOLD  # noqa: E402
from src.config.config import APIConfig  # noqa: E402
from src.domain.enums import DirectionalBias  # noqa: E402
from src.domain.tail_risk import trr_as_of, trr_level  # noqa: E402
from src.domain.types import SizingContext  # noqa: E402

# --- Pre-registered criteria. Fixed BEFORE the first run; do not tune. -------
SAMPLE_START = datetime(2026, 9, 14, tzinfo=ET)   # rows written after 2026-09-13
ALPHA_ONE_SIDED = 0.025
POWER = 0.80
P_FULL_PLANNING = 0.44       # CLAUDE.md §6 FULL crush, May 2026 -- the figure under test
P_NEITHER_PLANNING = 0.70    # live base crush, ab_evaluate_live.py 2026-09-14 (69.8%, n=222)
HOLM_TESTS = 2               # FULL vs NEITHER, PARTIAL vs NEITHER
BOOTSTRAP_N = 20000
SEED = 7
FORECAST_WEEKS = 13

FULL, PARTIAL, NEITHER = "FULL", "PARTIAL", "NEITHER"
GROUPS = (FULL, PARTIAL, NEITHER)


def required_n_per_group(p1: float, p2: float, alpha: float, power: float) -> int:
    """Per-group n for a one-sided two-proportion z-test, equal groups.

    n = (z_a * sqrt(2 pbar qbar) + z_b * sqrt(p1 q1 + p2 q2))^2 / (p1 - p2)^2
    """
    if p1 == p2:
        raise ValueError("no sample size detects a zero difference")
    z_a = NormalDist().inv_cdf(1 - alpha)
    z_b = NormalDist().inv_cdf(power)
    pbar = (p1 + p2) / 2
    num = (z_a * math.sqrt(2 * pbar * (1 - pbar))
           + z_b * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    return math.ceil(num / (p1 - p2) ** 2)


MIN_N_PER_GROUP = required_n_per_group(
    P_FULL_PLANNING, P_NEITHER_PLANNING, ALPHA_ONE_SIDED / HOLM_TESTS, POWER)


def classify(trr: str | None, sizing_alarm: bool | None, fused_bias: str | None) -> str:
    """FULL / PARTIAL / NEITHER, decided by SizingContext's own properties."""
    ctx = SizingContext(
        trr_level=trr,
        fcst_ern_iv_effect=FCST_ERN_IV_THRESHOLD if sizing_alarm else None,
        fused_bias=DirectionalBias(fused_bias) if fused_bias else None,
    )
    if not ctx.compound_risk_active:
        return NEITHER
    return FULL if ctx.bearish_fused_skew else PARTIAL


# --- sample -----------------------------------------------------------------------

def build_sample(predictions, reactions, analyses):
    """Grade predictions against reactions. Pure; see SAMPLE in the docstring.

    predictions: dicts with ticker, earnings_date, predicted_at (tz-aware),
                 fused_bias, directional_bias, sizing_alarm.
    reactions:   {ticker: [dicts with earnings_date, gap, cm, timing]}.
    analyses:    {ticker: [dicts with timestamp (tz-aware), earnings_date, impl]}.
    """
    graded, pending, post_open, no_implied = {}, [], [], []
    duplicates = 0
    for p in sorted(predictions, key=lambda r: r["predicted_at"]):
        rows = reactions.get(p["ticker"], [])
        dates = [r["earnings_date"] for r in rows]
        d, _ = nearest_reaction(p["earnings_date"], dates)
        if d is None:
            pending.append(p)
            continue
        r = next(r for r in rows if r["earnings_date"] == d)
        if not is_pre_open(p["predicted_at"], d, r["timing"]):
            post_open.append(p)
            continue
        candidates = [
            a for a in analyses.get(p["ticker"], [])
            if nearest_reaction(a["earnings_date"], dates)[0] == d
            and is_pre_open(a["timestamp"], d, r["timing"])
        ]
        if not candidates:
            no_implied.append(p)
            continue
        a = min(candidates, key=lambda a: abs((a["timestamp"] - p["predicted_at"]).total_seconds()))
        trr = trr_level(trr_as_of([(h["earnings_date"], h["gap"]) for h in rows], before=d))
        key = (issuer_of(p["ticker"]), d)
        duplicates += key in graded
        graded[key] = {        # sorted by predicted_at, so the latest prediction wins
            "ticker": p["ticker"], "reaction_date": d, "predicted_at": p["predicted_at"],
            "impl": a["impl"], "trr_level": trr,
            "fused_bias": p["fused_bias"] or p["directional_bias"],
            "sizing_alarm": bool(p["sizing_alarm"]),
            "group": classify(trr, p["sizing_alarm"], p["fused_bias"] or p["directional_bias"]),
            "crush": 1 if a["impl"] > abs(r["cm"]) else 0,
            "gap": abs(r["gap"]),
        }
    return {"graded": list(graded.values()), "pending": pending, "post_open": post_open,
            "no_implied": no_implied, "duplicates": duplicates}


# --- test ---------------------------------------------------------------------------

def gap_bootstrap(group, neither, n_boot=BOOTSTRAP_N, seed=SEED):
    """NEITHER crush minus group crush, resampling within each group."""
    rng = random.Random(seed)
    g = [e["crush"] for e in group]
    n = [e["crush"] for e in neither]
    diffs = []
    for _ in range(n_boot):
        gs = sum(g[rng.randrange(len(g))] for _ in g) / len(g)
        ns = sum(n[rng.randrange(len(n))] for _ in n) / len(n)
        diffs.append(ns - gs)
    return diffs


def describe(events):
    """Counts always; rates only at or above MIN_N_PER_GROUP."""
    out = {"n": len(events), "sufficient": len(events) >= MIN_N_PER_GROUP}
    if out["sufficient"]:
        out["crush_pct"] = 100 * sum(e["crush"] for e in events) / len(events)
        out["avg_gap_pct"] = sum(e["gap"] for e in events) / len(events)
    return out


def verdict(graded, n_boot=BOOTSTRAP_N, seed=SEED):
    by = {g: [e for e in graded if e["group"] == g] for g in GROUPS}
    groups = {g: describe(by[g]) for g in GROUPS}
    tests = {}
    for g in (FULL, PARTIAL):
        if groups[g]["sufficient"] and groups[NEITHER]["sufficient"]:
            lo, hi, p = ci_and_p(gap_bootstrap(by[g], by[NEITHER], n_boot, seed))
            tests[g] = {"gap_pts": groups[NEITHER]["crush_pct"] - groups[g]["crush_pct"],
                        "lo": 100 * lo, "hi": 100 * hi, "p": p}
        else:
            tests[g] = None
    adj = holm({g: (t["p"] if t else None) for g, t in tests.items()})
    for g, t in tests.items():
        if t is None:
            tests[g] = {"decision": "INSUFFICIENT"}
        else:
            t["p_holm"] = adj[g]
            t["decision"] = "LOWER" if adj[g] <= ALPHA_ONE_SIDED else "NOT SEPARABLE"
    return {"groups": groups, "tests": tests}


# --- forecast -------------------------------------------------------------------------

def _could_be(group: str, e: dict) -> bool:
    """Whether a row's non-skew flags leave room for the group, whatever the skew.

    FULL needs bearish skew plus TRR HIGH or the alarm; PARTIAL needs both. So
    the share of rows passing this is a ceiling on the group's share that no
    classifier change can lift -- the forecast's floor on the date.
    """
    high, alarm = e["trr_level"] == "HIGH", e["sizing_alarm"]
    if group == FULL:
        return high or alarm
    if group == PARTIAL:
        return high and alarm
    return True


def forecast(window_graded, weeks=FORECAST_WEEKS, start=SAMPLE_START, min_n=MIN_N_PER_GROUP):
    """Weeks until each group reaches min_n at the window's rate. See docstring.

    Three shares per group: observed (old-classifier skew, a proxy); the
    rule-of-three 95% upper share when none was observed; and the ceiling
    (`_could_be`), the earliest any skew classifier could fill it.
    """
    total = len(window_graded)
    per_week = total / weeks
    out = {"graded": total, "per_week": per_week, "groups": {}}

    def eta(s):
        rate = per_week * s
        if rate <= 0:
            return None, None
        w = min_n / rate
        return w, (start + timedelta(weeks=w)).date()

    for g in GROUPS:
        k = sum(1 for e in window_graded if e["group"] == g)
        share = k / total if total else 0.0
        upper = share if k else (3 / total if total else 0.0)
        ceiling = sum(1 for e in window_graded if _could_be(g, e)) / total if total else 0.0
        out["groups"][g] = {"count": k, "share": share, "upper_share": upper,
                            "ceiling_share": ceiling, "observed": eta(share),
                            "upper": eta(upper), "ceiling": eta(ceiling)}
    return out


# --- database ---------------------------------------------------------------------------

PREDICTION_QUERY = """
SELECT ticker, earnings_date, predicted_at, fused_bias, directional_bias, sizing_alarm
FROM bias_predictions
WHERE predicted_at >= %s
ORDER BY predicted_at, id
"""

REACTION_QUERY = """
SELECT h.ticker, h.earnings_date, h.gap_move_pct gap, h.close_move_pct cm, ec.timing
FROM historical_moves h
LEFT JOIN earnings_calendar ec ON ec.ticker = h.ticker AND ec.earnings_date = h.earnings_date
WHERE h.ticker = ANY(%s)
ORDER BY h.ticker, h.earnings_date
"""

ANALYSIS_QUERY = """
SELECT ticker, "timestamp", earnings_date, implied_move_pct impl
FROM analysis_log
WHERE ticker = ANY(%s) AND "timestamp" >= %s
ORDER BY "timestamp", id
"""


def load(conn):
    window_start = SAMPLE_START - timedelta(weeks=FORECAST_WEEKS)
    preds = [r._asdict() for r in conn.execute(PREDICTION_QUERY, (window_start,))]
    tickers = sorted({p["ticker"] for p in preds})
    reactions, analyses = defaultdict(list), defaultdict(list)
    for r in conn.execute(REACTION_QUERY, (tickers,)):
        reactions[r.ticker].append(r._asdict())
    # an analysis can precede its prediction by a few minutes, never by a quarter
    for a in conn.execute(ANALYSIS_QUERY, (tickers, window_start - timedelta(days=7))):
        analyses[a.ticker].append(a._asdict())
    sample = [p for p in preds if p["predicted_at"] >= SAMPLE_START]
    window = [p for p in preds if p["predicted_at"] < SAMPLE_START]
    return sample, window, dict(reactions), dict(analyses)


def main():
    from common.db import connect, target
    conn = connect()
    try:
        sample_rows, window_rows, reactions, analyses = load(conn)
    finally:
        conn.close()
    s = build_sample(sample_rows, reactions, analyses)
    v = verdict(s["graded"])
    w = build_sample(window_rows, reactions, analyses)
    f = forecast(w["graded"])
    orats_enabled = APIConfig.from_env().orats_enabled

    print("=" * 70)
    print("COMPOUND-RISK RECALIBRATION (audit 4.5) -- new skew classifier rows only")
    print("=" * 70)
    print(f"db: {target()}")
    print(f"pre-registered: predicted_at >= {SAMPLE_START:%Y-%m-%d %H:%M %Z}; "
          f"MIN_N_PER_GROUP = {MIN_N_PER_GROUP}")
    print(f"  = power {POWER:.2f} to detect {P_FULL_PLANNING:.0%} vs {P_NEITHER_PLANNING:.0%} crush, "
          f"one-sided alpha {ALPHA_ONE_SIDED / HOLM_TESTS} (Holm step 1 of {HOLM_TESTS})")
    print(f"  (at the ~80% universe baseline it would be "
          f"{required_n_per_group(P_FULL_PLANNING, 0.80, ALPHA_ONE_SIDED / HOLM_TESTS, POWER)})")
    print(f"\nrows: {len(sample_rows)}  graded: {len(s['graded'])}  pending: {len(s['pending'])}  "
          f"post-open: {len(s['post_open'])}  no pre-open analysis: {len(s['no_implied'])}  "
          f"duplicate reactions: {s['duplicates']}")
    print(f"\n  {'group':8s} {'n':>4s}  status")
    for g in GROUPS:
        d = v["groups"][g]
        if d["sufficient"]:
            status = f"crush {d['crush_pct']:.1f}%  avg gap {d['avg_gap_pct']:.2f}%"
        else:
            status = f"insufficient (need {MIN_N_PER_GROUP})"
        if g == PARTIAL and not orats_enabled:
            status += " -- cannot fill while ORATS_ENABLED=false: the sizing alarm never fires"
        print(f"  {g:8s} {d['n']:4d}  {status}")
    print()
    for g, role in ((FULL, "PRIMARY"), (PARTIAL, "SECONDARY")):
        t = v["tests"][g]
        if t["decision"] == "INSUFFICIENT":
            print(f"{role} {g} vs NEITHER: INSUFFICIENT -- no verdict below {MIN_N_PER_GROUP} per group")
        else:
            print(f"{role} {g} vs NEITHER: {t['decision']}  gap {t['gap_pts']:+.1f} pts "
                  f"(NEITHER - {g}), 95% CI [{t['lo']:+.1f}, {t['hi']:+.1f}], "
                  f"p={t['p']:.3f}, Holm p={t['p_holm']:.3f}")

    print(f"\n--- when this becomes runnable (window: {FORECAST_WEEKS} weeks before "
          f"{SAMPLE_START:%Y-%m-%d}) ---")
    print(f"rows written: {len(window_rows)}  gradable: {f['graded']} "
          f"({f['per_week']:.2f}/week)  [skew shares use the OLD classifier: a proxy]")
    for g in GROUPS:
        d = f["groups"][g]
        obs_w, obs_date = d["observed"]
        up_w, up_date = d["upper"]
        obs = f"{obs_w:.0f} weeks -> {obs_date}" if obs_w else "never at the observed share"
        line = f"  {g:8s} {d['count']:3d} of {f['graded']} ({d['share']:.1%}): {obs}"
        if d["count"] == 0 and up_w:
            line += f"; at the 95% upper share {d['upper_share']:.1%}: {up_w:.0f} weeks -> {up_date}"
        print(line)
        if g != NEITHER:
            ceil_w, ceil_date = d["ceiling"]
            need = "TRR HIGH or alarm" if g == FULL else "TRR HIGH and alarm"
            when = f"{ceil_w:.0f} weeks -> {ceil_date}" if ceil_w else "never"
            print(f"  {'':8s} ceiling, any classifier ({need}: {d['ceiling_share']:.1%}): {when}")
    print("\nNothing here changes a live threshold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
