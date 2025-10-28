"""
Earnings calendar scanner using Nasdaq API.
Gets actual earnings calendar for specified date range.
"""

import requests
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import logging

logger = logging.getLogger(__name__)


class EarningsCalendar:
    """Scan earnings calendar from Nasdaq."""

    def __init__(self):
        """Initialize earnings calendar scanner."""
        self.base_url = 'https://api.nasdaq.com/api/calendar/earnings'
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }

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
        days: int = 7
    ) -> Dict[str, List[Dict]]:
        """
        Get earnings for a week.

        Args:
            start_date: Start date (defaults to today)
            days: Number of days to scan (default 7)

        Returns:
            Dict mapping date strings to lists of earnings
        """
        if start_date is None:
            start_date = datetime.now()

        week_earnings = {}

        for i in range(days):
            date = start_date + timedelta(days=i)
            date_str = date.strftime('%Y-%m-%d')
            day_name = date.strftime('%A')

            logger.info(f"Fetching earnings for {day_name}, {date_str}")
            earnings = self.get_earnings_for_date(date)

            if earnings:
                week_earnings[date_str] = earnings
                logger.info(f"Found {len(earnings)} earnings for {date_str}")

        return week_earnings

    def get_filtered_earnings(
        self,
        days: int = 7,
        min_market_cap: Optional[float] = None,
        tickers: Optional[List[str]] = None
    ) -> List[Dict]:
        """
        Get filtered earnings for date range.

        Args:
            days: Number of days to scan
            min_market_cap: Minimum market cap filter (optional)
            tickers: List of specific tickers to filter for (optional)

        Returns:
            Flat list of filtered earnings
        """
        week_earnings = self.get_week_earnings(days=days)

        all_earnings = []
        for date_str, earnings_list in week_earnings.items():
            for earning in earnings_list:
                # Apply filters
                if tickers and earning['ticker'] not in tickers:
                    continue

                if min_market_cap and earning.get('market_cap', 0) < min_market_cap:
                    continue

                all_earnings.append(earning)

        # Sort by date
        all_earnings.sort(key=lambda x: x['date'])

        return all_earnings


# CLI for testing
if __name__ == "__main__":
    from datetime import datetime

    calendar = EarningsCalendar()

    print()
    print('='*70)
    print('EARNINGS CALENDAR - THIS WEEK')
    print('='*70)
    print()

    # Get this week's earnings
    week_earnings = calendar.get_week_earnings(days=7)

    if not week_earnings:
        print("No earnings found for this week.")
    else:
        total_earnings = sum(len(earnings) for earnings in week_earnings.values())
        print(f"Found {total_earnings} total earnings across {len(week_earnings)} days")
        print()

        for date_str, earnings in week_earnings.items():
            date_obj = datetime.strptime(date_str, '%Y-%m-%d')
            day_name = date_obj.strftime('%A, %B %d, %Y')

            print(f"\n{day_name}")
            print('-' * 70)
            print(f"Total: {len(earnings)} companies reporting")

            # Show first 10 for each day
            for i, earning in enumerate(earnings[:10], 1):
                ticker = earning['ticker']
                company = earning['company'][:40] if earning['company'] else 'N/A'
                time = earning['time']

                print(f"{i:2d}. {ticker:6s} - {company:40s} ({time})")

            if len(earnings) > 10:
                print(f"    ... and {len(earnings) - 10} more")

    print()
    print('='*70)
