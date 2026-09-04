#!/usr/bin/env python3
"""
Backfill missing earnings_date for strategies.

Matches strategies to earnings events where the trade window
brackets (or is very close to) the earnings date.
"""

import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Tuple


def find_matching_earnings(conn: sqlite3.Connection) -> List[Tuple[int, str, float]]:
    """
    Find strategies that can be matched to earnings dates.

    Returns list of (strategy_id, earnings_date, actual_move) tuples.
    """
    cursor = conn.cursor()

    # Match strategies to earnings where:
    # - Same ticker
    # - Earnings date is within the trade window (acquired_date to sale_date)
    # - Or within 3 days of the window (to catch edge cases)
    cursor.execute("""
        SELECT
            s.id as strategy_id,
            h.earnings_date,
            MAX(ABS(h.gap_move_pct), ABS(COALESCE(h.intraday_move_pct, 0))) as actual_move,
            s.symbol,
            s.acquired_date,
            s.sale_date
        FROM strategies s
        JOIN historical_moves h
            ON s.symbol = h.ticker
            AND h.earnings_date >= date(s.acquired_date, '-3 days')
            AND h.earnings_date <= date(s.sale_date, '+3 days')
        WHERE s.earnings_date IS NULL
        GROUP BY s.id
        ORDER BY s.symbol, s.sale_date
    """)

    return cursor.fetchall()


def update_strategy_earnings(
    conn: sqlite3.Connection,
    strategy_id: int,
    earnings_date: str,
    actual_move: float
) -> bool:
    """Update a single strategy with earnings data."""
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE strategies
        SET earnings_date = ?, actual_move = ?
        WHERE id = ?
    """, (earnings_date, actual_move, strategy_id))
    return cursor.rowcount > 0


def run_backfill(db_path: str, dry_run: bool = False) -> Dict[str, Any]:
    """
    Run the earnings date backfill.

    Returns stats on updates made.
    """
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA foreign_keys=ON')

    try:
        matches = find_matching_earnings(conn)

        stats = {
            'matches_found': len(matches),
            'updates_made': 0,
            'by_symbol': {},
        }

        expected_columns = 6  # strategy_id, earnings_date, actual_move, symbol, acq, sale

        if dry_run:
            print(f"\n[DRY RUN] Would update {len(matches)} strategies:")
            for row in matches[:20]:
                if len(row) != expected_columns:
                    print(f"  WARNING: Skipping row with {len(row)} columns (expected {expected_columns}): {row}")
                    continue
                strategy_id, earnings_date, actual_move, symbol, acq, sale = row
                print(f"  {symbol}: strategy {strategy_id}, earnings {earnings_date}, move {actual_move:.2f}%")
            if len(matches) > 20:
                print(f"  ... and {len(matches) - 20} more")
            return stats

        for row in matches:
            if len(row) != expected_columns:
                print(f"  WARNING: Skipping row with {len(row)} columns (expected {expected_columns}): {row}")
                continue
            strategy_id, earnings_date, actual_move, symbol, acq, sale = row
            if update_strategy_earnings(conn, strategy_id, earnings_date, actual_move):
                stats['updates_made'] += 1
                stats['by_symbol'][symbol] = stats['by_symbol'].get(symbol, 0) + 1

        conn.commit()
        return stats

    except Exception as e:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    import argparse

    project_root = Path(__file__).parent.parent
    default_db = project_root / "2.0" / "data" / "ivcrush.db"

    parser = argparse.ArgumentParser(description='Backfill earnings dates for strategies')
    parser.add_argument('--db', default=str(default_db), help='Database path')
    parser.add_argument('--dry-run', action='store_true', help='Preview without making changes')

    args = parser.parse_args()

    print(f"Database: {args.db}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")

    stats = run_backfill(args.db, dry_run=args.dry_run)

    if not args.dry_run:
        print(f"\nBackfill Results:")
        print(f"  Matches found: {stats['matches_found']}")
        print(f"  Updates made: {stats['updates_made']}")

        if stats['by_symbol']:
            print(f"\nBy Symbol (top 10):")
            sorted_symbols = sorted(stats['by_symbol'].items(), key=lambda x: -x[1])
            for symbol, count in sorted_symbols[:10]:
                print(f"  {symbol}: {count} strategies")


if __name__ == "__main__":
    main()
