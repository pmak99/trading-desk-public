"""Shared timezone utilities for Trading Desk.

CRITICAL: All trading times are Eastern Time (ET).
Cloud Run defaults to UTC, so explicit timezone handling is required.
"""

from datetime import datetime

import pytz

MARKET_TZ = pytz.timezone('America/New_York')


def now_et() -> datetime:
    """Get current time in Eastern timezone."""
    return datetime.now(MARKET_TZ)


def today_et() -> str:
    """Get today's date in Eastern as YYYY-MM-DD."""
    return now_et().strftime('%Y-%m-%d')


# Market half-days (close at 1 PM ET)
HALF_DAYS = {
    "2025-07-03",   # Day before July 4th
    "2025-11-28",   # Day after Thanksgiving
    "2025-12-24",   # Christmas Eve
    "2026-07-02",   # Day before July 4th (2026)
    "2026-11-27",   # Day after Thanksgiving (2026)
    "2026-12-24",   # Christmas Eve (2026)
}


def is_half_day(date_str: str = None) -> bool:
    """Check if a date is a market half-day."""
    if date_str is None:
        date_str = today_et()
    return date_str in HALF_DAYS
