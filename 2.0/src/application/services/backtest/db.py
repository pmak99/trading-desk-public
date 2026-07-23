"""SQLite query functions for historical moves and earnings events."""
import sqlite3
from datetime import date
from pathlib import Path
from typing import List, Tuple


def get_historical_moves(
    db_path: Path,
    ticker: str,
    before_date: date,
    num_quarters: int = 4,
) -> List[Tuple[date, float]]:
    """
    Get historical moves for a ticker before a specific date.

    Args:
        db_path: Path to SQLite database
        ticker: Stock symbol
        before_date: Only include moves before this date
        num_quarters: Number of past quarters to include

    Returns:
        List of (date, move_pct) tuples, ordered most recent first
    """
    conn = sqlite3.connect(str(db_path), timeout=30)
    cursor = conn.cursor()

    cursor.execute(
        '''
        SELECT earnings_date, close_move_pct
        FROM historical_moves
        WHERE ticker = ?
          AND earnings_date < ?
        ORDER BY earnings_date DESC
        LIMIT ?
        ''',
        (ticker, str(before_date), num_quarters),
    )

    moves = [
        (date.fromisoformat(row[0]), row[1])
        for row in cursor.fetchall()
    ]

    conn.close()
    return moves


def get_all_earnings_in_period(
    db_path: Path,
    start_date: date,
    end_date: date,
) -> List[Tuple[str, date, float]]:
    """
    Get all earnings events in a date range.

    Args:
        db_path: Path to SQLite database
        start_date: Start of period (inclusive)
        end_date: End of period (inclusive)

    Returns:
        List of (ticker, earnings_date, actual_move) tuples, ordered by date then ticker
    """
    conn = sqlite3.connect(str(db_path), timeout=30)
    cursor = conn.cursor()

    cursor.execute(
        '''
        SELECT ticker, earnings_date, close_move_pct
        FROM historical_moves
        WHERE earnings_date >= ?
          AND earnings_date <= ?
        ORDER BY earnings_date, ticker
        ''',
        (str(start_date), str(end_date)),
    )

    events = [
        (row[0], date.fromisoformat(row[1]), row[2])
        for row in cursor.fetchall()
    ]

    conn.close()
    return events
