#!/usr/bin/env python3
"""
Live forward-validation evaluator for the two logged A/B experiments
(VRP denominator convention, IV term-structure slope). Backs `/ab-status`.

WHY THIS EXISTS AS A SCRIPT
---------------------------
`/ab-status`'s original Step 3 criterion was structurally invalid and had to be
replaced (Sep 4 2026). It compared the two VRP conventions by counting events
where they "disagree on tradeability" at the SAME numeric threshold (both >=1.4,
or close >=1.8 after a "recalibration"). But close-baseline VRP is mechanically
smaller than intraday VRP -- it divides by a bigger denominator, since the
close-to-close baseline includes the overnight gap the intraday baseline drops.
Measured over the live sample: median intraday 2.28 vs close 1.26, and the close
baseline runs a median 1.90x the intraday baseline.

So a same-threshold comparison compares UNITS, not conventions. The tell in the
Sep 4 2026 run: 100% of "disagreements" ran one direction (101 of 101 at >=1.4,
149 of 149 at >=1.8, zero the other way). The old rule scored the close
convention at 32-34% "correct" and would have retired it -- for a units bug, not
an effect. The old "recalibrated tiers: close GOOD >=1.8, EXCELLENT >=2.5" line
made it worse: it moved close's threshold UP when the mechanical shift is DOWN,
selecting 48 of 232 events that then crushed at 66.7% vs a 64.2% base rate.

The valid comparison is rank-based at matched selectivity -- which is what
`ab_test_vrp_baseline.py` (the universe backtest this A/B exists to confirm)
already did: top-20% crush 87.7% -> 90.9% at a 12-quarter lookback, 2026-09-13.
The Jun 2026 headline 81.2% -> 84.9% came from an 8-quarter version since retired.
This script implements that test, so the criterion lives in code with its
rationale instead of as prose that can drift.

CRUSH DEFINITION: `implied_move_pct > ABS(close_move_pct)` -- gap-inclusive, the
economically correct target (a spread held through earnings eats the gap). Note
this shares a measurement basis with close-VRP and therefore FAVORS the close
convention; treat a close-convention win as an upper bound on its true edge.

SAMPLE HYGIENE (rev. 2026-09-13)
--------------------------------
A study of the sample this script graded found three defects. None changes a
pre-registered threshold; each changes which rows reach the tests.

1. **The FIRST pre-open analysis is graded, not the latest.** The evaluator used
   to take each event's latest analysis. For 63 of 227 reactions that log was
   taken after the reaction-day open by the calendar's timing tags, when the
   implied move has already collapsed: median implied move 4.22% against 8.43%,
   crush 55.6% against 68.9%. Those rows measure the post-event chain, not the
   forecast, and they sat in both A/Bs. Now: the first analysis taken strictly
   before the open of the reaction session (`reaction_open_cutoff`). First, not
   last before the open, because the first log is the one no later look could
   have chosen. A reaction with no such analysis is reported and not graded. Each test takes
   the first pre-open analysis that logged its own variable (`build_samples`):
   scan rows logged no term slope until Sep 4 2026, and "first row of any kind"
   would have cut the term sample from 56 reactions to 18 for that reason alone.
2. **One graded row per issuer reaction.** The 30-day nearest-date join is right
   for a corrected calendar date, but it matched a stale date to the same
   `historical_moves` reaction a second time: 15 of 242 rows repeated a reaction
   already counted (10 tickers logged under both 07-28 and 08-05; ISRG, CDNS,
   PYPL and KR under two dates; GOOG beside GOOGL). The unit is now (issuer,
   reaction date). Share
   classes of one issuer are one reaction: their outcomes are the same news on the
   same day, and counting both double-weights it (`SHARE_CLASS_ISSUER`).
3. **One primary verdict, Holm across the family.** Five top-K cuts and two
   verdicts used one alpha with no correction. The VRP top-20% test is the
   PRIMARY verdict, as pre-registered; the term-structure test is SECONDARY. Both
   gate on a Holm-adjusted p-value (see `holm`). The other top-K cuts, the
   quintile tables and the point-biserial t are descriptive and test nothing.

Usage:  ./venv/bin/python scripts/ab_evaluate_live.py [--full]

No --db flag, and no path of any kind: the target comes from the PG*
environment, so `PGDATABASE=ivcrush_scratch ...` reaches a scratch copy and
the default reaches production. Source 2.0/.env first.

⚠️ **The venv IS required now.** This used to be stdlib-only (sqlite3) so the
quarterly routine could run it with a bare python3 against a database copy
pulled from GCS. It reads Postgres through psycopg via common.db, so a bare
python3 fails on the import. The maths is still stdlib (math/random/
statistics) -- no numpy.
"""
import argparse
import math
import random
import statistics as st
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from common.db import connect  # noqa: E402
from common.db import target as db_target
from src.domain.enums import Recommendation  # noqa: E402

# The pre-open rule lives in the domain so the bias-prediction writer refuses
# exactly the rows this grader would drop; re-exported for the research scripts.
from src.domain.reaction_session import (  # noqa: E402,F401
    ET,
    JOIN_DAYS,
    MARKET_OPEN,
    is_pre_open,
    nearest_reaction,
    reaction_open_cutoff,
)
from src.domain.vrp_tier import vrp_tier  # noqa: E402

# The ladder in force when this A/B was registered. Every tier this script reads is
# an INTRADAY ratio (vi), so it grades on this ladder, never the live close-to-close one.
INTRADAY_LADDER = {"excellent": 1.8, "good": 1.4, "marginal": 1.2}
# Convention migrated on the universe backtest and the structural case, not by this
# test, whose gate has ~2-7% power at 300 (research/ab_vrp_power.py). vrp_decision and
# the Holm family stay as registered and keep printing.
CONVENTION_MIGRATED = "2026-09 (docs/superpowers/plans/2026-09-16-vrp-close-denominator.md)"

# --- Pre-registered decision criteria (rev. Sep 4 2026) ---------------------
TOP_K_PCT = 20          # selectivity the Jun 2026 universe backtest reported
MIN_EVENTS = 300        # sample floor before the test is allowed to decide
MIN_EFFECT_PTS = 3.0    # backtest measured +3.7 pts OOS; require at least +3
BOOTSTRAP_N = 20000
SEED = 7
TERM_MIN_PER_BUCKET = 8
TERM_MIN_GAP_PTS = 10.0
# Term-structure split, revised Sep 5 2026. The original rule tested
# `slope < 1.10` among VRP>=1.8 events and could not be evaluated: the flat
# bucket held 0 of 37. The first diagnosis (widen the VRP band) was WRONG --
# measured across the live sample, dropping the band to VRP>=1.2 gains exactly
# ONE flat event. The binding constraint is the THRESHOLD, not the band:
# observed slope runs 1.04-2.48 (median 1.58) with only 2% below 1.10, because
# pre-earnings names are backwardated by construction. An absolute cutoff below
# the distribution's floor has no power no matter how much data accumulates.
#
# So the split is now distribution-relative -- bottom tercile vs the rest --
# mirroring the rank-based approach the VRP test uses for the same reason.
# The economic hypothesis is unchanged and still pre-registered: a FLATTER term
# structure means the elevated IV is persistent risk rather than event risk, so
# it should crush LESS. Only the instrument changed.
#
# This is not threshold-shopping: the direction was fixed in advance, the split
# is chosen by data volume rather than by outcome, and the gates below (bucket
# size, effect size, bootstrap CI) are the same bar the VRP test must clear. At
# the time of the change the tercile split gave +14.3 pts in the hypothesised
# direction with CI [-21.4, +42.9] -- i.e. NOT significant, so the design was
# not selected for giving a favourable answer.
#
# If this ever passes, the deployed sizing threshold must be re-derived from the
# live distribution (the split point this prints), NOT set at 1.10.
TERM_SPLIT_QUANTILE = 1.0 / 3.0

# Separability, rev. 2026-09-13. Both tests were pre-registered as "bootstrap
# 95% CI lower bound > 0". For a percentile bootstrap that is exactly
# P(effect <= 0) <= 0.025 (`ci_and_p` and its test), so this constant is the
# alpha the criterion always had, written down -- not a new threshold. What is
# new is that the two verdicts now share it as one family: each gates on its
# Holm-adjusted p (`holm`), not its raw p.
ALPHA_ONE_SIDED = 0.025

# Date-join tolerance, JOIN_DAYS (30): src/domain/reaction_session.py, beside the
# pre-open rule, so the bias validator matches a prediction to the reaction this
# evaluator would. A wide window can match a stale date to a reaction another date
# already matched; `select_events` collapses those to one graded row per reaction.

# Only rows since the Jun 2026 re-backfill: earlier vrp_intraday_ratio values are
# inflated for mixed-sign tickers, and the close/term columns did not exist.
# Compared as a UTC date, which is what the production session's TimeZone
# always made of `timestamp::date`; spelled out so no session setting moves it.
SAMPLE_START = "2026-06-10"

# Share classes of one issuer, graded as one reaction. Only pairs present in the
# sample belong here, each checked against the listing exchange: Alphabet Class
# C (GOOG) and Class A (GOOGL), both Nasdaq. Keyed by the class that folds into
# the other.
SHARE_CLASS_ISSUER = {"GOOG": "GOOGL"}

# Diagnostics only -- no verdict reads these. The engine's history window and
# floor (prices_repository.get_historical_moves limit=12,
# min_historical_quarters=4); the "logged >10% above recompute" line; and the
# implied-move collapse that marks a timing tag the option chain contradicts.
PIT_LOOKBACK_QUARTERS = 12
PIT_MIN_QUARTERS = 4
DIAG_INFLATION = 1.10
COLLAPSE_RATIO = 0.6

ANALYSIS_QUERY = f"""
SELECT a.id, a.ticker, a."timestamp", a.earnings_date, a.expiration,
       a.implied_move_pct impl, a.vrp_intraday_ratio vi, a.vrp_close_ratio vc,
       a.term_slope_ratio ts, ec.timing analysis_timing
FROM analysis_log a
LEFT JOIN earnings_calendar ec
  ON ec.ticker = a.ticker AND ec.earnings_date = a.earnings_date
WHERE a.vrp_close_ratio IS NOT NULL
  AND a.vrp_intraday_ratio IS NOT NULL
  AND (a."timestamp" AT TIME ZONE 'UTC')::date >= DATE '{SAMPLE_START}'
ORDER BY a."timestamp", a.id
"""

# Every reaction for the sampled tickers, not just the matched ones: the
# point-in-time diagnostic needs each ticker's earlier quarters too.
REACTION_QUERY = """
SELECT h.ticker, h.earnings_date, h.close_move_pct cm, h.intraday_move_pct im,
       ec.timing
FROM historical_moves h
LEFT JOIN earnings_calendar ec
  ON ec.ticker = h.ticker AND ec.earnings_date = h.earnings_date
WHERE h.ticker = ANY(%s)
ORDER BY h.ticker, h.earnings_date
"""


# --- sample selection ---------------------------------------------------------
# nearest_reaction / reaction_open_cutoff / is_pre_open: src/domain/reaction_session.py

def issuer_of(ticker):
    return SHARE_CLASS_ISSUER.get(ticker, ticker)


@dataclass
class Selection:
    graded: list = field(default_factory=list)       # one per issuer reaction
    pending: list = field(default_factory=list)      # no reaction row yet
    no_pre_open: list = field(default_factory=list)  # reactions nothing forecast
    stats: dict = field(default_factory=dict)
    reactions: dict = field(default_factory=dict)    # every loaded move, by ticker
    groups: dict = field(default_factory=dict)       # (issuer, date) -> [(analysis, reaction)]


def _event(a, r):
    e = {"ticker": a["ticker"], "issuer": issuer_of(a["ticker"]), "analysis_id": a["id"],
         "analysed_at": a["timestamp"], "logged_date": a["earnings_date"],
         "expiration": a.get("expiration"),
         "impl": a["impl"], "vi": a["vi"], "vc": a["vc"], "ts": a["ts"]}
    if r is None:
        e.update(earnings_date=a["earnings_date"], timing=a["analysis_timing"], cm=None)
    else:
        e.update(earnings_date=r["earnings_date"], timing=r["timing"], cm=r["cm"],
                 crush=1 if a["impl"] > abs(r["cm"]) else 0)
    return e


def select_events(analyses, reactions, join_days=JOIN_DAYS):
    """Reduce analysis_log rows to the graded sample. Pure; see module docstring.

    analyses:  dicts with id, ticker, timestamp (tz-aware), earnings_date (the
               logged date), impl, vi, vc, ts, analysis_timing.
    reactions: {ticker: [dicts with earnings_date, cm, im, timing]}.
    """
    by_key = {(t, r["earnings_date"]): r for t, rows in reactions.items() for r in rows}
    dates = {t: sorted(r["earnings_date"] for r in rows) for t, rows in reactions.items()}

    match, ties = {}, 0
    for a in analyses:
        key = (a["ticker"], a["earnings_date"])
        if key not in match:
            d, tie = nearest_reaction(a["earnings_date"], dates.get(a["ticker"], []), join_days)
            match[key] = d
            ties += tie

    groups, pending_groups = defaultdict(list), defaultdict(list)
    for a in analyses:
        d = match[(a["ticker"], a["earnings_date"])]
        if d is None:
            pending_groups[(issuer_of(a["ticker"]), a["earnings_date"])].append(a)
        else:
            groups[(issuer_of(a["ticker"]), d)].append((a, by_key[(a["ticker"], d)]))

    def order(pair):
        return (pair[0]["timestamp"], pair[0]["id"])

    sel = Selection(reactions=reactions, groups=dict(groups))
    post_open_rows = matched_rows = latest_post_open = 0
    share_class_pairs = []
    for (_issuer, d), pairs in sorted(groups.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        pairs.sort(key=order)
        matched_rows += len(pairs)
        pre = [(a, r) for a, r in pairs if is_pre_open(a["timestamp"], r["earnings_date"], r["timing"])]
        post_open_rows += len(pairs) - len(pre)
        a_last, r_last = pairs[-1]
        latest_post_open += not is_pre_open(a_last["timestamp"], r_last["earnings_date"], r_last["timing"])
        classes = sorted({a["ticker"] for a, _ in pairs})
        if len(classes) > 1:
            share_class_pairs.append(("+".join(classes), d))
        if pre:
            sel.graded.append(_event(*pre[0]))
        else:
            shown = _event(*pairs[0])
            del shown["crush"]            # not graded: nothing here may be scored
            sel.no_pre_open.append(shown)

    # No reaction to anchor a pending event on, so logged dates of one issuer
    # within join_days of each other are one report (quarters are ~90 days apart).
    pending_clusters = defaultdict(list)
    for issuer in sorted({i for i, _ in pending_groups}):
        anchor = prev = None
        for d in sorted(d for i, d in pending_groups if i == issuer):
            if prev is None or (d - prev).days > join_days:
                anchor = d
            pending_clusters[(issuer, anchor)].extend(pending_groups[(issuer, d)])
            prev = d
    for _cluster, rows in sorted(pending_clusters.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        rows.sort(key=lambda a: (a["timestamp"], a["id"]))
        pre = [a for a in rows if is_pre_open(a["timestamp"], a["earnings_date"], a["analysis_timing"])]
        sel.pending.append(_event((pre or rows)[0], None))

    matched_logged = {k for k, d in match.items() if d is not None}
    ticker_reactions = {(t, d) for (t, _), d in match.items() if d is not None}
    sel.stats = {
        "analysis_rows": len(analyses),
        "logged_events": len(match),
        "matched_events": len(matched_logged),
        "stale_date_duplicates": len(matched_logged) - len(ticker_reactions),
        "share_class_duplicates": len(ticker_reactions) - len(groups),
        "share_class_pairs": share_class_pairs,
        "reactions": len(groups),
        "no_pre_open": len(sel.no_pre_open),
        "graded": len(sel.graded),
        "pending": len(sel.pending),
        "pending_duplicates": len(pending_groups) - len(sel.pending),
        "matched_rows": matched_rows,
        "post_open_rows": post_open_rows,
        "latest_post_open": latest_post_open,
        "ties": ties,
        "graded_timing": dict(Counter(e["timing"] or "untagged" for e in sel.graded)),
    }
    return sel


def load_rows(conn):
    analyses = [r._asdict() for r in conn.execute(ANALYSIS_QUERY)]
    tickers = sorted({a["ticker"] for a in analyses})
    reactions = defaultdict(list)
    for r in conn.execute(REACTION_QUERY, (tickers,)):
        reactions[r.ticker].append({"earnings_date": r.earnings_date, "cm": r.cm, "im": r.im,
                                    "timing": r.timing})
    return analyses, dict(reactions)


def build_samples(analyses, reactions):
    """One sample per test: the first pre-open analysis that logged its variable.

    Every loaded row carries close VRP (the query requires it), so the PRIMARY
    sample is simply the first pre-open analysis. The SECONDARY sample is the
    first pre-open analysis carrying term_slope_ratio: scan/whisper rows logged no
    slope until Sep 4 2026 (scripts/scan/workflows/ticker_analysis.py), so on
    2026-09-13 the first pre-open log carried one for only 18 reactions while 56
    had a slope logged before the open. Which code path wrote the first row is
    unrelated to the hypothesis, and neither rule looks at the outcome. Slopes
    before Sep 4 still come only from /analyze runs, the selection bias that
    commit c7e859d stopped for new rows. The two samples can grade different rows
    (different implied moves) for one reaction; each test is internally consistent.
    """
    return {"vrp": select_events(analyses, reactions),
            "term": select_events([a for a in analyses if a["ts"] is not None], reactions)}


def load():
    conn = connect()
    try:
        analyses, reactions = load_rows(conn)
    finally:
        conn.close()
    return build_samples(analyses, reactions)


# --- statistics -----------------------------------------------------------------

def top_k_crush(events, key, pct=TOP_K_PCT):
    m = max(1, round(len(events) * pct / 100))
    top = sorted(events, key=lambda e: -e[key])[:m]
    return sum(e["crush"] for e in top) / m, m


def ci_and_p(diffs):
    """Percentile 95% CI and one-sided P(effect <= 0) from bootstrap differences.

    `lo > 0` holds exactly when `p <= 0.025`: lo is the value at index
    floor(0.025 L) of the sorted list, so it is positive iff at most that many
    differences are <= 0.
    """
    diffs = sorted(diffs)
    lo = diffs[int(0.025 * len(diffs))]
    hi = diffs[int(0.975 * len(diffs))]
    return lo, hi, sum(1 for d in diffs if d <= 0) / len(diffs)


def bootstrap_delta(known, pct=TOP_K_PCT, n_boot=BOOTSTRAP_N, seed=SEED):
    """95% CI on (close top-K crush - intraday top-K crush), resampling events."""
    rng = random.Random(seed)
    n = len(known)
    m = max(1, round(n * pct / 100))
    diffs = []
    for _ in range(n_boot):
        s = [known[rng.randrange(n)] for _ in range(n)]
        ti = sorted(s, key=lambda e: -e["vi"])[:m]
        tc = sorted(s, key=lambda e: -e["vc"])[:m]
        diffs.append(sum(e["crush"] for e in tc) / m - sum(e["crush"] for e in ti) / m)
    return ci_and_p(diffs)


def holm(pvalues):
    """Holm step-down adjusted p-values for one family of tests.

    Sorted ascending, the i-th smallest p (0-based) is multiplied by (m - i),
    clipped at 1, and carried forward so adjusted values never decrease. A test
    that could not be evaluated keeps its seat in the family at p = 1: the family
    was fixed when the tests were pre-registered, not by which one had data.
    """
    items = sorted((1.0 if p is None else p, name) for name, p in pvalues.items())
    m, running, out = len(items), 0.0, {}
    for i, (p, name) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        out[name] = running
    return out


def equal_selectivity_threshold(known, intraday_thr=INTRADAY_LADDER["good"]):
    """Close-VRP cutoff selecting the same count intraday>=thr selects.

    This -- not a hand-picked number -- is how close-VRP tiers must be derived
    if the convention is ever migrated. The old command file's "close GOOD>=1.8"
    moved the cutoff the wrong way entirely.
    """
    n_sel = sum(1 for e in known if e["vi"] >= intraday_thr)
    if not n_sel:
        return None
    desc = sorted((e["vc"] for e in known), reverse=True)
    return desc[min(n_sel, len(desc)) - 1]


def point_biserial(known, key):
    x = [e[key] for e in known]
    y = [e["crush"] for e in known]
    mx, my = st.mean(x), st.mean(y)
    num = sum((a - mx) * (b - my) for a, b in zip(x, y, strict=True))
    den = math.sqrt(sum((a - mx) ** 2 for a in x) * sum((b - my) ** 2 for b in y))
    if den == 0:
        return 0.0, 0.0
    r = num / den
    t = r * math.sqrt(len(known) - 2) / math.sqrt(1 - r * r) if abs(r) < 1 else float("inf")
    return r, t


def quintiles(known, key):
    s = sorted(known, key=lambda e: e[key])
    n = len(s)
    out = []
    for i in range(5):
        b = s[i * n // 5:(i + 1) * n // 5]
        if b:
            out.append((b[0][key], b[-1][key], len(b),
                        100 * sum(e["crush"] for e in b) / len(b)))
    return out


def vrp_stats(known):
    """PRIMARY test statistics: rank-based top-K head-to-head."""
    ci, m = top_k_crush(known, "vi")
    cc, _ = top_k_crush(known, "vc")
    lo, hi, p_le0 = bootstrap_delta(known)
    return {"top_n": m, "intraday": 100 * ci, "close": 100 * cc, "delta": 100 * (cc - ci),
            "lo": 100 * lo, "hi": 100 * hi, "p_le0": p_le0}


def vrp_decision(d, n, p_adj):
    enough = n >= MIN_EVENTS
    significant = p_adj <= ALPHA_ONE_SIDED
    big_enough = d["delta"] >= MIN_EFFECT_PTS
    if enough and significant and big_enough:
        return "MIGRATE"
    if enough and significant:
        return "KEEP INTRADAY (effect real but below the +3pt bar)"
    if not enough:
        return f"INSUFFICIENT DATA ({n}/{MIN_EVENTS} events)"
    return "KEEP INTRADAY (effect not separable from zero after Holm)"


def term_bands(known):
    """flat/backwardation split by VRP band — diagnostic, not the decision.

    The promotion rule tests VRP>=1.8 only (that is where a sizing flag would
    actually apply), but the flat bucket's base rate is what determines how
    long it takes to fill, so report the wider picture too.
    """
    out = []
    for tier in (Recommendation.SKIP, Recommendation.MARGINAL,
                 Recommendation.GOOD, Recommendation.EXCELLENT):
        b = [e for e in known if e["ts"] is not None
             and vrp_tier(e["vi"], **INTRADAY_LADDER) is tier]
        flat = [e for e in b if e["ts"] < 1.10]
        out.append((tier.name, len(b), len(flat)))
    return out


def _rate(b):
    return 100 * sum(e["crush"] for e in b) / len(b) if b else None


def term_bootstrap(sub, cut, n_boot=BOOTSTRAP_N, seed=SEED):
    """95% CI on (rest crush - bottom-tercile crush), resampling events."""
    rng = random.Random(seed)
    n = len(sub)
    diffs = []
    for _ in range(n_boot):
        samp = [sub[rng.randrange(n)] for _ in range(n)]
        ss = sorted(samp, key=lambda e: e["ts"])
        b, r = ss[:cut], ss[cut:]
        if b and r:
            diffs.append(_rate(r) - _rate(b))
    if not diffs:
        return None, None, None
    return ci_and_p(diffs)


def term_stats(known):
    """SECONDARY test statistics: bottom tercile of slope vs the rest.

    See TERM_SPLIT_QUANTILE for why this replaced the absolute `slope < 1.10`
    test, and why that is a power fix rather than threshold-shopping.
    """
    sub = sorted((e for e in known if e["ts"] is not None), key=lambda e: e["ts"])
    n = len(sub)
    cut = int(n * TERM_SPLIT_QUANTILE)
    legacy_sub = [e for e in known if e["ts"] is not None
                  and vrp_tier(e["vi"], **INTRADAY_LADDER) is Recommendation.EXCELLENT]
    base = {"coverage": n, "legacy_flat": sum(1 for e in legacy_sub if e["ts"] < 1.10),
            "legacy_n": len(legacy_sub)}
    if cut < 1 or n - cut < 1:
        return dict(base, low_n=0, low_rate=None, high_n=0, high_rate=None, split=None,
                    gap=None, lo=None, hi=None, p_le0=None)
    low, high = sub[:cut], sub[cut:]
    rl, rh = _rate(low), _rate(high)
    lo, hi, p_le0 = term_bootstrap(sub, cut)
    return dict(base, low_n=len(low), low_rate=rl, high_n=len(high), high_rate=rh,
                split=(low[-1]["ts"] + high[0]["ts"]) / 2, gap=rh - rl,
                lo=lo, hi=hi, p_le0=p_le0)


def term_decision(td, p_adj):
    if td["gap"] is None:
        return f"CANNOT EVALUATE — only {td['coverage']} events carry term_slope_ratio"
    if td["low_n"] < TERM_MIN_PER_BUCKET or td["high_n"] < TERM_MIN_PER_BUCKET:
        return (f"CANNOT EVALUATE — bottom n={td['low_n']}, rest n={td['high_n']}; "
                f"need >={TERM_MIN_PER_BUCKET} each")
    significant = td["p_le0"] is not None and p_adj <= ALPHA_ONE_SIDED
    if td["gap"] >= TERM_MIN_GAP_PTS and significant:
        return (f"PROMOTE flat term structure to a 50%-size flag "
                f"(derive the cutoff from split ~{td['split']:.2f}, NOT 1.10)")
    if significant:
        return f"REAL BUT SMALL (gap {td['gap']:+.1f} pts, below the {TERM_MIN_GAP_PTS:.0f}-pt bar)"
    return f"KEEP OBSERVATIONAL (gap {td['gap']:+.1f} pts, not separable from zero after Holm)"


# --- diagnostics: nothing below feeds a verdict ----------------------------------------

def suspect_timing_tags(sel, ratio=COLLAPSE_RATIO):
    """Graded reactions whose own later pre-open log says the event already happened.

    A later analysis that is pre-open by the tag, on the same expiration, with an
    implied move below `ratio` of the graded log's, is what a post-event chain
    looks like. The usual cause is a wrong timing tag (a BMO report tagged AMC),
    which also means historical_moves measured the day after the reaction. The fix
    is the calendar and a re-backfill, never an exclusion here: this is a
    heuristic, and grading on it would be choosing rows by what they show.
    """
    out = []
    for e in sel.graded:
        later = [a for a, r in sel.groups[(e["issuer"], e["earnings_date"])]
                 if a["timestamp"] > e["analysed_at"]
                 and a.get("expiration") == e.get("expiration")
                 and is_pre_open(a["timestamp"], r["earnings_date"], r["timing"])
                 and a["impl"] < ratio * e["impl"]]
        if later:
            first = min(later, key=lambda a: a["timestamp"])
            out.append({"ticker": e["ticker"], "earnings_date": e["earnings_date"],
                        "timing": e["timing"], "impl": e["impl"], "collapsed_impl": first["impl"],
                        "collapsed_at": first["timestamp"]})
    return out


def pit_intraday_vrp(impl, history, before, lookback=PIT_LOOKBACK_QUARTERS,
                     min_quarters=PIT_MIN_QUARTERS):
    """Intraday VRP from today's historical_moves, as of a reaction date.

    The last `lookback` quarters strictly before `before`, mean |intraday move|,
    the engine's window and floor. Diagnostic only: it shows how much of a logged
    VRP came from history rows since repaired. No verdict reads it.
    """
    prior = sorted((r for r in history if r["earnings_date"] < before),
                   key=lambda r: r["earnings_date"], reverse=True)[:lookback]
    if len(prior) < min_quarters:
        return None
    mean = st.mean(abs(r["im"]) for r in prior)
    return impl / mean if mean >= 1e-6 else None


def print_diagnostic(sel):
    known = sel.graded
    print()
    print("--- DIAGNOSTIC ONLY — no verdict reads this section ---")
    flagged = suspect_timing_tags(sel)
    print(f"timing tags the option chain contradicts: {len(flagged)} graded reactions have a")
    print(f"  later same-expiration log, pre-open by the tag, with implied move below "
          f"{100 * COLLAPSE_RATIO:.0f}%")
    print("  of the graded log's. Likely a wrong tag, so historical_moves measured the day")
    print("  after the reaction. Fix the calendar and re-backfill; these stay graded.")
    for f in flagged:
        print(f"    {f['ticker']:6s} {f['earnings_date']:%Y-%m-%d} {f['timing'] or '-':>7s}  "
              f"implied {f['impl']:5.2f} -> {f['collapsed_impl']:5.2f} at "
              f"{f['collapsed_at'].astimezone(ET):%m-%d %H:%M} ET")
    print()
    rows = []
    for e in known:
        pit = pit_intraday_vrp(e["impl"], sel.reactions.get(e["ticker"], []), e["earnings_date"])
        if pit is not None:
            rows.append(dict(e, vi_pit=pit))
    print("intraday VRP recomputed point-in-time from TODAY's historical_moves: the")
    print(f"{PIT_LOOKBACK_QUARTERS} quarters before each graded reaction (min {PIT_MIN_QUARTERS}), "
          f"same implied move.")
    print(f"  recomputed for {len(rows)} of {len(known)} graded events")
    if len(rows) < 5:
        return
    for key, lab in (("vi", "LOGGED"), ("vi_pit", "RECOMPUTED")):
        print(f"  crush by {lab} intraday VRP quintile:")
        qs = quintiles(rows, key)
        for i, (lo_v, hi_v, cnt, rt) in enumerate(qs, 1):
            print(f"    Q{i} [{lo_v:5.2f}-{hi_v:5.2f}] n={cnt:3d}  crush={rt:5.1f}%")
        print(f"    Q5-Q1 spread = {qs[-1][3]-qs[0][3]:+.1f} pts")
    top = sorted(rows, key=lambda e: e["vi"])[4 * len(rows) // 5:]
    inflated = sum(1 for e in top if e["vi"] > DIAG_INFLATION * e["vi_pit"])
    print(f"  logged top quintile: {inflated} of {len(top)} logged more than "
          f"{100 * (DIAG_INFLATION - 1):.0f}% above the recompute")
    ratio = st.median(e["vi"] / e["vi_pit"] for e in rows)
    print(f"  median logged/recomputed = {ratio:.3f} across all {len(rows)}")


# --- report --------------------------------------------------------------------------

def format_event_row(e):
    ts = f"{e['ts']:.2f}" if e["ts"] is not None else "  -  "
    at = e["analysed_at"].astimezone(ET).strftime("%m-%d %H:%M")
    cm = f"{e['cm']:9.1f}" if e.get("cm") is not None else f"{'':>9s}"
    if e.get("cm") is None:
        outcome = "pending"
    elif "crush" not in e:
        outcome = "no pre-open log"
    else:
        outcome = "CRUSH" if e["crush"] else "MISS"
    return (f"{e['ticker']:6s} {e['earnings_date']:%Y-%m-%d} {e['timing'] or '-':>7s} "
            f"{at:>11s} {e['impl']:6.1f} {e['vi']:6.2f} {e['vc']:6.2f} {ts:>5s} {cm}  {outcome}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="dump the per-event table")
    # No --db flag: the target comes from PG* in the environment
    # (`PGDATABASE=ivcrush_scratch ...`). A path argument would be a
    # lie -- connect() would ignore it.
    args = ap.parse_args()

    samples = load()
    sel, term_sel = samples["vrp"], samples["term"]
    known, s = sel.graded, sel.stats
    term_known = term_sel.graded
    if not known:
        print("No outcome-known A/B events. Run /maintenance backfill-outcomes.")
        return 1
    n = len(known)
    pending = len(sel.pending)

    print("=" * 62)
    print("A/B FORWARD-VALIDATION — LIVE EVALUATOR")
    print("=" * 62)
    print(f"db: {db_target()}")
    print(f"events={n + pending}  with_outcome={n}  pending={pending}  "
          f"tickers={len({e['ticker'] for e in known})}")
    print(f"base crush rate = {100*sum(e['crush'] for e in known)/n:.1f}%")
    if pending > 2:
        print(f"  -> {pending} pending; run /maintenance backfill-outcomes")
    print()

    print("--- sample hygiene (rev. 2026-09-13; see module docstring) ---")
    print(f"  analysis_log rows since {SAMPLE_START} with close VRP: {s['analysis_rows']}")
    print(f"  logged (ticker, earnings_date) events: {s['logged_events']}  "
          f"matched to a reaction: {s['matched_events']}  "
          f"pending: {pending + s['pending_duplicates']} -> {pending} after "
          f"{s['pending_duplicates']} repeated date(s)")
    pairs = ", ".join(f"{p} {d:%Y-%m-%d}" for p, d in s["share_class_pairs"]) or "none"
    print(f"  duplicate reactions removed: {s['stale_date_duplicates'] + s['share_class_duplicates']}"
          f"  (stale calendar date {s['stale_date_duplicates']}, "
          f"share class {s['share_class_duplicates']}: {pairs})")
    print(f"  reactions: {s['reactions']}  no pre-open analysis: {s['no_pre_open']}  "
          f"graded: {s['graded']}")
    print(f"  post-open log rows excluded: {s['post_open_rows']} of {s['matched_rows']}")
    print(f"  reactions whose LATEST log was post-open (graded before this fix): "
          f"{s['latest_post_open']}")
    print("  graded = FIRST analysis before the reaction-day open, 09:30 ET on: BMO the")
    print("    report date; AMC the next session; DMH/UNKNOWN/untagged the report date")
    timing = "  ".join(f"{k} {v}" for k, v in sorted(s["graded_timing"].items()))
    print(f"  timing of graded reactions: {timing}")
    print(f"  nearest-date ties (broken to the earlier reaction): {s['ties']}")
    primary_row = {(e["issuer"], e["earnings_date"]): e["analysis_id"] for e in known}
    differs = sum(1 for e in term_known
                  if primary_row.get((e["issuer"], e["earnings_date"])) != e["analysis_id"])
    print(f"  SECONDARY sample = first pre-open log that measured a slope: "
          f"{len(term_known)} reactions")
    print(f"    ({differs} graded on a different row than PRIMARY; "
          f"{term_sel.stats['post_open_rows']} post-open slope rows excluded)")
    print()

    print("--- scale check (why same-threshold comparison is invalid) ---")
    print(f"  median intraday VRP = {st.median([e['vi'] for e in known]):.2f}   "
          f"median close VRP = {st.median([e['vc'] for e in known]):.2f}")
    thr = equal_selectivity_threshold(known)
    if thr:
        print(f"  close-VRP cutoff matching intraday>=1.4 selectivity = {thr:.2f}")
        print("  (derive migrated tiers from THIS, never from hand-picked numbers)")
    print()

    print("--- top-K crush rate (backtest: top-20% 87.7% -> 90.9%, ab_test_vrp_baseline.py) ---")
    print("  descriptive: only the top-20% row is tested (PRIMARY verdict below)")
    print(f"  {'top':>5s} {'n':>5s} {'intraday':>10s} {'close':>9s} {'delta':>8s}")
    for pct in (10, 20, 30, 50, 100):
        ci, m = top_k_crush(known, "vi", pct)
        cc, _ = top_k_crush(known, "vc", pct)
        print(f"  {pct:4d}% {m:5d} {100*ci:9.1f}% {100*cc:8.1f}% {100*(cc-ci):+7.1f}")
    print()

    for key, lab in (("vi", "intraday"), ("vc", "close")):
        print(f"--- crush by {lab} VRP quintile (descriptive) ---")
        qs = quintiles(known, key)
        for i, (lo_v, hi_v, cnt, rt) in enumerate(qs, 1):
            print(f"  Q{i} [{lo_v:5.2f}-{hi_v:5.2f}] n={cnt:3d}  crush={rt:5.1f}%")
        if qs:
            print(f"  Q5-Q1 spread = {qs[-1][3]-qs[0][3]:+.1f} pts", end="  ")
        r, t = point_biserial(known, key)
        print(f"| point-biserial r={r:+.3f} t={t:+.2f} "
              f"{'p<0.05' if abs(t) > 1.97 else '(n.s.)'}, uncorrected")
        print()

    d, td = vrp_stats(known), term_stats(term_known)
    adj = holm({"vrp": d["p_le0"], "term": td["p_le0"]})
    dec, tdec = vrp_decision(d, n, adj["vrp"]), term_decision(td, adj["term"])

    print("=" * 62)
    print(f"PRIMARY — VRP CONVENTION VERDICT: {dec}")
    print(f"  top-{TOP_K_PCT}% (n={d['top_n']}): intraday {d['intraday']:.1f}%  "
          f"close {d['close']:.1f}%  delta {d['delta']:+.1f} pts")
    print(f"  bootstrap 95% CI on delta: [{d['lo']:+.1f}, {d['hi']:+.1f}] pts   "
          f"P(delta<=0)={d['p_le0']:.3f}  Holm p={adj['vrp']:.3f}")
    print(f"  criteria: n>={MIN_EVENTS} (have {n}), Holm p<={ALPHA_ONE_SIDED} "
          f"(uncorrected: CI lower bound>0), delta>=+{MIN_EFFECT_PTS} pts")
    print(f"  ⚠️ convention migrated to close-to-close {CONVENTION_MIGRATED}: "
          f"this verdict is descriptive and decides nothing")
    print()

    print(f"SECONDARY — TERM-STRUCTURE VERDICT: {tdec}")
    lr = f"{td['low_rate']:.1f}%" if td["low_rate"] is not None else "n/a"
    hr = f"{td['high_rate']:.1f}%" if td["high_rate"] is not None else "n/a"
    sp = f"{td['split']:.2f}" if td["split"] is not None else "n/a"
    print(f"  flattest third (slope < ~{sp})  n={td['low_n']:3d}  crush={lr}")
    print(f"  steeper two-thirds             n={td['high_n']:3d}  crush={hr}")
    if td["gap"] is not None and td["lo"] is not None:
        print(f"  gap {td['gap']:+.1f} pts   bootstrap 95% CI [{td['lo']:+.1f}, {td['hi']:+.1f}]"
              f"   P(gap<=0)={td['p_le0']:.3f}  Holm p={adj['term']:.3f}")
    print(f"  criteria: >={TERM_MIN_PER_BUCKET} per bucket, Holm p<={ALPHA_ONE_SIDED}, "
          f"gap>=+{TERM_MIN_GAP_PTS:.0f} pts")
    print(f"  term_slope coverage: {td['coverage']}/{n} outcome-known events")
    print("  slope distribution by VRP band (diagnostic):")
    for lab, nb, nf in term_bands(term_known):
        share = f"{100*nf/nb:.0f}%" if nb else "n/a"
        print(f"    {lab:16s} n={nb:3d}  below 1.10={nf:2d}  ({share})")
    print(f"  legacy absolute test (slope<1.10 among VRP>=1.8): "
          f"{td['legacy_flat']}/{td['legacy_n']} — retired Sep 5 2026, no power:")
    print("    observed slope runs ~1.04-2.48, so a 1.10 cutoff has almost no mass.")
    print("    Widening the VRP band does NOT fix this (VRP>=1.2 gained 1 event);")
    print("    the threshold was the binding constraint, not the band.")
    print()
    print("multiple comparisons: PRIMARY and SECONDARY are one Holm family at one-sided")
    print(f"  alpha {ALPHA_ONE_SIDED} (an unevaluable test counts as p=1). Everything else")
    print("  above is descriptive and decides nothing.")
    print("=" * 62)

    print_diagnostic(sel)

    if args.full:
        print("\n--- per-event table (graded, then no pre-open log, then pending) ---")
        print(f"{'ticker':6s} {'reaction':10s} {'timing':>7s} {'logged ET':>11s} {'impl':>6s} "
              f"{'vrp_i':>6s} {'vrp_c':>6s} {'term':>5s} {'close_mv':>9s}  outcome")
        for e in known + sel.no_pre_open + sel.pending:
            print(format_event_row(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
