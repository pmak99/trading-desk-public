"""
Alpha Vantage earnings calendar integration.
Official NASDAQ data vendor with free tier (25 calls/day).

Returns earnings calendar in CSV format with confirmed dates.
"""

import requests
import csv
import io
import os
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging
import pytz
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)


class AlphaVantageCalendar:
    """Earnings calendar using Alpha Vantage API (official NASDAQ vendor)."""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Alpha Vantage calendar.

        Args:
            api_key: Alpha Vantage API key (defaults to env var)
        """
        self.api_key = api_key or os.getenv('ALPHA_VANTAGE_API_KEY')
        if not self.api_key:
            raise ValueError(
                "Alpha Vantage API key required. Set ALPHA_VANTAGE_API_KEY env var "
                "or pass api_key parameter. Get free key at: "
                "https://www.alphavantage.co/support/#api-key"
            )

        self.base_url = 'https://www.alphavantage.co/query'
        # Eastern timezone for market hours
        self.eastern = pytz.timezone('US/Eastern')

        # Persistent cache to reduce API calls (25/day limit on free tier)
        self._cache_dir = Path(__file__).parent.parent.parent / 'data' / 'cache'
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_file = self._cache_dir / 'alpha_vantage_earnings.json'

        self._cache = {}
        self._cache_timestamp = None
        self._cache_duration_hours = 24  # Cache for 24 hours (earnings calendars don't change often)

        # Load existing cache from file
        self._load_cache_from_file()

    def _load_cache_from_file(self) -> None:
        """Load cache from persistent file storage."""
        try:
            if self._cache_file.exists():
                with open(self._cache_file, 'r') as f:
                    cache_data = json.load(f)

                # Restore cache and timestamp
                self._cache = cache_data.get('data', {})
                timestamp_str = cache_data.get('timestamp')

                if timestamp_str:
                    self._cache_timestamp = datetime.fromisoformat(timestamp_str)
                    age = datetime.now() - self._cache_timestamp

                    if age < timedelta(hours=self._cache_duration_hours):
                        logger.info(f"Loaded Alpha Vantage cache from file (age: {age.seconds // 3600}h {(age.seconds % 3600) // 60}m)")
                    else:
                        logger.info(f"Cache file exists but is stale (age: {age.days}d {age.seconds // 3600}h)")
                        self._cache = {}
                        self._cache_timestamp = None
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to load cache from file: {e}. Starting with empty cache.")
            self._cache = {}
            self._cache_timestamp = None
        except Exception as e:
            logger.error(f"Unexpected error loading cache: {e}")
            self._cache = {}
            self._cache_timestamp = None

    def _save_cache_to_file(self) -> None:
        """Save cache to persistent file storage."""
        try:
            cache_data = {
                'data': self._cache,
                'timestamp': self._cache_timestamp.isoformat() if self._cache_timestamp else None,
                'cache_duration_hours': self._cache_duration_hours
            }

            with open(self._cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)

            logger.debug(f"Saved cache to {self._cache_file}")
        except Exception as e:
            logger.warning(f"Failed to save cache to file: {e}")

    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid."""
        if self._cache_timestamp is None:
            return False

        age = datetime.now() - self._cache_timestamp
        return age < timedelta(hours=self._cache_duration_hours)

    def _fetch_earnings_calendar(self, horizon: str = '3month') -> List[Dict]:
        """
        Fetch earnings calendar from Alpha Vantage API.

        Note: API requires minimum 3-month horizon, but we filter to only keep
        the next 14 days (aligned with 1-2 day pre-earnings entry strategy).

        Args:
            horizon: Time horizon ('3month', '6month', '12month')

        Returns:
            List of earnings dicts (filtered to 14 days)
        """
        # Check cache first
        if self._is_cache_valid() and horizon in self._cache:
            logger.info(f"Using cached Alpha Vantage data (age: {datetime.now() - self._cache_timestamp})")
            return self._cache[horizon]

        params = {
            'function': 'EARNINGS_CALENDAR',
            'horizon': horizon,
            'apikey': self.api_key
        }

        try:
            logger.info(f"Fetching earnings calendar from Alpha Vantage (horizon: {horizon})")
            response = requests.get(
                self.base_url,
                params=params,
                timeout=15
            )
            response.raise_for_status()

            # Alpha Vantage returns CSV format
            csv_data = response.text

            # Check for rate limit error (returns "I,n,f,o,r,m" instead of real data)
            if csv_data.strip().startswith('symbol,name,reportDate') and 'I,n,f,o,r,m' in csv_data:
                logger.error("Alpha Vantage API rate limit exceeded (25 requests/day on free tier)")
                logger.error("Wait until tomorrow or upgrade to premium plan")
                logger.error("See: https://www.alphavantage.co/premium/")

                # Try to use stale cache if available
                if horizon in self._cache and self._cache[horizon]:
                    age = datetime.now() - self._cache_timestamp if self._cache_timestamp else timedelta(days=999)
                    logger.warning(f"Using STALE cached data (age: {age.days}d {age.seconds // 3600}h)")
                    logger.warning("This data may be outdated but better than nothing!")
                    return self._cache[horizon]

                return []

            # Parse CSV
            earnings = []
            reader = csv.DictReader(io.StringIO(csv_data))

            for row in reader:
                # CSV columns: symbol, name, reportDate, fiscalDateEnding, estimate, currency
                ticker = row.get('symbol', '').strip()
                company = row.get('name', 'Unknown').strip()
                report_date = row.get('reportDate', '').strip()
                estimate = row.get('estimate', '').strip()

                if ticker and report_date:
                    try:
                        # Parse date to validate format
                        date_obj = datetime.strptime(report_date, '%Y-%m-%d')

                        earnings.append({
                            'ticker': ticker,
                            'company': company,
                            'date': report_date,
                            'time': 'time-not-confirmed',  # AV doesn't provide time
                            'market_cap': 0,  # Not provided by AV
                            'estimate': estimate if estimate else None,
                            'source': 'alphavantage'
                        })
                    except ValueError:
                        logger.warning(f"Invalid date format for {ticker}: {report_date}")
                        continue

            logger.info(f"Fetched {len(earnings)} earnings from Alpha Vantage")

            # Filter to only keep current week's earnings (1-2 day pre-earnings strategy)
            # Keep 14 days to cover ~7 trading days + weekends/holidays
            today = datetime.now().date()
            cutoff_date = today + timedelta(days=14)

            filtered_earnings = [
                e for e in earnings
                if datetime.strptime(e['date'], '%Y-%m-%d').date() <= cutoff_date
            ]

            if len(filtered_earnings) < len(earnings):
                logger.info(f"Filtered to {len(filtered_earnings)} earnings within 14 days (discarded {len(earnings) - len(filtered_earnings)} distant earnings)")

            # Update cache in memory (only store current week)
            self._cache[horizon] = filtered_earnings
            self._cache_timestamp = datetime.now()

            # Save cache to persistent file
            self._save_cache_to_file()

            return earnings

        except requests.RequestException as e:
            logger.error(f"Error fetching from Alpha Vantage: {e}")
            # Return cached data if available
            if horizon in self._cache:
                logger.warning("Using stale cached data due to API error")
                return self._cache[horizon]
            return []
        except csv.Error as e:
            logger.error(f"Error parsing CSV response: {e}")
            return []

    def _is_already_reported(self, earning: Dict, now_et: datetime) -> bool:
        """
        Check if earnings have already been reported.

        Note: Alpha Vantage doesn't provide time (pre/post market),
        so we conservatively assume earnings are reported same day.

        Args:
            earning: Earnings dict with 'date' field
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

        # Today - since we don't know the time, assume after-hours (most common)
        # Only filter if market has closed (4:00 PM ET), so after-hours earnings are still available
        current_hour = now_et.hour
        current_minute = now_et.minute
        market_closed = current_hour > 16 or (current_hour == 16 and current_minute >= 0)

        # Before 4pm ET: keep earnings (could be after-hours)
        # After 4pm ET: filter earnings (likely already reported)
        return market_closed

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

        # Major market holidays by year
        # Note: Update annually or use a market calendar library for production
        holidays_by_year = {
            2025: [
                (1, 1),    # New Year's Day
                (1, 20),   # MLK Day
                (2, 17),   # Presidents Day
                (4, 18),   # Good Friday
                (5, 26),   # Memorial Day
                (7, 4),    # Independence Day
                (9, 1),    # Labor Day
                (11, 27),  # Thanksgiving
                (12, 25),  # Christmas
            ],
            2026: [
                (1, 1),    # New Year's Day
                (1, 19),   # MLK Day
                (2, 16),   # Presidents Day
                (4, 3),    # Good Friday
                (5, 25),   # Memorial Day
                (7, 3),    # Independence Day (observed)
                (9, 7),    # Labor Day
                (11, 26),  # Thanksgiving
                (12, 25),  # Christmas
            ],
            2027: [
                (1, 1),    # New Year's Day
                (1, 18),   # MLK Day
                (2, 15),   # Presidents Day
                (3, 26),   # Good Friday
                (5, 31),   # Memorial Day
                (7, 5),    # Independence Day (observed)
                (9, 6),    # Labor Day
                (11, 25),  # Thanksgiving
                (12, 24),  # Christmas (observed)
            ]
        }

        # Get holidays for this year, fallback to empty list for unknown years
        year_holidays = holidays_by_year.get(date.year, [])
        if not year_holidays:
            logger.warning(f"No holiday data for {date.year}, update holidays_by_year in alpha_vantage.py")

        date_tuple = (date.month, date.day)
        return date_tuple in year_holidays

    def get_earnings_for_date(self, date: datetime) -> List[Dict]:
        """
        Get earnings for a specific date.

        Args:
            date: Date to get earnings for

        Returns:
            List of earnings dicts with ticker, company, time, etc.
        """
        date_str = date.strftime('%Y-%m-%d')

        # Determine horizon based on how far out the date is
        days_ahead = (date.date() - datetime.now().date()).days
        if days_ahead <= 90:
            horizon = '3month'
        elif days_ahead <= 180:
            horizon = '6month'
        else:
            horizon = '12month'

        # Fetch full calendar
        all_earnings = self._fetch_earnings_calendar(horizon)

        # Filter to specific date
        earnings = [e for e in all_earnings if e['date'] == date_str]

        logger.info(f"Found {len(earnings)} earnings for {date_str} from Alpha Vantage")
        return earnings

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

        # Fetch all earnings upfront (more efficient than per-date calls)
        all_earnings = self._fetch_earnings_calendar('3month')

        # Group by date
        earnings_by_date = {}
        for earning in all_earnings:
            date_str = earning['date']
            if date_str not in earnings_by_date:
                earnings_by_date[date_str] = []
            earnings_by_date[date_str].append(earning)

        # Filter to requested date range
        week_earnings = {}
        days_scanned = 0
        offset = 0

        while days_scanned < days and offset < days * 2:
            date = start_date + timedelta(days=offset)
            offset += 1

            # Skip weekends and holidays if requested
            if skip_weekends and self._is_weekend_or_holiday(date):
                logger.debug(f"Skipping {date.strftime('%Y-%m-%d')} (weekend/holiday)")
                continue

            date_str = date.strftime('%Y-%m-%d')

            if date_str in earnings_by_date:
                earnings = earnings_by_date[date_str]
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
            min_market_cap: Minimum market cap filter (not supported by AV, ignored)
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

                # Apply ticker filter
                if tickers and earning['ticker'] not in tickers:
                    continue

                # Note: market_cap filter not supported by Alpha Vantage
                if min_market_cap:
                    logger.warning(
                        "min_market_cap filter not supported by Alpha Vantage, ignoring"
                    )

                all_earnings.append(earning)

        if filtered_count > 0:
            logger.info(f"Filtered out {filtered_count} already-reported earnings")

        # Sort by date
        all_earnings.sort(key=lambda x: x['date'])

        return all_earnings


# CLI for testing
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    try:
        calendar = AlphaVantageCalendar()

        logger.info("")
        logger.info('='*70)
        logger.info('ALPHA VANTAGE EARNINGS CALENDAR - THIS WEEK')
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
                    estimate = earning.get('estimate', 'N/A')

                    logger.info(f"{i:2d}. {ticker:6s} - {company:40s} (Est: {estimate})")

                if len(earnings) > 10:
                    logger.info(f"    ... and {len(earnings) - 10} more")

        logger.info("")
        logger.info('='*70)

    except ValueError as e:
        logger.error(str(e))
        logger.info("\nTo use Alpha Vantage:")
        logger.info("1. Get free API key: https://www.alphavantage.co/support/#api-key")
        logger.info("2. Add to .env file: ALPHA_VANTAGE_API_KEY=your_key_here")
