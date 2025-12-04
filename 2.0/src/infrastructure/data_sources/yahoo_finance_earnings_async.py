"""
Async Yahoo Finance earnings date fetcher.

Async version for improved I/O performance with concurrent operations.
"""

import logging
import asyncio
from datetime import date, datetime, timedelta
from typing import Optional, Tuple, Dict
from enum import Enum
from collections import OrderedDict

from src.domain.errors import Result, AppError, ErrorCode
from src.domain.types import EarningsTiming

try:
    import yfinance as yf
except ImportError:
    yf = None

logger = logging.getLogger(__name__)


class YahooFinanceEarningsAsync:
    """Async fetch earnings dates from Yahoo Finance with LRU caching."""

    def __init__(
        self,
        timeout: int = 10,
        cache_ttl_hours: int = 24,
        max_cache_size: int = 1000
    ):
        """
        Initialize async Yahoo Finance earnings fetcher.

        Args:
            timeout: Request timeout in seconds
            cache_ttl_hours: Cache time-to-live in hours (default: 24)
            max_cache_size: Maximum number of entries in cache (default: 1000)
        """
        if yf is None:
            raise ImportError("yfinance not installed. Run: pip install yfinance")

        self.timeout = timeout
        self.cache_ttl = timedelta(hours=cache_ttl_hours)
        self.max_cache_size = max_cache_size

        # LRU Cache: OrderedDict maintains insertion order
        # {ticker: (earnings_date, timing, cached_at)}
        self._cache: OrderedDict[str, Tuple[date, EarningsTiming, datetime]] = OrderedDict()

        # Cache statistics
        self._stats = {
            "hits": 0,
            "misses": 0,
            "evictions": 0,
            "expirations": 0,
        }

        # Lock for thread-safe cache operations
        self._cache_lock = asyncio.Lock()

    async def get_next_earnings_date(
        self, ticker: str
    ) -> Result[Tuple[date, EarningsTiming], AppError]:
        """
        Async get next earnings date for a ticker from Yahoo Finance.

        Uses an in-memory cache to avoid redundant API calls within the TTL window.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Result with (earnings_date, timing) tuple or error
        """
        # Check cache first (thread-safe)
        async with self._cache_lock:
            if ticker in self._cache:
                cached_date, cached_timing, cached_at = self._cache[ticker]
                age = datetime.now() - cached_at
                if age < self.cache_ttl:
                    # Cache hit - move to end (most recently used)
                    self._cache.move_to_end(ticker)
                    self._stats["hits"] += 1
                    logger.debug(
                        f"{ticker}: Using cached Yahoo Finance data (age: {age.seconds//60}min)"
                    )
                    return Result.Ok((cached_date, cached_timing))
                else:
                    # Cache expired - remove stale entry
                    del self._cache[ticker]
                    self._stats["expirations"] += 1
                    logger.debug(f"{ticker}: Cache expired (age: {age.seconds//3600}hrs)")

            # Cache miss
            self._stats["misses"] += 1

        try:
            logger.debug(f"Fetching earnings date from Yahoo Finance: {ticker}")

            # Run blocking I/O in executor to avoid blocking event loop
            loop = asyncio.get_event_loop()
            earnings_date, timing = await loop.run_in_executor(
                None,  # Use default executor
                self._fetch_earnings_sync,
                ticker
            )

            logger.info(
                f"{ticker}: Yahoo Finance earnings date = {earnings_date} ({timing.value})"
            )

            # Update cache with LRU eviction (thread-safe)
            async with self._cache_lock:
                await self._update_cache(ticker, earnings_date, timing)

            return Result.Ok((earnings_date, timing))

        except Exception as e:
            logger.warning(f"Failed to fetch earnings date from Yahoo Finance for {ticker}: {e}")
            return Result.Err(
                AppError(
                    ErrorCode.EXTERNAL,
                    f"Yahoo Finance error for {ticker}: {str(e)}"
                )
            )

    def _fetch_earnings_sync(self, ticker: str) -> Tuple[date, EarningsTiming]:
        """
        Synchronous fetch (runs in executor).

        Separated for cleaner async/await pattern.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Tuple of (earnings_date, timing)

        Raises:
            Exception if fetch fails
        """
        # Fetch ticker data
        stock = yf.Ticker(ticker)

        # Get calendar data (includes next earnings date)
        calendar = stock.calendar

        if not calendar or 'Earnings Date' not in calendar:
            raise ValueError(f"No earnings date found for {ticker} in Yahoo Finance calendar")

        # Yahoo Finance returns a list of possible dates
        earnings_dates = calendar['Earnings Date']
        if not earnings_dates or len(earnings_dates) == 0:
            raise ValueError(f"Empty earnings date list for {ticker}")

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

        return earnings_date, timing

    async def _update_cache(
        self,
        ticker: str,
        earnings_date: date,
        timing: EarningsTiming
    ) -> None:
        """
        Update cache with LRU eviction policy (must be called with lock held).

        If cache is full, evicts the least recently used entry.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Earnings date to cache
            timing: Earnings timing (BMO/AMC/DMH)
        """
        # Check if cache is full
        if len(self._cache) >= self.max_cache_size:
            # Evict least recently used (first item in OrderedDict)
            evicted_ticker, _ = self._cache.popitem(last=False)
            self._stats["evictions"] += 1
            logger.debug(
                f"Cache full ({self.max_cache_size}): Evicted {evicted_ticker} for {ticker}"
            )

        # Add/update entry (always added to end as most recently used)
        self._cache[ticker] = (earnings_date, timing, datetime.now())
        logger.debug(f"{ticker}: Cached Yahoo Finance data (cache size: {len(self._cache)})")

    async def get_cache_stats(self) -> Dict[str, int]:
        """
        Get cache statistics.

        Returns:
            Dict with hits, misses, evictions, expirations, size, and hit_rate
        """
        async with self._cache_lock:
            total_requests = self._stats["hits"] + self._stats["misses"]
            hit_rate = (self._stats["hits"] / total_requests * 100) if total_requests > 0 else 0.0

            return {
                "hits": self._stats["hits"],
                "misses": self._stats["misses"],
                "evictions": self._stats["evictions"],
                "expirations": self._stats["expirations"],
                "size": len(self._cache),
                "max_size": self.max_cache_size,
                "hit_rate": round(hit_rate, 2),
            }

    async def clear_cache(self) -> None:
        """Clear the cache and reset statistics."""
        async with self._cache_lock:
            self._cache.clear()
            self._stats = {
                "hits": 0,
                "misses": 0,
                "evictions": 0,
                "expirations": 0,
            }
            logger.info("Cache cleared and statistics reset")


async def main():
    """Test async Yahoo Finance fetcher."""
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')

    # Test with small cache size to demonstrate LRU eviction
    fetcher = YahooFinanceEarningsAsync(max_cache_size=3)

    # Test with known tickers
    tickers = ['MRVL', 'AEO', 'SNOW', 'CRM', 'AAPL']

    print("=== First pass (cache misses) - Running concurrently ===")
    # Fetch all concurrently
    tasks = [fetcher.get_next_earnings_date(ticker) for ticker in tickers]
    results = await asyncio.gather(*tasks)

    for ticker, result in zip(tickers, results):
        if result.is_ok:
            earnings_date, timing = result.value
            print(f"{ticker}: {earnings_date} ({timing.value})")
        else:
            print(f"{ticker}: ERROR - {result.error}")

    print("\n=== Cache Statistics After First Pass ===")
    stats = await fetcher.get_cache_stats()
    for key, value in stats.items():
        print(f"{key}: {value}")

    print("\n=== Second pass (testing cache hits) - Running concurrently ===")
    # Test last 3 tickers (should be cache hits if still in cache)
    test_tickers = ['AAPL', 'CRM', 'SNOW']
    tasks = [fetcher.get_next_earnings_date(ticker) for ticker in test_tickers]
    results = await asyncio.gather(*tasks)

    for ticker, result in zip(test_tickers, results):
        if result.is_ok:
            earnings_date, timing = result.value
            print(f"{ticker}: {earnings_date} ({timing.value}) [CACHED]")

    print("\n=== Final Cache Statistics ===")
    stats = await fetcher.get_cache_stats()
    for key, value in stats.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    asyncio.run(main())
