"""VIX-regime VRP threshold multipliers: keep them on every path, or on none.

TickerAnalyzer._apply_adaptive_thresholds (src/application/services/analyzer.py,
as of b5ad9b1) multiplied the three VRP tier thresholds by 1.1-1.5 as the VIX
rose and refused every trade at VIX 40+. Only /analyze applied it; scan, whisper
and the Telegram bot never did, and CLAUDE.md section 1 states the tiers with no
VIX term. The multipliers were set by hand. This asks whether the events the
multiplier stops trading are worse than the events it lets through.

Two comparisons, both required, because the first alone is confounded: every
event the multiplier demotes to SKIP sits just above the base marginal threshold
(VRP 1.2 to 1.2 x factor, or anywhere at VIX 40+), and low VRP crushes less at
any VIX (CLAUDE.md "Do not re-litigate": VRP is the one monotonic factor). So
"demoted crush less than kept" would pass on VRP alone. The second holds VRP
fixed: it compares the demoted events with events in the same VRP bins at VIX
below 20, where the multiplier is 1.0 and those same ratios ARE traded. Only a
difference that survives that is evidence for a VIX term.

Resampling unit is the as-of VIX date, not the event: every event on a date
shares its VIX, and high-VIX dates arrive in a few episodes, so an event
bootstrap would count one market shock dozens of times as independent evidence.

Events and outcomes come from research/events.py: a short straddle held to
expiry, crushed = stored pre-earnings straddle > |gap-inclusive close move|. A
straddle proxy, not spread P&L. VIX is Yahoo ^VIX daily closes.

Usage (from 2.0/, with 2.0/.env exported; read-only, one SELECT and one Yahoo
download):
    ./venv/bin/python scripts/research/vix_adaptive_thresholds.py
"""

import bisect
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from research.events import load_events  # noqa: E402
from src.domain.vrp_tier import vrp_tier  # noqa: E402

# --- Pre-registered. Fixed and committed BEFORE the first run; do not tune. ---
HYPOTHESIS = (
    "Among events tradeable at the base VRP thresholds, the events the VIX-regime "
    "multiplier would demote to SKIP crush less often than (1) the events it keeps "
    "and (2) events with the same VRP at VIX below 20, where the multiplier is 1.0."
)
METRIC = "crush rate: straddle > |close move| (research/events.py), in points"

# CLAUDE.md section 1 as it stood for this study's intraday baseline (BASELINE
# below): frozen here, not read from src.domain.vrp_tier, which moved to a
# close-to-close ladder in the 2026-09 migration and no longer shares units
# with this module's intraday-only VRP inputs.
BASE_MARGINAL = 1.2
BASE_GOOD = 1.4
BASE_EXCELLENT = 1.8

# (VIX floor, multiplier), highest first: src/domain/vix_regime.py boundaries
# with AdaptiveThresholdCalculator.REGIME_ADJUSTMENTS, as of b5ad9b1. None is
# NO_TRADE_REGIMES ("extreme"): every tier becomes SKIP.
VIX_FACTORS = (
    (40.0, None),   # extreme
    (35.0, 1.5),    # high
    (30.0, 1.3),    # elevated_high
    (25.0, 1.2),    # elevated
    (20.0, 1.1),    # normal_high
    (0.0, 1.0),     # very_low, low, normal
)

# Sample: every research/events.py event with the live VRP convention (intraday
# denominator, 12-quarter lookback, 4-quarter minimum) that is tradeable at the
# base thresholds and has an as-of VIX close.
LOOKBACK_QUARTERS = 12
MIN_PRIOR_QUARTERS = 4
BASELINE = "intraday"

# As-of VIX: the last ^VIX close dated strictly BEFORE earnings_date. That is the
# entry-day close for a BMO report and the prior session for an AMC one, so no
# timing tag is needed and nothing after the entry can leak in.
VIX_SYMBOL = "^VIX"
VIX_MAX_STALENESS_DAYS = 5      # a longer gap means missing data, not a weekend

# VRP bins for the matched comparison, [edge_i, edge_i+1); the last is open-ended.
VRP_BIN_EDGES = (1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4)

BOOTSTRAP_N = 5000
SEED = 7
CI_PCT = 95

# Below either, the test cannot support a decision: INSUFFICIENT, never a pass.
MIN_DEMOTED_EVENTS = 100
MIN_DEMOTED_DATES = 20

# ADOPT (apply on every path) only if PASS, which requires all of:
#   - demoted-to-SKIP events >= MIN_DEMOTED_EVENTS on >= MIN_DEMOTED_DATES dates
#   - crush(kept) - crush(demoted): 95% date-cluster bootstrap CI lower bound > 0
#   - crush(VRP-matched VIX<20 events) - crush(demoted): same, lower bound > 0
# Anything else removes the multiplier from the analyzer.
# Secondary, reported and never decisive: full-size tiers (GOOD/EXCELLENT)
# demoted to MARGINAL, i.e. the half-size effect, measured the same way.


def factor_for(vix: float):
    """Threshold multiplier at this VIX level; None means no trade at all."""
    for floor, factor in VIX_FACTORS:
        if vix >= floor:
            return factor
    raise ValueError(f"negative VIX: {vix}")


def tier(vrp: float, factor) -> str:
    """The domain tier with every threshold scaled by factor, as the analyzer did."""
    if factor is None:
        return "SKIP"
    return vrp_tier(
        vrp, BASE_EXCELLENT * factor, BASE_GOOD * factor, BASE_MARGINAL * factor
    ).name


FULL_SIZE = ("GOOD", "EXCELLENT")


class VixIndex:
    """Daily closes, looked up strictly before a date."""

    def __init__(self, closes: dict):
        self._dates = sorted(closes)
        self._closes = closes

    def asof(self, day: date):
        i = bisect.bisect_left(self._dates, day) - 1
        if i < 0:
            return None
        d = self._dates[i]
        if (day - d).days > VIX_MAX_STALENESS_DAYS:
            return None
        return d, self._closes[d]

    @property
    def span(self):
        return (self._dates[0], self._dates[-1]) if self._dates else (None, None)


def load_vix_closes(start: date, end: date) -> dict:
    """Yahoo ^VIX daily closes, date -> close. One request, through the Yahoo gate."""
    import yfinance as yf

    from src.infrastructure.api.yahoo import yahoo_call

    hist = yahoo_call(lambda: yf.Ticker(VIX_SYMBOL).history(
        start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
        interval="1d", auto_adjust=False,
    ))
    if hist is None or hist.empty:
        raise RuntimeError(f"Yahoo returned no {VIX_SYMBOL} history for {start}..{end}")
    return {ts.date(): float(close) for ts, close in hist["Close"].items() if close == close}


def classify(events: list, vix: VixIndex):
    """Base-tradeable events with their as-of VIX and what the multiplier does.

    Returns (sample, n_without_vix). n_without_vix counts base-tradeable events
    dropped for having no as-of close.
    """
    sample, no_vix = [], 0
    for e in events:
        base = tier(e["vrp"], 1.0)
        if base == "SKIP":
            continue
        hit = vix.asof(e["earnings_date"])
        if hit is None:
            no_vix += 1
            continue
        asof, level = hit
        factor = factor_for(level)
        adapted = tier(e["vrp"], factor)
        sample.append({
            **e,
            "asof": asof,
            "vix": level,
            "factor": factor,
            "base_tier": base,
            "adapted_tier": adapted,
            "demoted_skip": adapted == "SKIP",
            "demoted_to_marginal": base in FULL_SIZE and adapted == "MARGINAL",
        })
    return sample, no_vix


def vrp_bin(vrp: float) -> int:
    return max(0, bisect.bisect_right(VRP_BIN_EDGES, vrp) - 1)


N_BINS = len(VRP_BIN_EDGES)


def _empty():
    return {
        "treated_n": 0, "treated_crushed": 0,
        "kept_n": 0, "kept_crushed": 0,
        "t_bin_n": [0] * N_BINS, "t_bin_crushed": [0] * N_BINS,
        "c_bin_n": [0] * N_BINS, "c_bin_crushed": [0] * N_BINS,
    }


def aggregate(sample: list, treated_key: str) -> dict:
    """Per as-of date counts: the unit the bootstrap resamples.

    treated_key "demoted_skip": kept = every other event in the sample.
    treated_key "demoted_to_marginal": the comparison is restricted to events
    full size at base, and kept = those still full size after the multiplier.
    Controls are comparison-eligible events at multiplier 1.0 (VIX < 20).
    """
    out = {}
    for e in sample:
        if treated_key == "demoted_to_marginal" and e["base_tier"] not in FULL_SIZE:
            continue
        a = out.setdefault(e["asof"], _empty())
        b = vrp_bin(e["vrp"])
        if e[treated_key]:
            a["treated_n"] += 1
            a["treated_crushed"] += e["crushed"]
            a["t_bin_n"][b] += 1
            a["t_bin_crushed"][b] += e["crushed"]
            continue
        if treated_key == "demoted_to_marginal" and e["adapted_tier"] not in FULL_SIZE:
            continue
        a["kept_n"] += 1
        a["kept_crushed"] += e["crushed"]
        if e["factor"] == 1.0:
            a["c_bin_n"][b] += 1
            a["c_bin_crushed"][b] += e["crushed"]
    return out


def statistics(aggs: list) -> dict:
    """Rates and both deltas from summed per-date counts. None when undefined."""
    tot = _empty()
    for a in aggs:
        for k in ("treated_n", "treated_crushed", "kept_n", "kept_crushed"):
            tot[k] += a[k]
        for k in ("t_bin_n", "t_bin_crushed", "c_bin_n", "c_bin_crushed"):
            tot[k] = [x + y for x, y in zip(tot[k], a[k], strict=True)]
    out = {"treated_n": tot["treated_n"], "kept_n": tot["kept_n"],
           "treated_dates": sum(1 for a in aggs if a["treated_n"]),
           "treated_rate": None, "kept_rate": None, "primary_delta": None,
           "matched_control_rate": None, "matched_treated_rate": None,
           "matched_delta": None, "unmatched_treated": 0}
    if tot["treated_n"] == 0 or tot["kept_n"] == 0:
        return out
    out["treated_rate"] = tot["treated_crushed"] / tot["treated_n"]
    out["kept_rate"] = tot["kept_crushed"] / tot["kept_n"]
    out["primary_delta"] = out["kept_rate"] - out["treated_rate"]

    # Direct standardisation: control crush per bin, weighted by the treated
    # events' bin mix. A treated bin with no control is left out of both sides.
    matched_n = sum(n for n, c in zip(tot["t_bin_n"], tot["c_bin_n"], strict=True) if c)
    out["unmatched_treated"] = tot["treated_n"] - matched_n
    if matched_n == 0:
        return out
    control = sum(
        (tn / matched_n) * (cc / cn)
        for tn, cn, cc in zip(tot["t_bin_n"], tot["c_bin_n"], tot["c_bin_crushed"], strict=True)
        if cn
    )
    treated = sum(
        tc for tc, cn in zip(tot["t_bin_crushed"], tot["c_bin_n"], strict=True) if cn
    ) / matched_n
    out["matched_control_rate"] = control
    out["matched_treated_rate"] = treated
    out["matched_delta"] = control - treated
    return out


def bootstrap(aggs: list, n_boot: int = BOOTSTRAP_N, seed: int = SEED) -> dict:
    """Date-cluster bootstrap CI (CI_PCT) on both deltas.

    Resamples as-of dates with replacement. A resample where a delta is
    undefined (no treated event drawn) is dropped and counted.
    """
    rng = random.Random(seed)
    n = len(aggs)
    primary, matched, dropped = [], [], 0
    for _ in range(n_boot):
        s = statistics([aggs[rng.randrange(n)] for _ in range(n)])
        if s["primary_delta"] is None or s["matched_delta"] is None:
            dropped += 1
            continue
        primary.append(s["primary_delta"])
        matched.append(s["matched_delta"])
    tail = (100 - CI_PCT) / 200

    def ci(xs):
        if not xs:
            return (None, None)
        xs.sort()
        return xs[int(tail * len(xs))], xs[min(len(xs) - 1, int((1 - tail) * len(xs)))]

    return {"primary": ci(primary), "matched": ci(matched), "dropped": dropped}


def verdict(stats: dict, cis: dict) -> str:
    if stats["treated_n"] < MIN_DEMOTED_EVENTS or stats["treated_dates"] < MIN_DEMOTED_DATES:
        return "INSUFFICIENT"
    p_lo, m_lo = cis["primary"][0], cis["matched"][0]
    if p_lo is not None and m_lo is not None and p_lo > 0 and m_lo > 0:
        return "PASS"
    return "FAIL"


def _pts(x):
    return "  n/a" if x is None else f"{100 * x:+5.1f}"


def _rate(x):
    return " n/a" if x is None else f"{100 * x:4.1f}%"


def main() -> None:
    events = load_events(lookback=LOOKBACK_QUARTERS, min_prior=MIN_PRIOR_QUARTERS,
                         baseline=BASELINE)
    first = min(e["earnings_date"] for e in events)
    last = max(e["earnings_date"] for e in events)
    vix = VixIndex(load_vix_closes(first - timedelta(days=14), last))
    sample, no_vix = classify(events, vix)

    print("VIX-regime VRP threshold multipliers -- pre-registered test")
    print(f"hypothesis: {HYPOTHESIS}")
    print(f"metric: {METRIC}")
    print(f"events loaded {len(events)} ({first} to {last}); VIX closes "
          f"{vix.span[0]} to {vix.span[1]}")
    base_tradeable = len(sample) + no_vix
    print(f"base-tradeable (VRP >= {BASE_MARGINAL}): {base_tradeable}; "
          f"without an as-of VIX: {no_vix}; in sample: {len(sample)}")

    print(f"\n{'VIX band':<12} {'factor':>6} {'events':>7} {'dates':>6} {'crush':>6} "
          f"{'->SKIP':>7} {'->MARG':>7}")
    bands = list(VIX_FACTORS)
    for i, (floor, factor) in enumerate(bands):
        ceiling = bands[i - 1][0] if i else None
        sub = [e for e in sample if e["vix"] >= floor and (ceiling is None or e["vix"] < ceiling)]
        label = f"{floor:g}+" if ceiling is None else f"{floor:g}-{ceiling:g}"
        rate = _rate(sum(e["crushed"] for e in sub) / len(sub)) if sub else "  -  "
        print(f"{label:<12} {'none' if factor is None else f'{factor:.1f}':>6} {len(sub):>7} "
              f"{len({e['asof'] for e in sub}):>6} {rate:>6} "
              f"{sum(e['demoted_skip'] for e in sub):>7} "
              f"{sum(e['demoted_to_marginal'] for e in sub):>7}")

    results = {}
    for key, label in (("demoted_skip", "PRIMARY: demoted to SKIP"),
                       ("demoted_to_marginal", "secondary: full size demoted to MARGINAL")):
        aggs = list(aggregate(sample, key).values())
        s = statistics(aggs)
        cis = bootstrap(aggs)
        results[key] = (s, cis)
        print(f"\n{label}")
        print(f"  treated {s['treated_n']} events on {s['treated_dates']} dates, crush "
              f"{_rate(s['treated_rate'])}; kept {s['kept_n']}, crush {_rate(s['kept_rate'])}")
        print(f"  kept - treated          {_pts(s['primary_delta'])} pts  "
              f"[{_pts(cis['primary'][0])}, {_pts(cis['primary'][1])}]")
        print(f"  VRP-matched VIX<20 {_rate(s['matched_control_rate'])} vs treated "
              f"{_rate(s['matched_treated_rate'])} ({s['unmatched_treated']} unmatched)")
        print(f"  matched - treated       {_pts(s['matched_delta'])} pts  "
              f"[{_pts(cis['matched'][0])}, {_pts(cis['matched'][1])}]")
        print(f"  {CI_PCT}% date-cluster bootstrap, {BOOTSTRAP_N} resamples, seed {SEED}; "
              f"{cis['dropped']} resamples undefined")

    s, cis = results["demoted_skip"]
    print(f"\nVERDICT: {verdict(s, cis)}  (PASS requires >= {MIN_DEMOTED_EVENTS} demoted "
          f"events on >= {MIN_DEMOTED_DATES} dates and both lower bounds > 0)")


if __name__ == "__main__":
    main()
