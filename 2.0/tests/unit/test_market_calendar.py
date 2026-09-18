"""The NYSE calendar: one module, pinned to the exchange's published schedules.

Until 2026-09-14 there were three calendars. src/utils/market_hours.py held a
hand-written 2024-2026 holiday list for the market-open check, so from 2027 it
would have called every holiday a session; common/timezone.py held a half-day
list naming a day NYSE never closed early (2026-07-02); and
src/domain/market_calendar.py, the computed one, closed for Juneteenth in 2021,
knew no early close or unscheduled closure, and listed 2027-12-31 among 2028's
holidays for a New Year's Day NYSE does not observe.

The expected dates below are copied from NYSE's own publications, not derived
from the rules under test:

- 2025, 2026, 2027: "NYSE Group Announces 2025, 2026 and 2027 Holiday and Early
  Closings Calendar", 2024-11-08,
  https://ir.theice.com/press/news-details/2024/NYSE-Group-Announces-2025-2026-and-2027-Holiday-and-Early-Closings-Calendar/default.aspx
  -- holiday table and its three early-close footnotes (Thursday, July 3, 2025;
  Friday, November 28, 2025, November 27, 2026 and November 26, 2027; Wednesday,
  December 24, 2025 and Thursday, December 24, 2026).
- 2028 (and 2026-2027 again): https://www.nyse.com/markets/hours-calendars, read
  2026-09-14 -- "Because the holiday falls on Saturday, January 1, 2028, no New
  Year's Day holiday is observed"; early closes Monday, July 3, 2028 and Friday,
  November 24, 2028.
- Unscheduled closures: NYSE, "The New York Stock Exchange Will Close Markets on
  January 9 to Honor the Passing of Former President Jimmy Carter on National
  Day of Mourning", 2024-12-30 (ir.theice.com); George H. W. Bush's day of
  mourning 2018-12-05; Hurricane Sandy 2012-10-29 and 10-30.
- Juneteenth: NYSE first closed for it on Monday, June 20, 2022; it was open on
  Friday, June 18, 2021, the day after the federal holiday was signed.
"""

from datetime import date, datetime, time, timezone
from pathlib import Path

import pytest

from src.domain import market_calendar as mc
from src.domain.market_calendar import (
    get_early_closes,
    get_market_status,
    get_us_market_holidays,
    is_market_holiday,
    is_trading_day,
    next_trading_day,
    previous_trading_day,
    session_close,
)

REPO = Path(__file__).resolve().parents[3]
ET = mc.ET


def _dates(year, *month_days):
    return {date(year, m, d) for m, d in month_days}


PUBLISHED_HOLIDAYS = {
    2025: _dates(2025, (1, 1), (1, 20), (2, 17), (4, 18), (5, 26), (6, 19), (7, 4), (9, 1),
                 (11, 27), (12, 25)),
    2026: _dates(2026, (1, 1), (1, 19), (2, 16), (4, 3), (5, 25), (6, 19), (7, 3), (9, 7),
                 (11, 26), (12, 25)),
    2027: _dates(2027, (1, 1), (1, 18), (2, 15), (3, 26), (5, 31), (6, 18), (7, 5), (9, 6),
                 (11, 25), (12, 24)),
    # New Year's Day falls on Saturday and is not observed: nine holidays.
    2028: _dates(2028, (1, 17), (2, 21), (4, 14), (5, 29), (6, 19), (7, 4), (9, 4),
                 (11, 23), (12, 25)),
}

PUBLISHED_EARLY_CLOSES = {
    2025: _dates(2025, (7, 3), (11, 28), (12, 24)),
    2026: _dates(2026, (11, 27), (12, 24)),   # July 3 is the holiday itself
    2027: _dates(2027, (11, 26)),             # July 3 a Saturday, Dec 24 the holiday
    2028: _dates(2028, (7, 3), (11, 24)),     # Dec 24 a Sunday
}


@pytest.mark.parametrize("year", sorted(PUBLISHED_HOLIDAYS))
def test_full_day_closures_match_nyse_published_schedule(year):
    assert get_us_market_holidays(year) == PUBLISHED_HOLIDAYS[year]


@pytest.mark.parametrize("year", sorted(PUBLISHED_EARLY_CLOSES))
def test_early_closes_match_nyse_published_schedule(year):
    assert get_early_closes(year) == PUBLISHED_EARLY_CLOSES[year]


def test_a_saturday_new_years_day_closes_nothing():
    assert date(2027, 12, 31) not in get_us_market_holidays(2027)
    assert date(2027, 12, 31) not in get_us_market_holidays(2028)
    assert is_trading_day(date(2027, 12, 31))
    assert is_trading_day(date(2021, 12, 31))          # New Year's Day 2022, a Saturday


def test_juneteenth_closes_from_2022():
    assert is_trading_day(date(2021, 6, 18))
    assert is_market_holiday(date(2022, 6, 20))


@pytest.mark.parametrize("day", [date(2012, 10, 29), date(2012, 10, 30), date(2018, 12, 5),
                                 date(2025, 1, 9)])
def test_unscheduled_closures_are_not_sessions(day):
    assert is_market_holiday(day)
    assert not is_trading_day(day)
    assert day not in get_us_market_holidays(day.year)   # not on any published schedule


def test_sessions_step_over_weekends_and_closures():
    assert next_trading_day(date(2025, 1, 8)) == date(2025, 1, 10)     # Carter
    assert next_trading_day(date(2026, 9, 4)) == date(2026, 9, 8)      # Labor Day weekend
    assert previous_trading_day(date(2026, 9, 8)) == date(2026, 9, 4)
    assert previous_trading_day(date(2026, 1, 2)) == date(2025, 12, 31)
    assert not is_trading_day(date(2026, 9, 12))                        # Saturday


def test_session_close_is_one_pm_on_an_early_close():
    assert session_close(date(2026, 11, 27)) == time(13, 0)
    assert session_close(date(2026, 11, 25)) == time(16, 0)
    assert session_close(date(2026, 11, 26)) is None                    # Thanksgiving
    assert session_close(date(2026, 11, 28)) is None                    # Saturday


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 11, 27, 12, 59, tzinfo=ET), (True, "Market Open")),
    (datetime(2026, 11, 27, 13, 0, tzinfo=ET), (False, "After Hours (early close 13:00)")),
    (datetime(2026, 11, 25, 15, 59, tzinfo=ET), (True, "Market Open")),
    (datetime(2026, 11, 25, 16, 0, tzinfo=ET), (False, "After Hours")),
    (datetime(2026, 11, 25, 9, 29, tzinfo=ET), (False, "Pre-Market")),
    (datetime(2026, 11, 26, 11, 0, tzinfo=ET), (False, "Holiday")),
    (datetime(2027, 3, 26, 11, 0, tzinfo=ET), (False, "Holiday")),     # past the old list
    (datetime(2025, 1, 9, 11, 0, tzinfo=ET), (False, "Holiday")),
    (datetime(2026, 9, 12, 11, 0, tzinfo=ET), (False, "Weekend (Saturday)")),
    # 18:30 UTC on Christmas Eve is 13:30 in New York, after the early close.
    (datetime(2026, 12, 24, 18, 30, tzinfo=timezone.utc), (False, "After Hours (early close 13:00)")),
])
def test_market_status(now, expected):
    assert get_market_status(now) == expected


def test_market_status_reads_the_new_york_clock(monkeypatch):
    monkeypatch.setattr(mc, "now_et", lambda: datetime(2026, 11, 27, 14, 0, tzinfo=ET))
    assert get_market_status() == (False, "After Hours (early close 13:00)")


def test_there_is_one_calendar():
    """No second holiday or half-day list, and nothing imports the old module."""
    assert not (REPO / "2.0" / "src" / "utils" / "market_hours.py").exists()
    offenders = []
    for root in ("2.0/src", "2.0/scripts", "common", "scripts"):
        for path in (REPO / root).rglob("*"):
            if (path.suffix not in {".py", ".md", ".sh"} or {"tests", "venv"} & set(path.parts)
                    or path.name == "market_calendar.py"):    # which tells the history
                continue
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if any(name in line for name in ("US_MARKET_HOLIDAYS", "HALF_DAYS", "is_half_day",
                                                 "market_hours")):
                    offenders.append(f"{path.relative_to(REPO)}:{i}: {line.strip()}")
    assert offenders == []
