"""Correlate parsed trades with historical earnings moves from Postgres."""

from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from common.db import as_date, connect  # noqa: E402
from journal.models import Trade


def load_historical_moves(min_date: str = '2020-01-01') -> Dict[str, List[Dict]]:
    """Load historical moves from the database, grouped by ticker

    Args:
        min_date: Only load earnings after this date (default 2020-01-01)

    ⚠️ This took a `db_path` and returned empty unless that file existed. The
    default was 2.0/data/ivcrush.db, deleted at the 2026-09-12 cutover, so every
    import from then until 2026-09-13 matched nothing and wrote NULL
    earnings_date and actual_move. The target comes from PG* in the environment.
    """
    moves_by_ticker = defaultdict(list)

    conn = connect()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT ticker, earnings_date, gap_move_pct, intraday_move_pct
        FROM historical_moves
        WHERE earnings_date >= %s
        ORDER BY ticker, earnings_date
    """, (min_date,))

    for row in cursor.fetchall():
        ticker, earnings_date, gap_move, intraday_move = row
        moves_by_ticker[ticker].append({
            # A string, like every other date on Trade: Postgres returns a
            # `date`, which the parser's JSON export cannot serialise.
            'earnings_date': as_date(earnings_date).isoformat(),
            'gap_move_pct': gap_move,
            'intraday_move_pct': intraday_move,
            'actual_move': max(abs(gap_move), abs(intraday_move)),
        })

    conn.close()
    return moves_by_ticker


# A position opened more than this many days before a report is not that
# report's trade.
MAX_DAYS_OPEN_BEFORE = 7


def find_nearest_earnings(trade: Trade, moves_by_ticker: Dict) -> Optional[Dict]:
    """Find the earnings event that this trade straddles (for IV crush strategy)

    A position carries an event when it opened 0-7 days before the report and
    closed on or after it, however long after. Opening within a week bounds it to
    one event; the close has no bound, because a position held past the report is
    exactly the trade CLAUDE.md §8's exit evidence is about. Until 2026-09-13 the
    close also had to be within 7 days, which left four such positions undated.
    Among opens that qualify, the event closest before the close wins.
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
            earnings = as_date(move['earnings_date'])
            if earnings is None:
                continue
            earnings = datetime.combine(earnings, datetime.min.time())
        except (ValueError, TypeError):
            continue

        if open_dt:
            # IV crush: opened before earnings, closed after
            days_before = (earnings - open_dt).days
            days_after = (close_dt - earnings).days

            if 0 <= days_before <= MAX_DAYS_OPEN_BEFORE and days_after >= 0:
                diff = days_after
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


def correlate_with_vrp(trades: List[Trade]) -> Tuple[List[Trade], int]:
    """Add VRP correlation data to trades

    Returns:
        Tuple of (trades, matched_count)
    """
    moves_by_ticker = load_historical_moves()
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
