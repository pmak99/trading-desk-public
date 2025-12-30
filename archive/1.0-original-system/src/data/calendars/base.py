"""
Earnings calendar scanner using Nasdaq API.
Gets actual earnings calendar for specified date range.

Filters out already-reported earnings based on current time:
- Pre-market hours: Include both pre/post for today
- Market hours or after: Only post-market for today
- Always include future dates
"""

import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging
import pytz

logger = logging.getLogger(__name__)


class EarningsCalendar:
    """Scan earnings calendar from Nasdaq."""

    def __init__(self):
        """Initialize earnings calendar scanner."""
        self.base_url = 'https://api.nasdaq.com/api/calendar/earnings'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }
        # Eastern timezone for market hours
        self.eastern = pytz.timezone('US/Eastern')

    def _is_already_reported(self, earning: Dict, now_et: datetime) -> bool:
        """
        Check if earnings have already been reported.

        Args:
            earning: Earnings dict with 'date' and 'time' fields
            now_et: Current time in Eastern timezone

        Returns:
            True if already reported, False if still upcoming
        """
        earning_date = datetime.strptime(earning['date'], '%Y-%m-%d').date()
        today = now_et.date()

        # Future dates are never reported yet
        if earning_date > today:
            return False

        # Past dates are always reported
        if earning_date < today:
            return True

        # Today - check the time
        earning_time = earning.get('time', '').lower()
        current_hour = now_et.hour
        current_minute = now_et.minute

        # Market opens at 9:30 AM ET
        market_open = current_hour > 9 or (current_hour == 9 and current_minute >= 30)

        # If before market open, nothing is reported yet today
        if not market_open:
            return False

        # After market open, pre-market earnings are already reported
        if 'before' in earning_time or 'pre' in earning_time:
            return True

        # Post-market earnings haven't reported yet (report after 4pm)
        return False

    def _is_weekend_or_holiday(self, date: datetime) -> bool:
        """
        Check if date is weekend or major market holiday.

        Args:
            date: Date to check

        Returns:
            True if weekend or holiday, False otherwise
        """
        # Check if weekend (Saturday=5, Sunday=6)
        if date.weekday() >= 5:
            return True

        # Major market holidays 2025 (extend as needed)
        # Format: (month, day)
        holidays_2025 = [
            (1, 1),    # New Year's Day
            (1, 20),   # MLK Day
            (2, 17),   # Presidents Day
            (4, 18),   # Good Friday
            (5, 26),   # Memorial Day
            (7, 4),    # Independence Day
            (9, 1),    # Labor Day
            (11, 27),  # Thanksgiving
            (12, 25),  # Christmas
        ]

        date_tuple = (date.month, date.day)
        if date_tuple in holidays_2025:
            return True

        return False

    def get_earnings_for_date(self, date: datetime) -> List[Dict]:
        """
        Get earnings for a specific date.

        Args:
            date: Date to get earnings for

        Returns:
            List of earnings dicts with ticker, company, time, etc.
        """
        date_str = date.strftime('%Y-%m-%d')
        params = {'date': date_str}

        try:
            response = requests.get(
                self.base_url,
                headers=self.headers,
                params=params,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            if 'data' not in data or 'rows' not in data.get('data', {}):
                logger.warning(f"No earnings data for {date_str}")
                return []

            rows = data['data'].get('rows')
            if not rows or rows is None:
                logger.warning(f"Empty earnings data for {date_str}")
                return []

            earnings = []
            for row in rows:
                # Extract relevant fields
                ticker = row.get('symbol', '').strip()
                company = row.get('companyName', 'Unknown')
                market_cap = row.get('marketCap', 0)
                time = row.get('time', 'N/A')  # Before/After market

                if ticker:  # Only include if ticker exists
                    earnings.append({
                        'ticker': ticker,
                        'company': company,
                        'date': date_str,
                        'time': time,
                        'market_cap': market_cap,
                        'source': 'nasdaq'
                    })

            return earnings

        except requests.RequestException as e:
            logger.error(f"Error fetching earnings for {date_str}: {e}")
            return []
        except (KeyError, ValueError) as e:
            logger.error(f"Error parsing earnings data for {date_str}: {e}")
            return []

    def get_week_earnings(
        self,
        start_date: Optional[datetime] = None,
        days: int = 7,
        skip_weekends: bool = True
    ) -> Dict[str, List[Dict]]:
        """
        Get earnings for a week, skipping weekends and holidays.

        Args:
            start_date: Start date (defaults to today)
            days: Number of trading days to scan (default 7)
            skip_weekends: Skip weekends and holidays (default True)

        Returns:
            Dict mapping date strings to lists of earnings
        """
        if start_date is None:
            start_date = datetime.now()

        week_earnings = {}
        days_scanned = 0
        offset = 0

        while days_scanned < days and offset < days * 2:  # Safety limit
            date = start_date + timedelta(days=offset)
            offset += 1

            # Skip weekends and holidays if requested
            if skip_weekends and self._is_weekend_or_holiday(date):
                logger.debug(f"Skipping {date.strftime('%Y-%m-%d')} (weekend/holiday)")
                continue

            date_str = date.strftime('%Y-%m-%d')
            day_name = date.strftime('%A')

            logger.info(f"Fetching earnings for {day_name}, {date_str}")
            earnings = self.get_earnings_for_date(date)

            if earnings:
                week_earnings[date_str] = earnings
                logger.info(f"Found {len(earnings)} earnings for {date_str}")

            days_scanned += 1

        return week_earnings

    def get_filtered_earnings(
        self,
        days: int = 7,
        min_market_cap: Optional[float] = None,
        tickers: Optional[List[str]] = None,
        filter_reported: bool = True
    ) -> List[Dict]:
        """
        Get filtered earnings for date range.

        Args:
            days: Number of trading days to scan
            min_market_cap: Minimum market cap filter (optional)
            tickers: List of specific tickers to filter for (optional)
            filter_reported: Filter out already-reported earnings (default True)

        Returns:
            Flat list of filtered earnings
        """
        week_earnings = self.get_week_earnings(days=days, skip_weekends=True)

        # Get current time in Eastern timezone
        now_et = datetime.now(self.eastern)

        all_earnings = []
        filtered_count = 0

        for date_str, earnings_list in week_earnings.items():
            for earning in earnings_list:
                # Filter already-reported earnings
                if filter_reported and self._is_already_reported(earning, now_et):
                    filtered_count += 1
                    logger.debug(f"Filtered {earning['ticker']} - already reported")
                    continue

                # Apply other filters
                if tickers and earning['ticker'] not in tickers:
                    continue

                if min_market_cap and earning.get('market_cap', 0) < min_market_cap:
                    continue

                all_earnings.append(earning)

        if filtered_count > 0:
            logger.info(f"Filtered out {filtered_count} already-reported earnings")

        # Sort by date
        all_earnings.sort(key=lambda x: x['date'])

        return all_earnings


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    from datetime import datetime

    calendar = EarningsCalendar()

    logger.info("")
    logger.info('='*70)
    logger.info('EARNINGS CALENDAR - THIS WEEK')
    logger.info('='*70)
    logger.info("")

    # Get this week's earnings
    week_earnings = calendar.get_week_earnings(days=7)

    if not week_earnings:
        logger.info("No earnings found for this week.")
    else:
        total_earnings = sum(len(earnings) for earnings in week_earnings.values())
        logger.info(f"Found {total_earnings} total earnings across {len(week_earnings)} days")
        logger.info("")

        for date_str, earnings in week_earnings.items():
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            day_name = date_obj.strftime('%A, %B %d, %Y')

            logger.info(f"\n{day_name}")
            logger.info('-' * 70)
            logger.info(f"Total: {len(earnings)} companies reporting")

            # Show first 10 for each day
            for i, earning in enumerate(earnings[:10], 1):
                ticker = earning['ticker']
                company = earning['company'][:40] if earning['company'] else 'N/A'
                time = earning['time']

                logger.info(f"{i:2d}. {ticker:6s} - {company:40s} ({time})")

            if len(earnings) > 10:
                logger.info(f"    ... and {len(earnings) - 10} more")

    logger.info("")
    logger.info('='*70)
