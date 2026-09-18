"""VRP-denominator backtest, ranked through the LIVE scan scorer.

Question: does ranking historical events on close-baseline VRP (gap-inclusive,
previous close -> reaction close) select more IV crush than the intraday
baseline (reaction-day open -> close) that graded VRP until 2026-09? This is the
universe backtest behind the VRP-denominator decision in CLAUDE.md "Scoring"
("VRP denominator — decided 2026-09": VRP is graded close-to-close since);
ab_evaluate_live.py is its forward check on live scans, descriptive since the
migration. This script is descriptive: it reports, it does not decide.

EVENTS come from the shared research core, research/events.py, loaded once per
baseline with the live lookback (12 quarters, analyzer.py get_historical_moves
limit=12) and inner-joined on (ticker, earnings_date), so both conventions rank
the same events. The outcome does not depend on the baseline:
crushed = straddle > |close move|, the gap-inclusive move a held position eats.

Two rankings per convention:
  live score -- the scan scorer (calculate_scan_quality_score, imported, never
                restated) read against the 4.0 target these results were
                recorded with, not whatever SCORE_VRP_TARGET is live today. The
                implied move is the stored pre-earnings straddle. Liquidity is
                not stored historically, so every event scores UNKNOWN (scored
                as WARNING, 12 points): a constant that cannot reorder events.
  raw VRP    -- straddle / mean prior |move|, ranked directly.

Read the pairs differently. Close VRP runs materially smaller than intraday VRP
(the script prints the median per-event ratio; ab_evaluate_live.py measured
1.90x on live-logged events), so inside the live score the close convention
also shrinks the VRP term against the implied-move term: the live-score rows
move the denominator AND the effective weights together. Ranks are unit-free,
so the raw-VRP rows isolate the denominator.

Selection is by rank at matched selectivity (top 20/30/50%), never by a shared
numeric threshold -- a shared threshold compares units, not conventions (see
ab_evaluate_live.py).

Caveats: crush and edge are a short-straddle proxy, gross of costs, not spread
P&L (events.py). Survivorship bias inflates absolute crush rates; comparisons
within the universe still hold. Tied live scores (rounded to 0.1) keep the
loader's ticker/date order, as the live sort does when liquidity is equal.

Read-only: one SELECT per baseline, through load_events.

Usage (from 2.0/, with 2.0/.env exported):
    ./venv/bin/python scripts/ab_test_vrp_baseline.py
"""

import random
import statistics
import sys
from datetime import date
from pathlib import Path

_ENGINE_ROOT = str(Path(__file__).resolve().parents[1])  # 2.0/
if _ENGINE_ROOT not in sys.path:
    sys.path.insert(0, _ENGINE_ROOT)

from scripts.research.events import load_events  # noqa: E402
from scripts.scan.quality_scorer import calculate_scan_quality_score  # noqa: E402

LOOKBACK_QUARTERS = 12            # live: analyzer.py get_historical_moves(limit=12)
SPLIT_DATE = date(2025, 6, 1)     # earlier vs later period; nothing is fitted
TOP_K_PCTS = (20, 30, 50)
CI_TOP_K_PCT = 20                 # the selectivity ab_evaluate_live.py decides on
BOOTSTRAP_N = 2000
SEED = 7
HISTORICAL_LIQUIDITY_TIER = "UNKNOWN"   # not stored per event; scored as WARNING

BASELINES = ("intraday", "close")
RANKINGS = (("live score", "score"), ("raw VRP", "vrp"))

_OUTCOME_FIELDS = ("straddle", "actual", "crushed", "edge")


def match_events(intraday_events: list[dict], close_events: list[dict]) -> list[dict]:
    """Inner-join the two baselines' events on (ticker, earnings_date).

    Returns one dict per event present under both baselines, carrying the
    shared outcome plus vrp_intraday and vrp_close, in intraday load order.
    Raises ValueError if the two loads disagree on an event's outcome, which
    would mean the baseline leaked into what is being measured.
    """
    close_by_key = {(e["ticker"], e["earnings_date"]): e for e in close_events}
    matched = []
    for ie in intraday_events:
        ce = close_by_key.get((ie["ticker"], ie["earnings_date"]))
        if ce is None:
            continue
        for field in _OUTCOME_FIELDS:
            if ie[field] != ce[field]:
                raise ValueError(
                    f"{ie['ticker']} {ie['earnings_date']}: {field} differs by "
                    f"baseline ({ie[field]!r} vs {ce[field]!r})"
                )
        matched.append(
            {
                "ticker": ie["ticker"],
                "earnings_date": ie["earnings_date"],
                "straddle": ie["straddle"],
                "actual": ie["actual"],
                "crushed": ie["crushed"],
                "edge": ie["edge"],
                "vrp_intraday": ie["vrp"],
                "vrp_close": ce["vrp"],
            }
        )
    return matched


def live_score(vrp: float, straddle_pct: float) -> float:
    """Score one historical event with the live scan scorer."""
    return calculate_scan_quality_score(
        {
            "vrp_ratio": vrp,
            "implied_move_pct": straddle_pct,
            "liquidity_tier": HISTORICAL_LIQUIDITY_TIER,
        },
        vrp_target=4.0,  # the target these results were recorded with (RESEARCH_RESULTS, CLAUDE.md "VRP denominator")
    )


def add_live_scores(events: list[dict]) -> None:
    """Add score_intraday and score_close to each matched event, in place."""
    for e in events:
        for baseline in BASELINES:
            e[f"score_{baseline}"] = live_score(e[f"vrp_{baseline}"], e["straddle"])


def top_k(events: list[dict], key: str, pct: int) -> dict:
    """Crush rate and mean gross edge of the top pct% of events ranked on key.

    Ties keep input order (sorted() is stable).
    """
    if not events:
        raise ValueError("top_k needs at least one event")
    m = max(1, round(len(events) * pct / 100))
    top = sorted(events, key=lambda e: -e[key])[:m]
    return {
        "n": m,
        "crush_pct": 100 * sum(e["crushed"] for e in top) / m,
        "edge": sum(e["edge"] for e in top) / m,
    }


def split_events(events: list[dict], split: date) -> tuple[list[dict], list[dict]]:
    """(before split, on or after split). earnings_date is a date, never a str."""
    before = [e for e in events if e["earnings_date"] < split]
    after = [e for e in events if e["earnings_date"] >= split]
    return before, after


def median_vrp_scale(events: list[dict]) -> float:
    """Median of vrp_intraday / vrp_close: how much larger intraday VRP runs."""
    return statistics.median(e["vrp_intraday"] / e["vrp_close"] for e in events)


def bootstrap_top_k_delta(
    events: list[dict],
    key_a: str,
    key_b: str,
    pct: int = CI_TOP_K_PCT,
    n_boot: int = BOOTSTRAP_N,
    seed: int = SEED,
) -> tuple[float, float]:
    """95% CI, in points, on top-K crush under key_b minus under key_a.

    Resamples events with replacement; both rankings see the same resample.
    """
    rng = random.Random(seed)
    n = len(events)
    diffs = []
    for _ in range(n_boot):
        sample = [events[rng.randrange(n)] for _ in range(n)]
        diffs.append(
            top_k(sample, key_b, pct)["crush_pct"] - top_k(sample, key_a, pct)["crush_pct"]
        )
    diffs.sort()
    return diffs[int(0.025 * n_boot)], diffs[min(n_boot - 1, int(0.975 * n_boot))]


def _crush_pct(events: list[dict]) -> float:
    return 100 * sum(e["crushed"] for e in events) / len(events)


def main() -> None:
    intraday = load_events(baseline="intraday", lookback=LOOKBACK_QUARTERS)
    close = load_events(baseline="close", lookback=LOOKBACK_QUARTERS)
    events = match_events(intraday, close)
    add_live_scores(events)
    before, after = split_events(events, SPLIT_DATE)
    periods = (
        ("all", events),
        (f"< {SPLIT_DATE}", before),
        (f">= {SPLIT_DATE}", after),
    )

    dates = [e["earnings_date"] for e in events]
    print("VRP-denominator backtest -- research/events.py, ranked through the live scan scorer")
    print(f"lookback {LOOKBACK_QUARTERS} quarters; liquidity {HISTORICAL_LIQUIDITY_TIER} "
          f"(constant); outcome = straddle > |close move|")
    print(f"events loaded: intraday {len(intraday)}, close {len(close)}; matched {len(events)} "
          f"({min(dates)} to {max(dates)})")
    print(f"median intraday/close VRP: {median_vrp_scale(events):.2f}x")
    print("base crush rate (all events in period): " + ", ".join(
        f"{name} {_crush_pct(sub):.1f}% (n={len(sub)})" for name, sub in periods))
    print("cells: top-K crush rate / mean gross edge (straddle - |move|, pts)")

    header = f"\n{'ranking':<11} {'baseline':<9} {'period':<14} {'n':>5}"
    header += "".join(f"{'top ' + str(p) + '%':>22}" for p in TOP_K_PCTS)
    print(header)
    for name, sub in periods:
        for label, prefix in RANKINGS:
            for baseline in BASELINES:
                key = f"{prefix}_{baseline}"
                cells = []
                for p in TOP_K_PCTS:
                    r = top_k(sub, key, p)
                    cells.append(f"{r['crush_pct']:5.1f}% {r['edge']:+6.2f}")
                print(f"{label:<11} {baseline:<9} {name:<14} {len(sub):>5}"
                      + "".join(f"{c:>22}" for c in cells))
        print()

    print(f"top {CI_TOP_K_PCT}% crush, close minus intraday, points "
          f"[bootstrap 95% CI, {BOOTSTRAP_N} resamples, seed {SEED}]")
    for name, sub in periods:
        for label, prefix in RANKINGS:
            a, b = f"{prefix}_intraday", f"{prefix}_close"
            delta = top_k(sub, b, CI_TOP_K_PCT)["crush_pct"] - top_k(sub, a, CI_TOP_K_PCT)["crush_pct"]
            lo, hi = bootstrap_top_k_delta(sub, a, b)
            print(f"  {label:<11} {name:<14} {delta:+5.1f}  [{lo:+5.1f}, {hi:+5.1f}]")


if __name__ == "__main__":
    main()
