"""
Trading day calculations and expiration date logic.

Provides date parsing, holiday detection, and options expiration calculation.
"""

from datetime import date, datetime, timedelta
from typing import Dict, Optional, Set

from src.domain.enums import EarningsTiming

from .constants import MAX_TRADING_DAY_ITERATIONS

# Module-level cache for holidays
_holiday_cache: Dict[int, set] = {}


def parse_date(date_str: str) -> date:
    """Parse date string in ISO format."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as e:
        raise ValueError(f"Invalid date format: {date_str}. Use YYYY-MM-DD")


def get_us_market_holidays(year: int) -> set:
    """
    Get US stock market holidays for a given year (with caching).

    Returns fixed-date holidays and approximations for floating holidays.
    Note: Good Friday requires Easter calculation which is complex,
    so it's omitted here. For production, consider using a library like
    pandas_market_calendars or exchange_calendars.

    Args:
        year: The year to get holidays for

    Returns:
        Set of date objects representing market holidays
    """
    # Check cache first
    if year in _holiday_cache:
        return _holiday_cache[year]

    holidays = set()

    # Fixed holidays
    # New Year's Day (Jan 1)
    new_years = date(year, 1, 1)
    if new_years.weekday() == 5:  # Saturday -> observed Friday
        holidays.add(date(year - 1, 12, 31))
    elif new_years.weekday() == 6:  # Sunday -> observed Monday
        holidays.add(date(year, 1, 2))
    else:
        holidays.add(new_years)

    # Juneteenth (June 19) - observed since 2021
    if year >= 2021:
        juneteenth = date(year, 6, 19)
        if juneteenth.weekday() == 5:
            holidays.add(date(year, 6, 18))
        elif juneteenth.weekday() == 6:
            holidays.add(date(year, 6, 20))
        else:
            holidays.add(juneteenth)

    # Independence Day (July 4)
    july_4th = date(year, 7, 4)
    if july_4th.weekday() == 5:
        holidays.add(date(year, 7, 3))
    elif july_4th.weekday() == 6:
        holidays.add(date(year, 7, 5))
    else:
        holidays.add(july_4th)

    # Christmas Day (Dec 25)
    christmas = date(year, 12, 25)
    if christmas.weekday() == 5:
        holidays.add(date(year, 12, 24))
    elif christmas.weekday() == 6:
        holidays.add(date(year, 12, 26))
    else:
        holidays.add(christmas)

    # Floating holidays (approximations)
    # MLK Day (3rd Monday of January)
    jan_first = date(year, 1, 1)
    days_to_monday = (7 - jan_first.weekday()) % 7
    first_monday = jan_first + timedelta(days=days_to_monday)
    mlk_day = first_monday + timedelta(weeks=2)
    holidays.add(mlk_day)

    # Presidents' Day (3rd Monday of February)
    feb_first = date(year, 2, 1)
    days_to_monday = (7 - feb_first.weekday()) % 7
    first_monday = feb_first + timedelta(days=days_to_monday)
    presidents_day = first_monday + timedelta(weeks=2)
    holidays.add(presidents_day)

    # Memorial Day (last Monday of May)
    may_last = date(year, 5, 31)
    days_since_monday = may_last.weekday()
    memorial_day = may_last - timedelta(days=days_since_monday)
    holidays.add(memorial_day)

    # Labor Day (1st Monday of September)
    sep_first = date(year, 9, 1)
    days_to_monday = (7 - sep_first.weekday()) % 7
    labor_day = sep_first + timedelta(days=days_to_monday)
    holidays.add(labor_day)

    # Thanksgiving (4th Thursday of November)
    nov_first = date(year, 11, 1)
    days_to_thursday = (3 - nov_first.weekday()) % 7
    first_thursday = nov_first + timedelta(days=days_to_thursday)
    thanksgiving = first_thursday + timedelta(weeks=3)
    holidays.add(thanksgiving)

    # Cache the result
    _holiday_cache[year] = holidays
    return holidays


def is_market_holiday(target_date: date) -> bool:
    """
    Check if a date is a US stock market holiday.

    Args:
        target_date: Date to check

    Returns:
        True if the date is a market holiday
    """
    holidays = get_us_market_holidays(target_date.year)
    return target_date in holidays


def adjust_to_trading_day(target_date: date) -> date:
    """
    Adjust date to next trading day if on weekend or holiday.

    Args:
        target_date: Target date to check

    Returns:
        Next trading day (skips weekends and US market holidays)
    """
    adjusted = target_date

    # Keep adjusting until we find a trading day
    for _ in range(MAX_TRADING_DAY_ITERATIONS):
        weekday = adjusted.weekday()

        # Skip weekends
        if weekday == 5:  # Saturday -> Monday
            adjusted = adjusted + timedelta(days=2)
            continue
        elif weekday == 6:  # Sunday -> Monday
            adjusted = adjusted + timedelta(days=1)
            continue

        # Skip market holidays
        if is_market_holiday(adjusted):
            adjusted = adjusted + timedelta(days=1)
            continue

        # Found a trading day
        break

    return adjusted


def get_next_friday(from_date: date) -> date:
    """Get the next Friday from the given date."""
    days_until_friday = (4 - from_date.weekday()) % 7
    if days_until_friday == 0:
        # If today is Friday, get next Friday
        days_until_friday = 7
    return from_date + timedelta(days=days_until_friday)


def calculate_implied_move_expiration(earnings_date: date) -> date:
    """
    Calculate the expiration date for implied move calculation.

    For IV crush analysis, we always use the FIRST expiration after earnings
    to capture the pure implied volatility that will collapse post-earnings.

    Args:
        earnings_date: Date of earnings announcement

    Returns:
        First trading day after earnings (adjusted for weekends)

    Note:
        This differs from trading expiration (which may use Fridays for
        liquidity). Implied move must use first post-earnings expiration
        to accurately measure the volatility being priced in.
    """
    # Always use earnings_date + 1 day, adjusted to trading day
    next_day = earnings_date + timedelta(days=1)
    return adjust_to_trading_day(next_day)


def calculate_expiration_date(
    earnings_date: date,
    timing: EarningsTiming,
    offset_days: Optional[int] = None
) -> date:
    """
    Calculate expiration date for TRADING purposes (liquidity, strategy).

    Args:
        earnings_date: Date of earnings announcement
        timing: BMO (before market open), AMC (after market close), or UNKNOWN
        offset_days: Optional custom offset in days from earnings date

    Returns:
        Expiration date for options (adjusted to trading day if needed)

    Strategy (aligned with user's trading workflow):
        - Mon/Tue/Wed earnings -> Friday of same week
        - Thu/Fri earnings -> Friday 1 week out (avoid 0DTE risk)
        - Custom offset: earnings_date + offset_days (adjusted to trading day)

    User enters positions at 3-4pm on earnings day (or day before for BMO),
    exits next trading day at 9:30-10:30am, using Friday weekly expirations.

    Note:
        For implied move calculation, use calculate_implied_move_expiration()
        instead - it always uses first post-earnings expiration.
    """
    if offset_days is not None:
        target_date = earnings_date + timedelta(days=offset_days)
        return adjust_to_trading_day(target_date)

    # User strategy: Thursday or Friday earnings -> Use Friday 1 week out
    # This avoids 0DTE risk and provides buffer for exit
    weekday = earnings_date.weekday()

    if weekday in [3, 4]:  # Thursday or Friday
        # Use next Friday (1 week out)
        if weekday == 3:  # Thursday
            return earnings_date + timedelta(days=8)  # Thu + 8 = next Fri
        else:  # Friday
            return earnings_date + timedelta(days=7)  # Fri + 7 = next Fri

    # Mon/Tue/Wed: Use Friday of same week
    return get_next_friday(earnings_date)


def validate_expiration_date(
    expiration_date: date,
    earnings_date: date,
    ticker: str
) -> Optional[str]:
    """
    Validate expiration date is reasonable for trading.

    Args:
        expiration_date: Calculated expiration date
        earnings_date: Earnings announcement date
        ticker: Ticker symbol (for logging)

    Returns:
        Error message if invalid, None if valid
    """
    today = date.today()

    # Check if expiration is in the past
    if expiration_date < today:
        return f"Expiration date {expiration_date} is in the past (today: {today})"

    # Check if expiration is before earnings
    if expiration_date < earnings_date:
        return f"Expiration {expiration_date} is before earnings {earnings_date}"

    # Check if expiration is on weekend (should have been adjusted, but double-check)
    if expiration_date.weekday() in [5, 6]:
        return f"Expiration date {expiration_date} is on weekend (programming error)"

    # Check if expiration is too far in future (> 30 days from earnings)
    days_after_earnings = (expiration_date - earnings_date).days
    if days_after_earnings > 30:
        return f"Expiration is {days_after_earnings} days after earnings (> 30 days, likely error)"

    return None
