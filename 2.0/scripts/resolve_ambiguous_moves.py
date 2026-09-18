"""Resolve the ambiguous duplicate-move clusters cleanup_duplicate_moves.py leaves behind.

`cleanup_duplicate_moves.py` auto-resolves a near-date duplicate pair only when the
two rows carry identical moves, or when exactly one is ORATS-enriched. Post-ORATS
rows are bare and their moves differ, so that script classifies them `ambiguous` and
refuses to guess -- its docstring names what is actually needed: "the true report
date or reaction-day price bars". This script supplies both.

Why the earnings-calendar deduper cannot do this instead
--------------------------------------------------------
`sync_earnings_calendar.cleanup_duplicate_earnings()` breaks a confirmed-vs-confirmed
tie with a live Finnhub+Yahoo agreement. That works for upcoming or very recent
events and is the right mechanism there. It cannot work here: Finnhub's calendar
returns NODATA for windows more than a few months back (verified 2026-09-08 across
ZION/META/ORCL/AAPL), so every historical cluster fails corroboration and is left
for review forever. Yahoo's `earnings_dates` DataFrame does span history, so the
second source has to be something other than Finnhub.

Resolution rule (both signals must agree)
-----------------------------------------
1. AUTHORITY -- Yahoo `get_earnings_date_near()` returns the ticker's actual
   reported date nearest the cluster. It must match exactly one candidate.
2. CORROBORATION -- reaction-day price bars already stored on the rows. The real
   earnings reaction is the outsized move; a spurious row sits on an ordinary
   trading day. If the Yahoo-selected keeper is NOT the largest |gap| in the
   cluster and another candidate's |gap| is >= PRICE_CONTRADICTION_RATIO times
   larger, the two signals contradict and the cluster is left untouched. A rival
   move below MIN_CONTRADICTION_MOVE_PCT cannot veto -- it is not reaction-sized,
   so it carries no evidence about when the company actually reported.

Anything that fails either test is reported and never deleted. This mirrors the
project rule that an earnings date is never written on one source's say-so.

Deletions cover historical_moves AND the loser's earnings_calendar row -- without
the latter, `backfill_historical.py` recreates the deleted move on its next run
(it derives its date list from earnings_calendar). One database, so a delete is
simply a delete; there is no cloud copy to overwrite.

⚠️ "Yahoo reports X, which matches 0 of N candidates" usually means the cluster is
not a duplicate at all: EVERY stored row sits on the wrong day and the real
reaction is missing (2026-09-13: TSLA stored 04-21 and 04-30 around a 04-22 AMC
report; FIG stored 04-21 and 04-29 with its real 05-14 event absent). That needs
the calendar corrected and the event re-backfilled, not a row deleted -- this
script correctly refuses.

Usage (target from PG* in the environment):
    ./venv/bin/python scripts/resolve_ambiguous_moves.py
    ./venv/bin/python scripts/resolve_ambiguous_moves.py --execute
    ./venv/bin/python scripts/resolve_ambiguous_moves.py --tickers META ORCL
"""

import argparse
import logging
from typing import Dict, List, Optional

from common.constants import EARNINGS_SAME_EVENT_DAYS  # noqa: E402
from common.db import connect  # noqa: E402

logger = logging.getLogger(__name__)

# Same-ticker rows within this many days cannot be separate quarters.
# Deliberately far below the ~90-day quarterly cadence: this script must never
# be able to collapse two genuine consecutive quarters into one.
DEFAULT_MAX_GAP_DAYS = EARNINGS_SAME_EVENT_DAYS

# If the Yahoo-chosen keeper is not the biggest mover, another candidate whose
# |gap| exceeds it by at least this factor is treated as a contradiction.
PRICE_CONTRADICTION_RATIO = 3.0

# ...but only when that rival move is big enough to actually BE an earnings
# reaction. Without this floor the ratio test fires on rounding noise: MTB
# 0.03% vs 0.54% reads as "16.5x larger" and vetoes a sound Yahoo resolution,
# even though neither day moved enough to be an earnings event. Price is a
# veto on Yahoo's authority, not an independent vote — when no candidate shows
# a reaction-sized move there is nothing to contradict and the veto must not
# fire. Ironically these micro-move rows are the ones that most need removing:
# a spurious 0.06% row drags the historical mean move (the VRP denominator)
# down hardest.
MIN_CONTRADICTION_MOVE_PCT = 2.0


CLUSTER_QUERY = """
    SELECT id, ticker, earnings_date, gap_move_pct, close_move_pct
    FROM historical_moves
    WHERE close_move_pct IS NOT NULL
    ORDER BY ticker, earnings_date
"""


def find_clusters(max_gap_days: int = DEFAULT_MAX_GAP_DAYS,
                  tickers: Optional[List[str]] = None) -> List[Dict]:
    """Group historical_moves rows into same-event clusters.

    A cluster is 2+ rows for one ticker whose consecutive dates are each within
    max_gap_days. Returns one dict per cluster: {ticker, rows:[...]}.
    """
    conn = connect()
    try:
        rows = [r._asdict() for r in conn.execute(CLUSTER_QUERY).fetchall()]
    finally:
        conn.close()

    if tickers:
        wanted = {t.upper() for t in tickers}
        rows = [r for r in rows if r["ticker"] in wanted]

    by_ticker: Dict[str, List[Dict]] = {}
    for r in rows:
        by_ticker.setdefault(r["ticker"], []).append(r)

    clusters: List[Dict] = []
    for ticker, trows in by_ticker.items():
        trows.sort(key=lambda r: r["earnings_date"])
        current = [trows[0]]
        for r in trows[1:]:
            prev = current[-1]["earnings_date"]
            cur = r["earnings_date"]
            if (cur - prev).days <= max_gap_days:
                current.append(r)
            else:
                if len(current) > 1:
                    clusters.append({"ticker": ticker, "rows": current})
                current = [r]
        if len(current) > 1:
            clusters.append({"ticker": ticker, "rows": current})
    return clusters


def _price_contradicts(keeper: Dict, cluster_rows: List[Dict]) -> Optional[str]:
    """Return a reason string if price action contradicts the keeper, else None."""
    def mag(r):
        return abs(r["gap_move_pct"] or 0.0)

    keeper_mag = mag(keeper)
    biggest = max(cluster_rows, key=mag)
    if biggest["id"] == keeper["id"]:
        return None
    if mag(biggest) < MIN_CONTRADICTION_MOVE_PCT:
        return None
    if keeper_mag <= 0.01:
        return (f"keeper {keeper['earnings_date']} has no move ({keeper_mag:.2f}%) "
                f"while {biggest['earnings_date']} moved {mag(biggest):.2f}%")
    if mag(biggest) / keeper_mag >= PRICE_CONTRADICTION_RATIO:
        return (f"keeper {keeper['earnings_date']} moved {keeper_mag:.2f}% but "
                f"{biggest['earnings_date']} moved {mag(biggest):.2f}% "
                f"({mag(biggest)/keeper_mag:.1f}x larger)")
    return None


def resolve_clusters(yahoo, dry_run: bool = True,
                     max_gap_days: int = DEFAULT_MAX_GAP_DAYS,
                     tickers: Optional[List[str]] = None) -> Dict:
    """Resolve same-event clusters using Yahoo authority + price corroboration.

    Yahoo lookups need no spacing here: YahooFinanceEarnings makes each request
    under the process's Yahoo limit and backoff (src/infrastructure/api/yahoo.py),
    which replaced this loop's own 1 s sleep.

    Returns dict with keys: dry_run, resolved, unresolved, moves_deleted,
    calendar_deleted.
    """
    clusters = find_clusters(max_gap_days=max_gap_days, tickers=tickers)
    resolved: List[Dict] = []
    unresolved: List[Dict] = []

    # Cached per (ticker, cluster), NOT per ticker. get_earnings_date_near answers
    # "nearest to THESE candidates", so a ticker with two clusters in different
    # quarters needs two lookups — caching one answer per ticker would hand the
    # second cluster the first cluster's date, which matches nothing and would
    # silently strand every additional cluster as unresolved.
    yahoo_cache: Dict[tuple, object] = {}

    for cluster in clusters:
        ticker = cluster["ticker"]
        rows = cluster["rows"]
        candidates = [r["earnings_date"] for r in rows]
        cache_key = (ticker, tuple(r["earnings_date"] for r in rows))

        if cache_key not in yahoo_cache:
            try:
                result = yahoo.get_earnings_date_near(ticker, candidates)
                yahoo_cache[cache_key] = result if not result.is_err else None
            except Exception as exc:  # network/parse failures are not fatal
                logger.warning("  %s: Yahoo lookup failed (%s)", ticker, exc)
                yahoo_cache[cache_key] = None

        yresult = yahoo_cache[cache_key]
        if yresult is None:
            unresolved.append({"ticker": ticker, "rows": rows,
                               "reason": "Yahoo lookup unavailable"})
            continue

        ydate, _timing = yresult.value
        matches = [r for r in rows if r["earnings_date"] == ydate]
        if len(matches) != 1:
            unresolved.append({
                "ticker": ticker, "rows": rows,
                "reason": (f"Yahoo reports {ydate.isoformat()}, which matches "
                           f"{len(matches)} of {len(rows)} candidates"),
            })
            continue

        keeper = matches[0]
        contradiction = _price_contradicts(keeper, rows)
        if contradiction:
            unresolved.append({"ticker": ticker, "rows": rows,
                               "reason": f"price contradicts Yahoo: {contradiction}"})
            continue

        losers = [r for r in rows if r["id"] != keeper["id"]]
        resolved.append({"ticker": ticker, "keeper": keeper, "losers": losers,
                         "yahoo_date": ydate.isoformat()})

    moves_deleted = 0
    calendar_deleted = 0
    if not dry_run and resolved:
        conn = connect()
        try:
            cur = conn.cursor()
            for item in resolved:
                for loser in item["losers"]:
                    cur.execute("DELETE FROM historical_moves WHERE id = %s", (loser["id"],))
                    moves_deleted += cur.rowcount
                    cur.execute(
                        "DELETE FROM earnings_calendar WHERE ticker = %s AND earnings_date = %s",
                        (item["ticker"], loser["earnings_date"]),
                    )
                    calendar_deleted += cur.rowcount
            conn.commit()
        finally:
            conn.close()
    else:
        # Dry run must report the SAME blast radius the execute would produce,
        # including earnings_calendar. Counting only moves here made the dry run
        # under-report calendar impact as 0 while the real run deleted 227 rows
        # (seen 2026-09-08) — a dry run that understates what it will touch is
        # worse than no dry run, because it is trusted.
        conn = connect()
        try:
            for item in resolved:
                for loser in item["losers"]:
                    moves_deleted += 1
                    calendar_deleted += conn.execute(
                        "SELECT COUNT(*) FROM earnings_calendar "
                        "WHERE ticker = %s AND earnings_date = %s",
                        (item["ticker"], loser["earnings_date"]),
                    ).fetchone()[0]
        finally:
            conn.close()

    return {"dry_run": dry_run, "resolved": resolved, "unresolved": unresolved,
            "moves_deleted": moves_deleted, "calendar_deleted": calendar_deleted}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    # No --db flag: the target comes from PG* in the environment
    # (`PGDATABASE=ivcrush_scratch ...`). A path argument would be a
    # lie -- connect() would ignore it.
    parser.add_argument("--execute", action="store_true",
                        help="Apply deletions (default is a dry run)")
    parser.add_argument("--max-gap", type=int, default=DEFAULT_MAX_GAP_DAYS,
                        help=f"Same-event window in days (default: {DEFAULT_MAX_GAP_DAYS})")
    parser.add_argument("--tickers", nargs="*", default=None,
                        help="Limit to these tickers")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from src.infrastructure.data_sources.yahoo_finance_earnings import (
        YahooFinanceEarnings,
    )

    mode = "EXECUTE" if args.execute else "DRY RUN"
    logger.info(f"=== Resolve ambiguous duplicate moves ({mode}, max-gap {args.max_gap}d) ===")

    result = resolve_clusters(YahooFinanceEarnings(), dry_run=not args.execute,
                              max_gap_days=args.max_gap, tickers=args.tickers)

    for item in result["resolved"]:
        losers = ", ".join(f"{loser['earnings_date']} ({(loser['gap_move_pct'] or 0):.2f}%)"
                           for loser in item["losers"])
        logger.info(f"  {item['ticker']}: keep {item['keeper']['earnings_date']} "
                    f"({(item['keeper']['gap_move_pct'] or 0):.2f}%) [Yahoo] — remove {losers}")

    if result["unresolved"]:
        logger.warning(f"\n{len(result['unresolved'])} cluster(s) NOT resolved:")
        for item in result["unresolved"]:
            dates = ", ".join(r["earnings_date"].isoformat() for r in item["rows"])
            logger.warning(f"  {item['ticker']}: [{dates}] — {item['reason']}")

    verb = "Would delete" if result["dry_run"] else "Deleted"
    logger.info(f"\n{verb} {result['moves_deleted']} historical_moves row(s) "
                f"and {result['calendar_deleted']} earnings_calendar row(s) "
                f"across {len(result['resolved'])} resolved cluster(s)")


if __name__ == "__main__":
    main()
