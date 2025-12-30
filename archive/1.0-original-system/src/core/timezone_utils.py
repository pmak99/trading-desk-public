"""
Timezone utilities for consistent datetime handling.

All market-related times should use US/Eastern (NYSE timezone).
"""

import pytz
from datetime import datetime
from typing import Optional


# Market timezone constant
EASTERN = pytz.timezone('US/Eastern')


def get_eastern_now() -> datetime:
    """
    Get current time in US/Eastern (market timezone).

    Returns:
        Timezone-aware datetime object in US/Eastern
    """
    return datetime.now(EASTERN)


def to_eastern(dt: datetime) -> datetime:
    """
    Convert datetime to US/Eastern timezone.

    Args:
        dt: Datetime object (naive or aware)

    Returns:
        Timezone-aware datetime in US/Eastern
    """
    if dt.tzinfo is None:
        # Naive datetime - assume UTC
        dt = pytz.UTC.localize(dt)

    return dt.astimezone(EASTERN)


def get_market_date() -> str:
    """
    Get current market date (YYYY-MM-DD) in Eastern timezone.

    Important: Market date changes at midnight ET, not local time.

    Returns:
        Date string in YYYY-MM-DD format
    """
    return get_eastern_now().strftime('%Y-%m-%d')


def is_market_hours() -> bool:
    """
    Check if currently in regular market hours (9:30 AM - 4:00 PM ET, Mon-Fri).

    Returns:
        True if in market hours, False otherwise
    """
    now_et = get_eastern_now()

    # Check if weekday (Mon-Fri)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False

    # Check if in market hours (9:30 AM - 4:00 PM ET)
    market_open = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
    market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)

    return market_open <= now_et <= market_close


def is_after_hours() -> bool:
    """
    Check if currently in after-hours (4:00 PM - 8:00 PM ET).

    Returns:
        True if in after-hours, False otherwise
    """
    now_et = get_eastern_now()

    # Check if weekday
    if now_et.weekday() >= 5:
        return False

    # Check if in after-hours (4:00 PM - 8:00 PM ET)
    after_hours_start = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
    after_hours_end = now_et.replace(hour=20, minute=0, second=0, microsecond=0)

    return after_hours_start <= now_et <= after_hours_end
