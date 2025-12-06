"""
Async wrapper for yfinance API calls.

yfinance is synchronous, so we use asyncio.to_thread() to run it
in a thread pool without blocking the event loop.

This maintains compatibility with the existing yfinance library while
enabling concurrent execution alongside other async operations.

Usage:
    async with AsyncYFinance() as yf:
        info = await yf.get_ticker_info('AAPL')
        prices = await yf.get_ticker_infos(['AAPL', 'MSFT', 'GOOGL'])
"""

import asyncio
import logging
import re
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# Pre-compiled regex patterns for company name cleaning
_COMPANY_SUFFIX_PATTERNS = [
    re.compile(pattern, re.IGNORECASE) for pattern in [
        r',?\s+Inc\.?$',
        r',?\s+Incorporated$',
        r',?\s+Corp\.?$',
        r',?\s+Corporation$',
        r',?\s+Ltd\.?$',
        r',?\s+Limited$',
        r',?\s+LLC$',
        r',?\s+L\.L\.C\.?$',
        r',?\s+Co\.?$',
        r',?\s+Company$',
        r',?\s+PLC$',
        r',?\s+P\.L\.C\.?$',
        r',?\s+Plc$',
        r',?\s+LP$',
        r',?\s+L\.P\.?$',
    ]
]
_TRAILING_AMPERSAND_PATTERN = re.compile(r'\s*&\s*$')


def _clean_company_name(name: str) -> str:
    """Clean company name by removing formal suffixes."""
    cleaned = name
    for pattern in _COMPANY_SUFFIX_PATTERNS:
        cleaned = pattern.sub('', cleaned)
    cleaned = _TRAILING_AMPERSAND_PATTERN.sub('', cleaned)
    return cleaned.strip()


class AsyncYFinance:
    """
    Async wrapper for yfinance operations.

    Uses a thread pool to run yfinance calls without blocking
    the async event loop.
    """

    def __init__(
        self,
        max_workers: int = 5,
        rate_limit_delay: float = 0.1,
    ):
        """
        Initialize async yfinance wrapper.

        Args:
            max_workers: Max concurrent yfinance calls
            rate_limit_delay: Delay between calls to respect rate limits
        """
        self.max_workers = max_workers
        self.rate_limit_delay = rate_limit_delay
        self._executor: Optional[ThreadPoolExecutor] = None
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._yf = None  # Lazy loaded

        # Cache for ticker info
        self._cache: Dict[str, Tuple[Optional[float], Optional[str]]] = {}

        # Statistics
        self._request_count = 0
        self._cache_hits = 0

    async def __aenter__(self) -> 'AsyncYFinance':
        """Async context manager entry."""
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers)
        self._semaphore = asyncio.Semaphore(self.max_workers)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._executor:
            self._executor.shutdown(wait=False)
            self._executor = None

    def _ensure_yfinance(self) -> bool:
        """Lazy load yfinance module."""
        if self._yf is None:
            try:
                import yfinance
                self._yf = yfinance
                return True
            except ImportError:
                logger.warning("yfinance not available")
                return False
        return True

    def _fetch_ticker_info_sync(
        self, ticker: str
    ) -> Tuple[Optional[float], Optional[str]]:
        """
        Synchronous ticker info fetch (runs in thread pool).

        Returns:
            Tuple of (market_cap_millions, company_name)
        """
        if not self._ensure_yfinance():
            return (None, None)

        try:
            stock = self._yf.Ticker(ticker)
            info = stock.info

            # Extract market cap
            market_cap = info.get('marketCap')
            market_cap_millions = None
            if market_cap and market_cap > 0:
                market_cap_millions = market_cap / 1_000_000

            # Extract and clean company name
            company_name = info.get('shortName') or info.get('longName')
            cleaned_name = None
            if company_name:
                cleaned_name = _clean_company_name(company_name)

            return (market_cap_millions, cleaned_name)

        except Exception as e:
            logger.debug(f"{ticker}: Failed to fetch ticker info: {e}")
            return (None, None)

    async def get_ticker_info(
        self, ticker: str
    ) -> Tuple[Optional[float], Optional[str]]:
        """
        Get market cap and company name for a ticker (async).

        Returns cached result if available, otherwise fetches from API.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Tuple of (market_cap_millions, company_name)
        """
        # Check cache first
        if ticker in self._cache:
            self._cache_hits += 1
            return self._cache[ticker]

        if not self._executor:
            raise RuntimeError("AsyncYFinance must be used as async context manager")

        # Rate limit via semaphore
        async with self._semaphore:
            self._request_count += 1

            # Small delay to respect rate limits
            await asyncio.sleep(self.rate_limit_delay)

            # Run sync function in thread pool
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self._executor,
                self._fetch_ticker_info_sync,
                ticker
            )

            # Cache the result
            self._cache[ticker] = result
            return result

    async def get_ticker_infos(
        self, tickers: List[str]
    ) -> Dict[str, Tuple[Optional[float], Optional[str]]]:
        """
        Get market cap and company name for multiple tickers (async parallel).

        Args:
            tickers: List of stock ticker symbols

        Returns:
            Dict mapping ticker -> (market_cap_millions, company_name)
        """
        # Create tasks for all tickers
        tasks = [self.get_ticker_info(ticker) for ticker in tickers]

        # Execute all in parallel
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Build result dict
        ticker_infos: Dict[str, Tuple[Optional[float], Optional[str]]] = {}
        for ticker, result in zip(tickers, results):
            if isinstance(result, Exception):
                logger.warning(f"{ticker}: Error fetching info: {result}")
                ticker_infos[ticker] = (None, None)
            else:
                ticker_infos[ticker] = result

        return ticker_infos

    def get_market_cap_millions(self, ticker: str) -> Optional[float]:
        """
        Get cached market cap (sync convenience method).

        Only returns cached values - use get_ticker_info for fresh data.
        """
        if ticker in self._cache:
            return self._cache[ticker][0]
        return None

    def get_company_name(self, ticker: str) -> Optional[str]:
        """
        Get cached company name (sync convenience method).

        Only returns cached values - use get_ticker_info for fresh data.
        """
        if ticker in self._cache:
            return self._cache[ticker][1]
        return None

    def get_stats(self) -> Dict[str, int]:
        """Get statistics."""
        return {
            'request_count': self._request_count,
            'cache_hits': self._cache_hits,
            'cache_size': len(self._cache),
        }
