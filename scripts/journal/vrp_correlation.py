"""Correlate parsed trades with historical earnings moves from ivcrush.db."""

import os
import sqlite3
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from journal.models import Trade


def load_historical_moves(db_path: str, min_date: str = '2020-01-01') -> Dict[str, List[Dict]]:
    """Load historical moves from ivcrush database, grouped by ticker

    Args:
        db_path: Path to ivcrush.db
        min_date: Only load earnings after this date (default 2020-01-01)
    """
    moves_by_ticker = defaultdict(list)

    if not os.path.exists(db_path):
        print(f"      Warning: Database not found at {db_path}")
        return moves_by_ticker

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT ticker, earnings_date, gap_move_pct, intraday_move_pct
        FROM historical_moves
        WHERE earnings_date >= ?
        ORDER BY ticker, earnings_date
    """, (min_date,))

    for row in cursor.fetchall():
        ticker, earnings_date, gap_move, intraday_move = row
        moves_by_ticker[ticker].append({
            'earnings_date': earnings_date,
            'gap_move_pct': gap_move,
            'intraday_move_pct': intraday_move,
            'actual_move': max(abs(gap_move), abs(intraday_move)),
        })

    conn.close()
    return moves_by_ticker


def find_nearest_earnings(trade: Trade, moves_by_ticker: Dict) -> Optional[Dict]:
    """Find the earnings event that this trade straddles (for IV crush strategy)

    For IV crush: position opened BEFORE earnings, closed AFTER earnings.
    This function validates that the trade window brackets an earnings date.
    """
    ticker = trade.symbol
    if ticker not in moves_by_ticker:
        return None

    try:
        # Use normalised dates: Fidelity inverts acquired/sale for credit trades
        # (sale_date = when opened, acquired_date = when closed).
        # Trade.close_date handles this inversion; open_date is the other field.
        close_date_str = trade.close_date  # actual close, regardless of trade direction
        open_date_str = (
            trade.sale_date
            if close_date_str == trade.acquired_date
            else trade.acquired_date
        )
        close_dt = datetime.strptime(close_date_str, '%Y-%m-%d')
        open_dt = datetime.strptime(open_date_str, '%Y-%m-%d') if open_date_str else None
    except (ValueError, TypeError):
        return None

    best_match = None
    min_diff = float('inf')

    for move in moves_by_ticker[ticker]:
        try:
            earnings = datetime.strptime(move['earnings_date'], '%Y-%m-%d')
        except ValueError:
            continue

        if open_dt:
            # IV crush: opened before earnings, closed after
            days_before = (earnings - open_dt).days
            days_after = (close_dt - earnings).days

            if 0 <= days_before <= 7 and 0 <= days_after <= 7:
                diff = abs(days_after)
                if diff < min_diff:
                    min_diff = diff
                    best_match = move
        else:
            # No open date — loose match: closed 0-3 days after earnings
            days_after = (close_dt - earnings).days
            if 0 <= days_after <= 3:
                if days_after < min_diff:
                    min_diff = days_after
                    best_match = move

    return best_match


def correlate_with_vrp(trades: List[Trade], db_path: str) -> Tuple[List[Trade], int]:
    """Add VRP correlation data to trades

    Returns:
        Tuple of (trades, matched_count)
    """
    moves_by_ticker = load_historical_moves(db_path)
    matched_count = 0

    for trade in trades:
        if not trade.is_option:
            continue

        match = find_nearest_earnings(trade, moves_by_ticker)
        if match:
            trade.earnings_date = match['earnings_date']
            trade.actual_move = match['actual_move']
            matched_count += 1

    return trades, matched_count
