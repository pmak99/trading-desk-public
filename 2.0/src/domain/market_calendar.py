"""The NYSE calendar: which dates are sessions, and when each one closes.

The one calendar in the repo. The trading expiration (trading_expiration.py) is
built on it, record_moves and rule_evidence ask it whether a date is a session,
and the liquidity scorers and TACO ask get_market_status() whether the market is
open now. tests/unit/test_market_calendar.py pins it to NYSE's published
2025-2028 schedules.

Until 2026-09-14 there were three. src/utils/market_hours.py held a hand-written
2024-2026 holiday list for the open/closed check, so from 2027 it would have
called every holiday a session; common/timezone.py held a half-day list naming a
day NYSE never closed early (2026-07-02); and this module closed for Juneteenth
in 2021, knew no early close or unscheduled closure, and listed 2027-12-31 among
2028's holidays for a New Year's Day NYSE does not observe. Moved from
scripts/scan/date_utils.py on 2026-09-14 so src never imports the scripts tree.
"""

from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import FrozenSet, Optional, Tuple

from common.timezone import MARKET_TZ as ET
from common.timezone import now_et

MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)
EARLY_CLOSE = time(13, 0)

# Most days adjust_to_trading_day steps over (a weekend beside a holiday).
MAX_TRADING_DAY_ITERATIONS = 10

# Full-day closures no schedule announced, from where historical_moves begins
# (2007-07). Add the next one when NYSE announces it.
UNSCHEDULED_CLOSURES: FrozenSet[date] = frozenset({
    date(2012, 10, 29), date(2012, 10, 30),   # Hurricane Sandy
    date(2018, 12, 5),                        # National Day of Mourning, George H. W. Bush
    date(2025, 1, 9),                         # National Day of Mourning, Jimmy Carter
})

_MONDAY, _THURSDAY, _FRIDAY, _SATURDAY, _SUNDAY = 0, 3, 4, 5, 6


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7, weeks=n - 1)


def _last_monday_of_may(year: int) -> date:
    may_31 = date(year, 5, 31)
    return may_31 - timedelta(days=may_31.weekday())


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian algorithm."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _observed(holiday: date) -> date:
    """A Saturday holiday closes the Friday before, a Sunday one the Monday after."""
    if holiday.weekday() == _SATURDAY:
        return holiday - timedelta(days=1)
    if holiday.weekday() == _SUNDAY:
        return holiday + timedelta(days=1)
    return holiday


@lru_cache(maxsize=None)
def get_us_market_holidays(year: int) -> FrozenSet[date]:
    """NYSE's scheduled full-day closures observed in `year`.

    A holiday on a Saturday closes the Friday before, one on a Sunday the Monday
    after -- except New Year's Day, which on a Saturday is not observed at all
    (NYSE: "Because the holiday falls on Saturday, January 1, 2028, no New Year's
    Day holiday is observed"), so the set never holds another year's date.
    Unscheduled closures are UNSCHEDULED_CLOSURES, not here.
    """
    holidays = {
        _nth_weekday(year, 1, _MONDAY, 3),                 # Martin Luther King, Jr. Day
        _nth_weekday(year, 2, _MONDAY, 3),                 # Washington's Birthday
        _easter_sunday(year) - timedelta(days=2),          # Good Friday
        _last_monday_of_may(year),                         # Memorial Day
        _observed(date(year, 7, 4)),                       # Independence Day
        _nth_weekday(year, 9, _MONDAY, 1),                 # Labor Day
        _nth_weekday(year, 11, _THURSDAY, 4),              # Thanksgiving Day
        _observed(date(year, 12, 25)),                     # Christmas Day
    }
    new_years = date(year, 1, 1)
    if new_years.weekday() != _SATURDAY:
        holidays.add(_observed(new_years))
    if year >= 2022:                                        # first closed 2022-06-20
        holidays.add(_observed(date(year, 6, 19)))         # Juneteenth
    return frozenset(holidays)


@lru_cache(maxsize=None)
def get_early_closes(year: int) -> FrozenSet[date]:
    """NYSE's 1:00 p.m. closes in `year`.

    July 3 and December 24 when they fall Monday-Thursday (on a Friday each is
    the observed holiday itself), and the day after Thanksgiving.
    """
    closes = {_nth_weekday(year, 11, _THURSDAY, 4) + timedelta(days=1)}
    for eve in (date(year, 7, 3), date(year, 12, 24)):
        if eve.weekday() <= _THURSDAY:
            closes.add(eve)
    return frozenset(closes)


def is_market_holiday(target_date: date) -> bool:
    """True when NYSE is closed all day on a weekday: a scheduled holiday or an
    unscheduled closure."""
    return (target_date in get_us_market_holidays(target_date.year)
            or target_date in UNSCHEDULED_CLOSURES)


def is_trading_day(target_date: date) -> bool:
    """True when NYSE holds a session on the date, early closes included."""
    return target_date.weekday() < _SATURDAY and not is_market_holiday(target_date)


def next_trading_day(target_date: date) -> date:
    """The first session after the date."""
    d = target_date + timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def previous_trading_day(target_date: date) -> date:
    """The last session before the date."""
    d = target_date - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def session_close(target_date: date) -> Optional[time]:
    """The regular session's close in New York time; None when there is no session."""
    if not is_trading_day(target_date):
        return None
    return EARLY_CLOSE if target_date in get_early_closes(target_date.year) else MARKET_CLOSE


def get_market_status(now: Optional[datetime] = None) -> Tuple[bool, str]:
    """(is the regular session open, why) at `now`, default the New York clock.

    A naive `now` is taken as New York time. The reasons: "Market Open",
    "Weekend (Saturday)", "Holiday", "Pre-Market", "After Hours", and
    "After Hours (early close 13:00)" from an early close to 16:00 and after.
    """
    if now is None:
        now = now_et()
    elif now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    else:
        now = now.astimezone(ET)

    today = now.date()
    if today.weekday() >= _SATURDAY:
        return (False, f"Weekend ({'Saturday' if today.weekday() == _SATURDAY else 'Sunday'})")
    close = session_close(today)
    if close is None:
        return (False, "Holiday")
    if now.time() < MARKET_OPEN:
        return (False, "Pre-Market")
    if now.time() >= close:
        suffix = f" (early close {close:%H:%M})" if close != MARKET_CLOSE else ""
        return (False, f"After Hours{suffix}")
    return (True, "Market Open")


def adjust_to_trading_day(target_date: date) -> date:
    """The date itself when it is a session, else the next session."""
    adjusted = target_date
    for _ in range(MAX_TRADING_DAY_ITERATIONS):
        if is_trading_day(adjusted):
            break
        adjusted += timedelta(days=1)
    return adjusted
