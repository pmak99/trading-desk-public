"""
Market calendar using pandas-market-calendars for comprehensive holiday data.

Provides accurate US market holidays and trading days using the industry-standard
pandas_market_calendars library.

Installation:
    pip install pandas-market-calendars

Usage:
    from src.data.calendars.market_calendar import MarketCalendarClient
    calendar = MarketCalendarClient()
    is_trading = calendar.is_trading_day(date)
"""

import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import pytz

logger = logging.getLogger(__name__)

try:
    import pandas_market_calendars as mcal
    PANDAS_MARKET_CALENDARS_AVAILABLE = True
except ImportError:
    PANDAS_MARKET_CALENDARS_AVAILABLE = False
    logger.warning(
        "pandas-market-calendars not installed. "
        "Install with: pip install pandas-market-calendars"
    )


class MarketCalendarClient:
    """
    Market calendar using pandas-market-calendars.

    Provides accurate trading days and holidays for US markets (NYSE).

    Features:
    - Accurate market holidays (no manual updates needed)
    - Half-day detection (early close on certain holidays)
    - Special trading hours
    - Historical data back to 1962

    Fallback: If pandas-market-calendars not installed, falls back to basic logic.
    """

    def __init__(self, exchange: str = 'NYSE'):
        """
        Initialize market calendar.

        Args:
            exchange: Exchange name (default: NYSE)
                Options: NYSE, NASDAQ, CME, etc.
        """
        self.exchange = exchange
        self.eastern = pytz.timezone('US/Eastern')

        if PANDAS_MARKET_CALENDARS_AVAILABLE:
            try:
                self.calendar = mcal.get_calendar(exchange)
                logger.info(f"✅ Using pandas-market-calendars for {exchange}")
                self._use_library = True
            except Exception as e:
                logger.warning(f"Failed to load {exchange} calendar: {e}, using fallback")
                self._use_library = False
        else:
            self._use_library = False
            logger.info("Using fallback calendar (basic logic)")

    def is_trading_day(self, date: datetime) -> bool:
        """
        Check if date is a trading day.

        Args:
            date: Date to check

        Returns:
            True if market is open, False otherwise
        """
        if self._use_library:
            return self._is_trading_day_library(date)
        else:
            return self._is_trading_day_fallback(date)

    def _is_trading_day_library(self, date: datetime) -> bool:
        """Check trading day using pandas-market-calendars."""
        try:
            # Get schedule for the date
            schedule = self.calendar.schedule(
                start_date=date.date(),
                end_date=date.date()
            )

            # If schedule is empty, market is closed
            return not schedule.empty

        except Exception as e:
            logger.warning(f"Error checking trading day: {e}, using fallback")
            return self._is_trading_day_fallback(date)

    def _is_trading_day_fallback(self, date: datetime) -> bool:
        """
        Fallback: Basic trading day check (weekends only).

        Note: Does NOT account for holidays - use pandas-market-calendars for accuracy.
        """
        # Only check weekends (Monday=0, Sunday=6)
        return date.weekday() < 5

    def get_next_trading_day(self, date: datetime) -> datetime:
        """
        Get next trading day after given date.

        Args:
            date: Starting date

        Returns:
            Next trading day
        """
        if self._use_library:
            return self._get_next_trading_day_library(date)
        else:
            return self._get_next_trading_day_fallback(date)

    def _get_next_trading_day_library(self, date: datetime) -> datetime:
        """Get next trading day using library."""
        try:
            # Get valid trading days for next 10 days
            end_date = date + timedelta(days=10)
            schedule = self.calendar.schedule(
                start_date=date.date(),
                end_date=end_date.date()
            )

            if schedule.empty:
                # Fallback to simple logic
                return self._get_next_trading_day_fallback(date)

            # First trading day after (or on) given date
            next_day = schedule.index[0]
            return datetime.combine(next_day.date(), datetime.min.time())

        except Exception as e:
            logger.warning(f"Error getting next trading day: {e}")
            return self._get_next_trading_day_fallback(date)

    def _get_next_trading_day_fallback(self, date: datetime) -> datetime:
        """Fallback: Simple next weekday logic."""
        next_day = date + timedelta(days=1)

        # Skip weekends
        while next_day.weekday() >= 5:
            next_day += timedelta(days=1)

        return next_day

    def get_trading_hours(self, date: datetime) -> Optional[Dict]:
        """
        Get market open/close times for a date.

        Args:
            date: Date to check

        Returns:
            Dict with 'open' and 'close' times, or None if closed
        """
        if not self._use_library:
            # Fallback: Standard hours
            if self.is_trading_day(date):
                return {
                    'open': datetime.combine(date.date(), datetime.strptime('09:30', '%H:%M').time()),
                    'close': datetime.combine(date.date(), datetime.strptime('16:00', '%H:%M').time()),
                    'early_close': False
                }
            return None

        try:
            schedule = self.calendar.schedule(
                start_date=date.date(),
                end_date=date.date()
            )

            if schedule.empty:
                return None

            row = schedule.iloc[0]
            open_time = row['market_open'].to_pydatetime()
            close_time = row['market_close'].to_pydatetime()

            # Detect early close (before 4 PM ET)
            standard_close = datetime.combine(date.date(), datetime.strptime('16:00', '%H:%M').time())
            standard_close = self.eastern.localize(standard_close)

            early_close = close_time < standard_close

            return {
                'open': open_time,
                'close': close_time,
                'early_close': early_close
            }

        except Exception as e:
            logger.warning(f"Error getting trading hours: {e}")
            return None

    def get_holidays(self, year: int) -> List[datetime]:
        """
        Get all market holidays for a year.

        Args:
            year: Year to get holidays for

        Returns:
            List of holiday dates
        """
        if not self._use_library:
            logger.warning("Using fallback - holidays not available without pandas-market-calendars")
            return []

        try:
            # Get all days in the year
            start_date = datetime(year, 1, 1)
            end_date = datetime(year, 12, 31)

            # Get valid trading days
            schedule = self.calendar.schedule(
                start_date=start_date,
                end_date=end_date
            )

            # Get all weekdays in year
            all_weekdays = []
            current = start_date
            while current <= end_date:
                if current.weekday() < 5:  # Monday-Friday
                    all_weekdays.append(current.date())
                current += timedelta(days=1)

            # Trading days
            trading_days = set(schedule.index.date)

            # Holidays = weekdays that aren't trading days
            holidays = [
                datetime.combine(d, datetime.min.time())
                for d in all_weekdays
                if d not in trading_days
            ]

            return holidays

        except Exception as e:
            logger.warning(f"Error getting holidays: {e}")
            return []


# Example usage and testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    calendar = MarketCalendarClient()

    print("\n" + "="*70)
    print("MARKET CALENDAR TESTING")
    print("="*70 + "\n")

    # Test 1: Is today a trading day?
    today = datetime.now()
    is_trading = calendar.is_trading_day(today)
    print(f"Is today ({today.date()}) a trading day? {is_trading}")

    # Test 2: Next trading day
    next_trading = calendar.get_next_trading_day(today)
    print(f"Next trading day: {next_trading.date()}")

    # Test 3: Trading hours
    hours = calendar.get_trading_hours(today)
    if hours:
        print(f"Trading hours today:")
        print(f"  Open:  {hours['open'].strftime('%I:%M %p %Z')}")
        print(f"  Close: {hours['close'].strftime('%I:%M %p %Z')}")
        if hours['early_close']:
            print(f"  ⚠️  EARLY CLOSE")
    else:
        print(f"Market closed today")

    # Test 4: 2025 holidays
    if PANDAS_MARKET_CALENDARS_AVAILABLE:
        holidays_2025 = calendar.get_holidays(2025)
        print(f"\n2025 Market Holidays ({len(holidays_2025)} days):")
        for holiday in holidays_2025[:10]:  # First 10
            print(f"  - {holiday.strftime('%Y-%m-%d %A')}")
        if len(holidays_2025) > 10:
            print(f"  ... and {len(holidays_2025) - 10} more")
    else:
        print("\n⚠️  Install pandas-market-calendars for holiday data:")
        print("   pip install pandas-market-calendars")

    print("\n" + "="*70)
