"""
Market Hours Utility.

Determines if US equity markets are open/closed for accurate liquidity assessment.
During market closed periods, liquidity data may be stale or missing (volume=0, OI=0).
"""

import logging
from datetime import datetime, time, timedelta
from typing import Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# US Eastern timezone (handles DST automatically)
ET = ZoneInfo("America/New_York")

# Regular US equity market hours
MARKET_OPEN = time(9, 30)   # 9:30 AM ET
MARKET_CLOSE = time(16, 0)  # 4:00 PM ET

# US market holidays for 2024-2026
# Source: NYSE holiday calendar
# NOTE: Update annually - add next year's holidays before January
US_MARKET_HOLIDAYS = {
    # 2024
    (2024, 1, 1),   # New Year's Day
    (2024, 1, 15),  # MLK Day
    (2024, 2, 19),  # Presidents Day
    (2024, 3, 29),  # Good Friday
    (2024, 5, 27),  # Memorial Day
    (2024, 6, 19),  # Juneteenth
    (2024, 7, 4),   # Independence Day
    (2024, 9, 2),   # Labor Day
    (2024, 11, 28), # Thanksgiving
    (2024, 12, 25), # Christmas
    # 2025
    (2025, 1, 1),   # New Year's Day
    (2025, 1, 20),  # MLK Day
    (2025, 2, 17),  # Presidents Day
    (2025, 4, 18),  # Good Friday
    (2025, 5, 26),  # Memorial Day
    (2025, 6, 19),  # Juneteenth
    (2025, 7, 4),   # Independence Day
    (2025, 9, 1),   # Labor Day
    (2025, 11, 27), # Thanksgiving
    (2025, 12, 25), # Christmas
    # 2026
    (2026, 1, 1),   # New Year's Day
    (2026, 1, 19),  # MLK Day
    (2026, 2, 16),  # Presidents Day
    (2026, 4, 3),   # Good Friday
    (2026, 5, 25),  # Memorial Day
    (2026, 6, 19),  # Juneteenth
    (2026, 7, 3),   # Independence Day (observed, Jul 4 is Saturday)
    (2026, 9, 7),   # Labor Day
    (2026, 11, 26), # Thanksgiving
    (2026, 12, 25), # Christmas
}


def is_market_open(dt: datetime = None) -> bool:
    """
    Check if US equity markets are currently open.

    Args:
        dt: Datetime to check (default: now)

    Returns:
        True if market is open, False otherwise
    """
    if dt is None:
        dt = datetime.now(ET)
    elif dt.tzinfo is None:
        # Assume ET if no timezone
        dt = dt.replace(tzinfo=ET)
    else:
        # Convert to ET
        dt = dt.astimezone(ET)

    # Check if weekend (Saturday=5, Sunday=6)
    if dt.weekday() >= 5:
        return False

    # Check if holiday
    if (dt.year, dt.month, dt.day) in US_MARKET_HOLIDAYS:
        return False

    # Check if within trading hours
    current_time = dt.time()
    return MARKET_OPEN <= current_time < MARKET_CLOSE


def get_market_status() -> Tuple[bool, str]:
    """
    Get current market status with reason.

    Returns:
        Tuple of (is_open, reason_string)

    Examples:
        (True, "Market Open")
        (False, "Weekend")
        (False, "After Hours")
        (False, "Holiday")
    """
    now = datetime.now(ET)

    # Check weekend
    if now.weekday() >= 5:
        day_name = "Saturday" if now.weekday() == 5 else "Sunday"
        return (False, f"Weekend ({day_name})")

    # Check holiday
    if (now.year, now.month, now.day) in US_MARKET_HOLIDAYS:
        return (False, "Holiday")

    # Check trading hours
    current_time = now.time()

    if current_time < MARKET_OPEN:
        return (False, "Pre-Market")
    elif current_time >= MARKET_CLOSE:
        return (False, "After Hours")
    else:
        return (True, "Market Open")


def is_trading_day(dt: datetime = None) -> bool:
    """
    Check if the given date is a trading day (not weekend/holiday).

    This is useful for determining if we expect good liquidity data,
    even if markets are currently closed (e.g., after hours on a weekday).

    Args:
        dt: Datetime to check (default: now)

    Returns:
        True if it's a trading day, False if weekend/holiday
    """
    if dt is None:
        dt = datetime.now(ET)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    else:
        dt = dt.astimezone(ET)

    # Weekend check
    if dt.weekday() >= 5:
        return False

    # Holiday check
    if (dt.year, dt.month, dt.day) in US_MARKET_HOLIDAYS:
        return False

    return True


def get_last_trading_day() -> datetime:
    """
    Get the most recent trading day (for stale data reference).

    Returns:
        Datetime of the last trading day at market close
    """
    now = datetime.now(ET)

    # Start from today at market close
    check_date = now.replace(hour=16, minute=0, second=0, microsecond=0)

    # If it's before market close today, use yesterday as reference
    if now.time() < MARKET_CLOSE:
        check_date = check_date - timedelta(days=1)

    # Go back until we find a trading day
    for _ in range(10):  # Max 10 days back (handles holiday clusters)
        if is_trading_day(check_date):
            return check_date
        check_date = check_date - timedelta(days=1)

    # Fallback (shouldn't happen)
    return check_date
