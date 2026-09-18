"""
Concurrent ticker scanning utilities.

Provides thread-pool based concurrent processing for ticker analysis,
significantly improving scan performance for multiple tickers.

Performance Impact:
- Sequential: ~50 tickers x 2s = 100s
- Concurrent (5 workers): ~50 tickers / 5 = 20s (5x speedup)
- With caching: Additional 2-3x speedup for repeated scans

Usage:
    from src.utils.concurrent_scanner import ConcurrentScanner

    scanner = ConcurrentScanner(container, max_workers=5)
    results = scanner.scan_tickers(
        tickers=['AAPL', 'MSFT', 'GOOGL'],
        earnings_lookup=earnings_dict,
        expiration_offset=None,
    )
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.domain.enums import EarningsTiming
from src.domain.trading_expiration import calculate_expiration_date

logger = logging.getLogger(__name__)


@dataclass
class ScanResult:
    """Result of scanning a single ticker."""
    ticker: str
    status: str  # 'success', 'error', 'filtered', 'skip'
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_ms: float = 0.0


@dataclass
class BatchScanResult:
    """Result of batch ticker scan."""
    results: List[ScanResult]
    success_count: int
    error_count: int
    skip_count: int
    filtered_count: int
    total_duration_ms: float
    avg_duration_ms: float


class ConcurrentScanner:
    """
    Concurrent ticker scanner using thread pool.

    Features:
    - Configurable worker count
    - Progress callback support
    - Error isolation per ticker
    - Thread-safe result collection

    API pacing is not handled by this class — workers share the container's
    TradierAPI client, whose TokenBucketRateLimiter throttles at the actual
    HTTP call sites (accurate per-request, not per-ticker).

    Note: Uses threading (not asyncio) to work with existing
    synchronous Tradier/Finnhub API clients.
    """

    def __init__(
        self,
        container,
        max_workers: int = 5,
    ):
        """
        Initialize concurrent scanner.

        Args:
            container: Dependency injection container
            max_workers: Maximum concurrent threads (default: 5)

        Note: API pacing is NOT done here. It's enforced once, accurately, at
        the actual HTTP call sites in TradierAPI via the shared
        TokenBucketRateLimiter (src/utils/rate_limiter.py), which every
        worker thread shares through the container. A per-ticker gate here
        used to double up on that (throttling ticker *dispatch*, not real
        request rate) and only added an artificial floor to scan time.
        """
        self.container = container
        self.max_workers = max_workers

        logger.info(f"ConcurrentScanner initialized: {max_workers} workers")

    def scan_ticker(
        self,
        ticker: str,
        earnings_date: date,
        expiration_date: date,
        analyze_func: Callable,
        filter_func: Optional[Callable] = None,
        earnings_timing: EarningsTiming = EarningsTiming.UNKNOWN,
    ) -> ScanResult:
        """
        Scan a single ticker (thread-safe).

        Args:
            ticker: Stock ticker symbol
            earnings_date: Earnings announcement date
            expiration_date: Options expiration date
            analyze_func: Function to analyze ticker (receives container, ticker, dates)
            filter_func: Optional filter function (returns (should_filter, reason))
            earnings_timing: BMO/AMC timing for accurate analysis_log recording

        Returns:
            ScanResult with analysis data or error
        """
        start_time = time.perf_counter()

        try:
            # Check filter first
            if filter_func:
                should_filter, reason = filter_func(ticker, expiration_date)
                if should_filter:
                    return ScanResult(
                        ticker=ticker,
                        status='filtered',
                        error=reason,
                        duration_ms=(time.perf_counter() - start_time) * 1000
                    )

            # Analyze ticker
            result = analyze_func(
                self.container,
                ticker,
                earnings_date,
                expiration_date,
                earnings_timing=earnings_timing,
            )

            if result:
                return ScanResult(
                    ticker=ticker,
                    status='success' if result.get('status') == 'SUCCESS' else 'skip',
                    data=result,
                    duration_ms=(time.perf_counter() - start_time) * 1000
                )
            else:
                return ScanResult(
                    ticker=ticker,
                    status='error',
                    error='No result returned',
                    duration_ms=(time.perf_counter() - start_time) * 1000
                )

        except Exception as e:
            logger.error(f"Error scanning {ticker}: {e}")
            return ScanResult(
                ticker=ticker,
                status='error',
                error=str(e),
                duration_ms=(time.perf_counter() - start_time) * 1000
            )

    def scan_tickers(
        self,
        tickers: List[str],
        earnings_lookup: Dict[str, Tuple[date, str]],
        analyze_func: Callable,
        filter_func: Optional[Callable] = None,
        expiration_offset: Optional[int] = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> BatchScanResult:
        """
        Scan multiple tickers concurrently.

        Args:
            tickers: List of ticker symbols
            earnings_lookup: Dict mapping ticker -> (earnings_date, timing)
            analyze_func: Function to analyze each ticker
            filter_func: Optional filter function
            expiration_offset: Trading expiration this many days after the
                earnings date instead of the weekday rule; None for the rule.
                Passed to calculate_expiration_date as the sequential loops pass
                it, with the configured MIN_DTE.
            progress_callback: Optional callback(ticker, completed, total)

        Returns:
            BatchScanResult with all results and statistics
        """
        start_time = time.perf_counter()
        total_count = len(tickers)

        logger.info(f"Starting concurrent scan of {total_count} tickers with {self.max_workers} workers")

        # Keep the stock-price cache warm via the batch quote endpoint for the
        # whole scan: one rate-limit token per 100 tickers each time their
        # quotes expire, instead of one per ticker. A failed batch call is
        # non-fatal; individual fetches take over.
        warm = (self.container.cached_options_provider.keep_stock_prices_warm(tickers)
                if total_count > 3 else nullcontext(0))

        results: List[ScanResult] = []
        completed = 0
        min_dte = self.container.config.thresholds.min_dte

        with warm, ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all tasks
            future_to_ticker = {}

            for ticker in tickers:
                # Get earnings info
                earnings_info = earnings_lookup.get(ticker)
                if not earnings_info:
                    results.append(ScanResult(
                        ticker=ticker,
                        status='skip',
                        error='No earnings data'
                    ))
                    completed += 1
                    if progress_callback:
                        progress_callback(ticker, completed, total_count)
                    continue

                earnings_date, timing = earnings_info
                try:
                    timing_enum = EarningsTiming(timing)
                except ValueError:
                    timing_enum = EarningsTiming.UNKNOWN
                expiration_date = calculate_expiration_date(
                    earnings_date, timing_enum, expiration_offset, min_dte=min_dte
                )

                future = executor.submit(
                    self.scan_ticker,
                    ticker,
                    earnings_date,
                    expiration_date,
                    analyze_func,
                    filter_func,
                    timing_enum,
                )
                future_to_ticker[future] = ticker

            # Collect results as they complete
            for future in as_completed(future_to_ticker):
                ticker = future_to_ticker[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    # Log with traceback for debugging
                    logger.error(f"Future error for {ticker}: {e}", exc_info=True)
                    results.append(ScanResult(
                        ticker=ticker,
                        status='error',
                        error=str(e)
                    ))

                completed += 1
                if progress_callback:
                    progress_callback(ticker, completed, total_count)

        # Calculate statistics
        total_duration = (time.perf_counter() - start_time) * 1000

        success_count = sum(1 for r in results if r.status == 'success')
        error_count = sum(1 for r in results if r.status == 'error')
        skip_count = sum(1 for r in results if r.status == 'skip')
        filtered_count = sum(1 for r in results if r.status == 'filtered')

        avg_duration = total_duration / len(results) if results else 0

        logger.info(
            f"Concurrent scan complete: {success_count} success, "
            f"{error_count} errors, {skip_count} skipped, {filtered_count} filtered "
            f"in {total_duration:.0f}ms (avg {avg_duration:.0f}ms/ticker)"
        )

        return BatchScanResult(
            results=results,
            success_count=success_count,
            error_count=error_count,
            skip_count=skip_count,
            filtered_count=filtered_count,
            total_duration_ms=total_duration,
            avg_duration_ms=avg_duration
        )
