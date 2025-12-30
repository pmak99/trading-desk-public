"""
Ticker data fetcher module.

Handles fetching ticker data from multiple sources (yfinance, Tradier)
with parallel processing for optimal performance.

Extracted from earnings_analyzer.py to improve modularity.
"""

# Standard library imports
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

# Third-party imports
import yfinance as yf

# Local application imports
from src.core.exceptions import DataFetchError
from src.data.yfinance_cache import get_cache

logger = logging.getLogger(__name__)

# Timeout constants (reduced from 30s to 10s for faster failure recovery)
YFINANCE_FETCH_TIMEOUT = 10  # seconds per yfinance API call
TRADIER_FETCH_TIMEOUT = 10    # seconds per Tradier API call


class TickerDataFetcher:
    """
    Fetches ticker data from yfinance and Tradier in parallel.

    Uses batch fetching for yfinance (50% faster) and parallel fetching
    for Tradier options data (5-10x faster).

    Example:
        fetcher = TickerDataFetcher(ticker_filter)
        tickers_data, failed = fetcher.fetch_tickers_data(
            ['AAPL', 'MSFT'], '2025-11-15'
        )
    """

    def __init__(self, ticker_filter):
        """
        Initialize ticker data fetcher.

        Args:
            ticker_filter: TickerFilter instance for scoring and Tradier access
        """
        self.ticker_filter = ticker_filter

        # OPTIMIZATION: Reuse IVHistoryTracker instance to avoid DB connection overhead
        # Creating a new tracker for each ticker was adding 0.485s overhead per ticker
        from src.options.iv_history_tracker import IVHistoryTracker
        self.iv_tracker = IVHistoryTracker()

        # OPTIMIZATION: Cache yfinance .info results to reduce redundant API calls
        # 15-minute TTL - instant responses for repeated queries
        self.yf_cache = get_cache(ttl_minutes=15)

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures resource cleanup."""
        self.close()
        return False

    def __del__(self):
        """Destructor - ensures cleanup on garbage collection."""
        try:
            self.close()
        except Exception:
            pass  # Suppress errors during cleanup

    def close(self):
        """
        Explicitly close and cleanup resources.

        RESOURCE MANAGEMENT: Closes IVHistoryTracker DB connection.
        Call this when done with the fetcher, or use as context manager.
        """
        if hasattr(self, 'iv_tracker') and self.iv_tracker:
            try:
                self.iv_tracker.close()
            except Exception as e:
                logger.debug(f"Error closing IV tracker: {e}")

    def fetch_tickers_data(
        self,
        tickers: List[str],
        earnings_date: str
    ) -> Tuple[List[Dict], List[str]]:
        """
        Fetch basic ticker data from yfinance and options data from Tradier.

        Uses batch fetching with yf.Tickers() for improved performance (50% faster).

        Args:
            tickers: List of ticker symbols
            earnings_date: Earnings date for options selection

        Returns:
            Tuple of (tickers_data, failed_tickers)
            - tickers_data: List of dicts with ticker info and options data
            - failed_tickers: List of tickers that failed to fetch
        """
        tickers_data = []
        failed_tickers = []

        if not tickers:
            return tickers_data, failed_tickers

        # Batch fetch with error handling and fallback to individual fetching
        logger.info(f"📥 Batch fetching data for {len(tickers)} tickers...")
        tickers_str = ' '.join(tickers)

        # THREAD SAFETY: Disable batch mode for parallel fetching
        # yfinance Tickers() object is not guaranteed to be thread-safe
        # Only use batch mode for sequential fetching (1-2 tickers)
        use_batch_mode = len(tickers) < 3

        tickers_obj = None
        if use_batch_mode:
            try:
                tickers_obj = yf.Tickers(tickers_str)
                use_batch = True
            except Exception as e:
                logger.warning(f"Batch fetch failed ({e}), falling back to individual ticker fetching")
                tickers_obj = None
                use_batch = False
        else:
            # Parallel mode: force individual Ticker() calls (thread-safe)
            use_batch = False

        # STEP 1: Fetch basic ticker data from yfinance (PARALLEL for 3+ tickers)
        # OPTIMIZATION: Parallelize yfinance .info fetching for 3+ tickers
        # - Sequential for 1-2 tickers (overhead > benefit)
        # - Parallel for 3+ tickers (~3x speedup)
        # - Max 5 workers to avoid overwhelming yfinance servers
        # THREAD SAFETY: Each parallel worker creates its own Ticker() object
        basic_ticker_data = []

        if len(tickers) < 3:
            # Sequential for small batches (faster due to no threading overhead)
            logger.info(f"📥 Fetching data sequentially for {len(tickers)} ticker(s)...")
            for i, ticker in enumerate(tickers, 1):
                logger.info(f"  [{i}/{len(tickers)}] Fetching basic data for {ticker}...")
                ticker_data = self._fetch_single_ticker_info(
                    ticker, earnings_date, use_batch, tickers_obj
                )
                if ticker_data:
                    basic_ticker_data.append(ticker_data)
                else:
                    failed_tickers.append(ticker)
        else:
            # Parallel for larger batches (3+ tickers)
            logger.info(f"📥 Fetching data in parallel for {len(tickers)} tickers...")
            with ThreadPoolExecutor(max_workers=min(5, len(tickers))) as executor:
                # Submit all fetch tasks
                future_to_ticker = {
                    executor.submit(
                        self._fetch_single_ticker_info,
                        ticker,
                        earnings_date,
                        use_batch,
                        tickers_obj
                    ): ticker for ticker in tickers
                }

                # Process results as they complete
                for future in as_completed(future_to_ticker):
                    ticker = future_to_ticker[future]
                    try:
                        ticker_data = future.result(timeout=YFINANCE_FETCH_TIMEOUT)
                        if ticker_data:
                            basic_ticker_data.append(ticker_data)
                            logger.info(f"  ✓ {ticker}: Basic data fetched")
                        else:
                            failed_tickers.append(ticker)
                            logger.warning(f"  ✗ {ticker}: Failed to fetch basic data")
                    except Exception as e:
                        logger.warning(f"  ✗ {ticker}: Failed to fetch basic data: {e}")
                        failed_tickers.append(ticker)

            # Sort results by ticker for deterministic order (helps with debugging/testing)
            basic_ticker_data.sort(key=lambda x: x['ticker'])

        # STEP 2 - Fetch options data in PARALLEL (5-10x speedup)
        logger.info(f"📊 Fetching options data for {len(basic_ticker_data)} tickers in parallel...")
        tickers_data = self._fetch_options_parallel(basic_ticker_data, earnings_date, failed_tickers)

        return tickers_data, failed_tickers

    def _fetch_single_ticker_info(
        self,
        ticker: str,
        earnings_date: str,
        use_batch: bool,
        tickers_obj: Optional[yf.Tickers]
    ) -> Optional[Dict]:
        """
        Fetch basic info for a single ticker from yfinance with caching.

        Helper function for parallel fetching. Can be called from ThreadPoolExecutor.

        OPTIMIZATION: Uses 15-minute TTL cache to avoid redundant yfinance API calls.
        Cache hit = instant response (vs 200-400ms for API call).

        Args:
            ticker: Ticker symbol
            earnings_date: Earnings date
            use_batch: Whether to use batch tickers object
            tickers_obj: yf.Tickers object (if use_batch=True)

        Returns:
            Dict with ticker data, or None if fetch failed
        """
        try:
            # Try cache first
            info = self.yf_cache.get_info(ticker)

            if info is None:
                # Cache miss - fetch from yfinance
                if use_batch and tickers_obj:
                    try:
                        stock = tickers_obj.tickers.get(ticker)
                        if not stock:
                            logger.debug(f"{ticker}: Not in batch, fetching individually")
                            stock = yf.Ticker(ticker)
                    except (KeyError, AttributeError) as e:
                        logger.debug(f"{ticker}: Batch access failed, fetching individually")
                        stock = yf.Ticker(ticker)
                else:
                    stock = yf.Ticker(ticker)

                info = stock.info

                # Cache for future use
                self.yf_cache.set_info(ticker, info)

            ticker_data = {
                'ticker': ticker,
                'earnings_date': earnings_date,
                'earnings_time': 'amc',  # Default to after-market
                'market_cap': info.get('marketCap', 0),
                'price': info.get('currentPrice', info.get('regularMarketPrice', 0))
            }

            return ticker_data

        except (ConnectionError, TimeoutError, ValueError, KeyError, AttributeError) as e:
            # Expected errors - log at debug level
            logger.debug(f"{ticker}: Failed to fetch info: {e}")
            return None
        except Exception as e:
            # Unexpected errors - log at error level for debugging
            logger.error(f"{ticker}: Unexpected error fetching info: {e}", exc_info=True)
            return None

    def _fetch_options_parallel(
        self,
        basic_ticker_data: List[Dict],
        earnings_date: str,
        failed_tickers: List[str]
    ) -> List[Dict]:
        """
        Fetch options data in parallel using ThreadPoolExecutor.

        OPTIMIZATION: Tradier API can handle concurrent requests.
        Using 5 parallel workers provides 5-10x speedup vs sequential.

        Args:
            basic_ticker_data: List of dicts with basic ticker info (ticker, price, etc.)
            earnings_date: Earnings date for options selection
            failed_tickers: List to append failed tickers to

        Returns:
            List of ticker dicts with options_data and scores added
        """
        tickers_data = []

        # Use ThreadPoolExecutor for I/O-bound Tradier API calls
        # max_workers=5 balances speed vs API rate limits
        with ThreadPoolExecutor(max_workers=5) as executor:
            # Submit all options data fetch tasks
            future_to_ticker = {
                executor.submit(
                    self.ticker_filter.tradier_client.get_options_data,
                    td['ticker'],
                    td['price'],
                    earnings_date
                ): td for td in basic_ticker_data
            }

            # Process results as they complete
            for future in as_completed(future_to_ticker):
                ticker_data = future_to_ticker[future]
                ticker = ticker_data['ticker']

                try:
                    # Get options data result (with reduced timeout for faster failure recovery)
                    options_data = future.result(timeout=TRADIER_FETCH_TIMEOUT)

                    # Validate options data
                    if not options_data or not options_data.get('current_iv'):
                        logger.warning(f"{ticker}: No valid options data - skipping")
                        failed_tickers.append(ticker)
                        continue

                    # Add options data to ticker_data
                    ticker_data['options_data'] = options_data

                    # Calculate and add weekly IV change to options_data (for reporting)
                    # OPTIMIZATION: Use shared IV tracker instance (avoids DB connection overhead)
                    current_iv = options_data.get('current_iv')
                    if current_iv and current_iv > 0:
                        try:
                            weekly_change = self.iv_tracker.get_weekly_iv_change(ticker, current_iv)
                            if weekly_change is not None:
                                options_data['weekly_iv_change'] = weekly_change
                        except Exception as e:
                            logger.debug(f"{ticker}: Failed to get weekly IV change: {e}")

                    # Calculate score
                    ticker_data['score'] = self.ticker_filter.calculate_score(ticker_data)

                    tickers_data.append(ticker_data)
                    logger.info(f"    ✓ {ticker}: IV={options_data.get('current_iv', 0):.2f}%, Score={ticker_data['score']:.1f}")

                except (TimeoutError, ConnectionError, ValueError, KeyError) as e:
                    logger.warning(f"    ✗ {ticker}: Failed to fetch options data: {e}")
                    failed_tickers.append(ticker)
                    continue
                except Exception as e:
                    # Log unexpected errors at higher level for debugging
                    logger.error(f"    ✗ {ticker}: Unexpected error fetching options data: {e}", exc_info=True)
                    failed_tickers.append(ticker)
                    continue

        # Sort results by ticker for deterministic order
        tickers_data.sort(key=lambda x: x['ticker'])

        return tickers_data
