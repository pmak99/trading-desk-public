#!/usr/bin/env python3
"""
Extract Q2, Q3, Q4 2024 trading history for empirical validation.

Extracts actual trades from the database to validate:
- Market regime analysis (VIX-based)
- Adaptive lookback window optimization

Usage:
    python scripts/extract_q234_trades.py --output results/q234_2024_trades.json
"""

import sys
import argparse
import logging
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import List, Dict

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)


def extract_trades(db_path: Path, start_date: date, end_date: date) -> List[Dict]:
    """
    Extract all trades from the database for a date range.

    Args:
        db_path: Path to database
        start_date: Start date
        end_date: End date

    Returns:
        List of trade dictionaries
    """
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()

    # Get all historical moves (actual trades)
    cursor.execute("""
        SELECT
            hm.ticker,
            hm.earnings_date,
            ABS(hm.close_move_pct) as actual_move,
            hm.close_move_pct as directional_move,
            ec.timing
        FROM historical_moves hm
        LEFT JOIN earnings_calendar ec
            ON hm.ticker = ec.ticker
            AND hm.earnings_date = ec.earnings_date
        WHERE hm.earnings_date >= ?
          AND hm.earnings_date <= ?
        ORDER BY hm.earnings_date
    """, (str(start_date), str(end_date)))

    trades = []
    for row in cursor.fetchall():
        ticker, earnings_date, actual_move, directional_move, timing = row

        trades.append({
            'ticker': ticker,
            'earnings_date': earnings_date,
            'actual_move': actual_move,
            'directional_move': directional_move,
            'timing': timing or 'Unknown'
        })

    conn.close()

    logger.info(f"✓ Extracted {len(trades)} trades from {start_date} to {end_date}")

    return trades


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Extract Q2-Q4 2024 trading history')
    parser.add_argument('--db', type=Path, default=Path('data/ivcrush.db'),
                      help='Path to database')
    parser.add_argument('--output', '-o', type=Path,
                      default=Path('results/q234_2024_trades.json'),
                      help='Output JSON file')
    parser.add_argument('--start-date', type=date.fromisoformat,
                      default=date(2024, 4, 1),
                      help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', type=date.fromisoformat,
                      default=date(2024, 12, 31),
                      help='End date (YYYY-MM-DD)')

    args = parser.parse_args()

    # Setup logging
    setup_logging()

    logger.info("="*80)
    logger.info("EXTRACT Q2-Q4 2024 TRADING HISTORY")
    logger.info("="*80)
    logger.info(f"Period: {args.start_date} to {args.end_date}")

    # Extract trades
    trades = extract_trades(args.db, args.start_date, args.end_date)

    # Save to JSON
    output_data = {
        'period': {
            'start': str(args.start_date),
            'end': str(args.end_date)
        },
        'trade_count': len(trades),
        'trades': trades
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)

    with open(args.output, 'w') as f:
        json.dump(output_data, f, indent=2)

    logger.info(f"✓ Saved to {args.output}")

    # Print summary
    print(f"\n{'='*80}")
    print(f"SUMMARY")
    print(f"{'='*80}")
    print(f"Period: {args.start_date} to {args.end_date}")
    print(f"Total Trades: {len(trades)}")

    # Count by timing
    timing_counts = {}
    for trade in trades:
        timing = trade['timing']
        timing_counts[timing] = timing_counts.get(timing, 0) + 1

    print(f"\nBy Timing:")
    for timing, count in sorted(timing_counts.items()):
        print(f"  {timing}: {count} trades ({count/len(trades)*100:.1f}%)")

    print(f"\n✓ Extraction complete!")

    return 0


if __name__ == '__main__':
    sys.exit(main())
