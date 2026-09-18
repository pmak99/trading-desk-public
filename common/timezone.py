"""The one market clock for Trading Desk: New York time, whatever the host's.

CRITICAL: All trading times are Eastern Time (ET), and the server's clock is UTC.
Every read of the current time or date in trading code goes through now_et() or
today_et() (scripts/tests/test_market_dates.py fails on a naive read).

MARKET_TZ is a zoneinfo zone, safe in `datetime(..., tzinfo=MARKET_TZ)` and
`.replace(tzinfo=MARKET_TZ)`. It was a pytz zone until 2026-09-14, which in either
of those positions silently applies New York's 1883 local mean time (-4:56).
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")


def now_et() -> datetime:
    """The current instant, aware, in New York time."""
    return datetime.now(MARKET_TZ)


def today_et() -> date:
    """The trading day: today's date in New York, whatever the host clock says.

    Use this, never date.today() or datetime.now(). the server's clock is UTC, so from
    20:00 ET (19:00 in winter) to midnight its date.today() is already tomorrow
    (scripts/tests/test_market_dates.py fails on a new one).
    """
    return now_et().date()
