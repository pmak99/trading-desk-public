"""
Yahoo Finance earnings date fetcher.

Fetches upcoming earnings dates from Yahoo Finance as a cross-reference
to Alpha Vantage data, which can sometimes be incorrect.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional, Tuple, Dict
from enum import Enum

from src.domain.errors import Result, AppError, ErrorCode
from src.domain.types import EarningsTiming

try:
    import yfinance as yf
except ImportError:
    yf = None

logger = logging.getLogger(__name__)


class YahooFinanceEarnings:
    """Fetch earnings dates from Yahoo Finance."""

    def __init__(self, timeout: int = 10, cache_ttl_hours: int = 24):
        """
        Initialize Yahoo Finance earnings fetcher.

        Args:
            timeout: Request timeout in seconds
            cache_ttl_hours: Cache time-to-live in hours (default: 24)
        """
        if yf is None:
            raise ImportError("yfinance not installed. Run: pip install yfinance")

        self.timeout = timeout
        self.cache_ttl = timedelta(hours=cache_ttl_hours)
        # Cache: {ticker: (earnings_date, timing, cached_at)}
        self._cache: Dict[str, Tuple[date, EarningsTiming, datetime]] = {}

    def get_next_earnings_date(
        self, ticker: str
    ) -> Result[Tuple[date, EarningsTiming], AppError]:
        """
        Get next earnings date for a ticker from Yahoo Finance.

        Uses an in-memory cache to avoid redundant API calls within the TTL window.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Result with (earnings_date, timing) tuple or error
        """
        # Check cache first
        if ticker in self._cache:
            cached_date, cached_timing, cached_at = self._cache[ticker]
            age = datetime.now() - cached_at
            if age < self.cache_ttl:
                logger.debug(
                    f"{ticker}: Using cached Yahoo Finance data (age: {age.seconds//60}min)"
                )
                return Result.Ok((cached_date, cached_timing))
            else:
                logger.debug(f"{ticker}: Cache expired (age: {age.seconds//3600}hrs)")

        try:
            logger.debug(f"Fetching earnings date from Yahoo Finance: {ticker}")

            # Fetch ticker data
            stock = yf.Ticker(ticker)

            # Get calendar data (includes next earnings date)
            calendar = stock.calendar

            if not calendar or 'Earnings Date' not in calendar:
                return Result.Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"No earnings date found for {ticker} in Yahoo Finance calendar"
                    )
                )

            # Yahoo Finance returns a list of possible dates
            earnings_dates = calendar['Earnings Date']
            if not earnings_dates or len(earnings_dates) == 0:
                return Result.Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"Empty earnings date list for {ticker}"
                    )
                )

            # Use the first date (most likely/confirmed)
            earnings_date = earnings_dates[0]

            # Convert to Python date if it's a datetime
            if isinstance(earnings_date, datetime):
                earnings_date = earnings_date.date()

            # Try to get timing from earnings_dates DataFrame (has timestamps)
            timing = EarningsTiming.AMC  # Default to AMC (most common)
            try:
                earnings_df = stock.earnings_dates
                if earnings_df is not None and len(earnings_df) > 0:
                    # Get the most recent future earnings date from DataFrame
                    future_dates = earnings_df[earnings_df.index >= datetime.now()]
                    if len(future_dates) > 0:
                        next_earnings = future_dates.index[0]
                        hour = next_earnings.hour

                        # Determine timing from hour
                        if hour < 9 or (hour == 9 and next_earnings.minute < 30):
                            timing = EarningsTiming.BMO
                        elif hour >= 16:
                            timing = EarningsTiming.AMC
                        else:
                            timing = EarningsTiming.DMH

                        logger.debug(f"{ticker}: Detected timing {timing.value} from hour {hour}")
            except Exception as e:
                logger.debug(f"{ticker}: Could not determine timing from DataFrame: {e}")

            logger.info(
                f"{ticker}: Yahoo Finance earnings date = {earnings_date} ({timing.value})"
            )

            # Update cache
            self._cache[ticker] = (earnings_date, timing, datetime.now())
            logger.debug(f"{ticker}: Cached Yahoo Finance data")

            return Result.Ok((earnings_date, timing))

        except Exception as e:
            logger.warning(f"Failed to fetch earnings date from Yahoo Finance for {ticker}: {e}")
            return Result.Err(
                AppError(
                    ErrorCode.EXTERNAL,
                    f"Yahoo Finance error for {ticker}: {str(e)}"
                )
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

    fetcher = YahooFinanceEarnings()

    # Test with known tickers
    for ticker in ['MRVL', 'AEO', 'SNOW', 'CRM']:
        result = fetcher.get_next_earnings_date(ticker)
        if result.is_ok:
            earnings_date, timing = result.value
            print(f"{ticker}: {earnings_date} ({timing.value})")
        else:
            print(f"{ticker}: ERROR - {result.error}")
