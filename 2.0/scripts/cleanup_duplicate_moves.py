"""Dedupe adjacent-date duplicate earnings events (Jul 2026 audit).

historical_moves contains pairs where the same earnings event was written
under two nearby dates (announce date + reaction date) — a backfill artifact
from earnings_calendar holding both dates. Each pair double-counts a quarter
in VRP/TRR baselines. A company cannot report earnings twice within a few
days, so any same-ticker pair inside the window (default 2 days) is one event.

Two resolvable classes are deleted automatically:
  * identical close_move_pct (regardless of enrichment), OR
  * differing close_move_pct where exactly one row carries ORATS enrichment
    (non-null pre_earnings_straddle_pct / ern_iv_effect) — the enriched row
    is the real ORATS-backfilled event; the bare row is the no-ORATS artifact.
Keep-rule: more ORATS enrichment wins; tie -> earlier date (announce-date
convention). The loser's earnings_calendar row for that ticker is also
removed so re-running backfills cannot recreate the deleted row.

The 2-day gap and the differing-value class were added Jul 2026 after the
Jul 3 cleanup's deletions resurrected via sync-cloud: every reappeared pair
was 2 days apart, so the original <=1-day / identical-value-only query
matched none of them (dry-run reported 0 while 24 pairs were live).

Differing close_move_pct with EQUAL enrichment (typically both bare, e.g.
the MU/OKTA 2025-12 pairs) cannot be resolved from stored data — the correct
keeper needs the true report date or reaction-day price bars. These are
reported as `ambiguous` and never deleted; resolve them by hand.

Usage:
    ./venv/bin/python scripts/cleanup_duplicate_moves.py --db data/ivcrush.db            # dry run
    ./venv/bin/python scripts/cleanup_duplicate_moves.py --db data/ivcrush.db --execute
    ./venv/bin/python scripts/cleanup_duplicate_moves.py --db data/ivcrush.db --max-gap 1 # legacy window
"""

import argparse
import logging
import sqlite3
from typing import Dict, List

logger = logging.getLogger(__name__)

# Same-ticker pairs within this many days are the same earnings event.
DEFAULT_MAX_GAP_DAYS = 2

PAIR_QUERY = """
    SELECT a.id            AS id_a,
           a.earnings_date AS date_a,
           a.close_move_pct AS close_a,
           (a.pre_earnings_straddle_pct IS NOT NULL) +
           (a.ern_iv_effect IS NOT NULL)             AS enrich_a,
           b.id            AS id_b,
           b.earnings_date AS date_b,
           b.close_move_pct AS close_b,
           (b.pre_earnings_straddle_pct IS NOT NULL) +
           (b.ern_iv_effect IS NOT NULL)             AS enrich_b,
           a.ticker        AS ticker
    FROM historical_moves a
    JOIN historical_moves b
      ON a.ticker = b.ticker
     AND a.earnings_date < b.earnings_date
     AND julianday(b.earnings_date) - julianday(a.earnings_date) <= :max_gap
     AND a.close_move_pct IS NOT NULL
     AND b.close_move_pct IS NOT NULL
    ORDER BY a.ticker, a.earnings_date
"""


def dedupe_duplicate_moves(
    db_path: str, dry_run: bool = True, max_gap_days: int = DEFAULT_MAX_GAP_DAYS
) -> Dict:
    """
    Find and (unless dry_run) delete near-date duplicate earnings events.

    A duplicate pair = same ticker, earnings_dates <= ``max_gap_days`` apart,
    both with non-null close_move_pct. Resolvable when values are identical OR
    exactly one row is ORATS-enriched; the row with more enrichment is kept and
    on a tie the earlier date wins. The loser's earnings_calendar rows (that
    ticker + date only) are deleted alongside. Differing-value pairs with equal
    enrichment are returned in ``ambiguous`` and left untouched.

    Returns:
        dict with keys: dry_run, moves_deleted, calendar_deleted, pairs
        (list of {ticker, keep_date, delete_date, delete_id}), ambiguous
        (list of {ticker, date_a, close_a, date_b, close_b}).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(PAIR_QUERY, {"max_gap": max_gap_days}).fetchall()

        plan: List[Dict] = []
        ambiguous: List[Dict] = []
        consumed: set = set()  # ids already assigned in an earlier pair
        for p in rows:
            # Skip pairs that reference a row already resolved (3+ row clusters).
            if p["id_a"] in consumed or p["id_b"] in consumed:
                continue

            identical = p["close_a"] == p["close_b"]
            if not identical and p["enrich_a"] == p["enrich_b"]:
                # Values differ and enrichment can't break the tie -> unsafe.
                ambiguous.append({
                    "ticker": p["ticker"],
                    "date_a": p["date_a"], "close_a": p["close_a"],
                    "date_b": p["date_b"], "close_b": p["close_b"],
                })
                continue

            # More enrichment wins; tie -> earlier date (a is earlier).
            if p["enrich_b"] > p["enrich_a"]:
                keep_date, delete_date, delete_id = p["date_b"], p["date_a"], p["id_a"]
            else:
                keep_date, delete_date, delete_id = p["date_a"], p["date_b"], p["id_b"]
            consumed.add(p["id_a"])
            consumed.add(p["id_b"])
            plan.append({
                "ticker": p["ticker"],
                "keep_date": keep_date,
                "delete_date": delete_date,
                "delete_id": delete_id,
            })

        calendar_deleted = 0
        for item in plan:
            calendar_deleted += conn.execute(
                "SELECT COUNT(*) FROM earnings_calendar "
                "WHERE ticker = ? AND earnings_date = ?",
                (item["ticker"], item["delete_date"]),
            ).fetchone()[0]

        if not dry_run and plan:
            conn.executemany(
                "DELETE FROM historical_moves WHERE id = ?",
                [(item["delete_id"],) for item in plan],
            )
            conn.executemany(
                "DELETE FROM earnings_calendar WHERE ticker = ? AND earnings_date = ?",
                [(item["ticker"], item["delete_date"]) for item in plan],
            )
            conn.commit()

        return {
            "dry_run": dry_run,
            "moves_deleted": len(plan),
            "calendar_deleted": calendar_deleted,
            "pairs": plan,
            "ambiguous": ambiguous,
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, help="Path to ivcrush.db")
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually delete (default is dry run)",
    )
    parser.add_argument(
        "--max-gap", type=int, default=DEFAULT_MAX_GAP_DAYS,
        help=f"Max days between duplicate dates (default {DEFAULT_MAX_GAP_DAYS})",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    result = dedupe_duplicate_moves(
        args.db, dry_run=not args.execute, max_gap_days=args.max_gap
    )

    mode = "EXECUTE" if args.execute else "DRY RUN"
    logger.info(f"=== Duplicate-move dedupe ({mode}, max-gap {args.max_gap}d) ===")
    for item in result["pairs"]:
        logger.info(
            f"  {item['ticker']:<6} keep {item['keep_date']}  "
            f"delete {item['delete_date']} (moves id {item['delete_id']})"
        )
    logger.info(
        f"\nhistorical_moves rows {'deleted' if args.execute else 'to delete'}: "
        f"{result['moves_deleted']}"
    )
    logger.info(
        f"earnings_calendar rows {'deleted' if args.execute else 'to delete'}: "
        f"{result['calendar_deleted']}"
    )
    if result["ambiguous"]:
        logger.info(
            f"\n⚠️  {len(result['ambiguous'])} ambiguous pair(s) — differing "
            f"moves, equal enrichment; NOT touched, verify by hand:"
        )
        for a in result["ambiguous"]:
            logger.info(
                f"  {a['ticker']:<6} {a['date_a']} ({a['close_a']}%) <-> "
                f"{a['date_b']} ({a['close_b']}%)"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
