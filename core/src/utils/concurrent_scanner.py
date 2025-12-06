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
        expiration_offset=0,
    )
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Dict, Optional, Callable, Any, Tuple
from threading import Lock

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
    - Automatic rate limiting integration
    - Error isolation per ticker
    - Thread-safe result collection

    Note: Uses threading (not asyncio) to work with existing
    synchronous Tradier/Alpha Vantage API clients.
    """

    def __init__(
        self,
        container,
        max_workers: int = 5,
        rate_limit_per_second: float = 2.0,
    ):
        """
        Initialize concurrent scanner.

        Args:
            container: Dependency injection container
            max_workers: Maximum concurrent threads (default: 5)
            rate_limit_per_second: Max requests per second across all workers
        """
        self.container = container
        self.max_workers = max_workers
        self.rate_limit_per_second = rate_limit_per_second

        # Rate limiting state
        self._request_lock = Lock()
        self._last_request_time = 0.0
        self._min_interval = 1.0 / rate_limit_per_second

        # Statistics
        self._stats_lock = Lock()
        self._call_count = 0
        self._total_wait_time = 0.0

        logger.info(
            f"ConcurrentScanner initialized: {max_workers} workers, "
            f"{rate_limit_per_second:.1f} req/s limit"
        )

    def _rate_limit(self) -> None:
        """Apply rate limiting between requests."""
        with self._request_lock:
            now = time.time()
            elapsed = now - self._last_request_time

            if elapsed < self._min_interval:
                wait_time = self._min_interval - elapsed
                time.sleep(wait_time)

                # Update wait time stats under stats lock
                with self._stats_lock:
                    self._total_wait_time += wait_time

            # CRITICAL: Set AFTER sleep completes for accurate rate limiting
            self._last_request_time = time.time()

            # Update call count under stats lock for thread safety
            with self._stats_lock:
                self._call_count += 1

    def scan_ticker(
        self,
        ticker: str,
        earnings_date: date,
        expiration_date: date,
        analyze_func: Callable,
        filter_func: Optional[Callable] = None,
    ) -> ScanResult:
        """
        Scan a single ticker (thread-safe).

        Args:
            ticker: Stock ticker symbol
            earnings_date: Earnings announcement date
            expiration_date: Options expiration date
            analyze_func: Function to analyze ticker (receives container, ticker, dates)
            filter_func: Optional filter function (returns (should_filter, reason))

        Returns:
            ScanResult with analysis data or error
        """
        start_time = time.perf_counter()

        try:
            # Apply rate limiting
            self._rate_limit()

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
                expiration_date
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
        expiration_offset: int = 0,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> BatchScanResult:
        """
        Scan multiple tickers concurrently.

        Args:
            tickers: List of ticker symbols
            earnings_lookup: Dict mapping ticker -> (earnings_date, timing)
            analyze_func: Function to analyze each ticker
            filter_func: Optional filter function
            expiration_offset: Days to add to base expiration
            progress_callback: Optional callback(ticker, completed, total)

        Returns:
            BatchScanResult with all results and statistics
        """
        start_time = time.perf_counter()
        total_count = len(tickers)

        logger.info(f"Starting concurrent scan of {total_count} tickers with {self.max_workers} workers")

        results: List[ScanResult] = []
        completed = 0

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
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
                expiration_date = self._calculate_expiration(
                    earnings_date, timing, expiration_offset
                )

                future = executor.submit(
                    self.scan_ticker,
                    ticker,
                    earnings_date,
                    expiration_date,
                    analyze_func,
                    filter_func
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

    def _calculate_expiration(
        self,
        earnings_date: date,
        timing: str,
        offset: int
    ) -> date:
        """Calculate expiration date from earnings date and timing."""
        # BMO (Before Market Open): Expiration on same day or Friday after
        # AMC (After Market Close): Expiration on next day or Friday after

        if timing == 'BMO':
            base_date = earnings_date
        else:  # AMC or DMH
            base_date = earnings_date + timedelta(days=1)

        # Find Friday on or after base date
        days_until_friday = (4 - base_date.weekday()) % 7
        if days_until_friday == 0 and timing != 'BMO':
            days_until_friday = 7

        expiration = base_date + timedelta(days=days_until_friday)

        # Apply offset
        if offset:
            expiration = expiration + timedelta(days=offset * 7)

        return expiration

    def get_statistics(self) -> Dict[str, Any]:
        """Get scanner statistics (thread-safe)."""
        with self._stats_lock:
            call_count = self._call_count
            total_wait = self._total_wait_time
            avg_wait = (total_wait / call_count * 1000) if call_count > 0 else 0.0

            return {
                'total_calls': call_count,
                'total_wait_time_ms': total_wait * 1000,
                'avg_wait_time_ms': avg_wait,
                'max_workers': self.max_workers,
                'rate_limit_per_second': self.rate_limit_per_second,
            }

    def reset_statistics(self) -> None:
        """Reset scanner statistics."""
        with self._stats_lock:
            self._call_count = 0
            self._total_wait_time = 0.0


class AdaptiveRateLimiter:
    """
    Adaptive rate limiter that adjusts based on API responses.

    Increases rate when successful, decreases on 429 errors.
    """

    def __init__(
        self,
        initial_rate: float = 2.0,
        min_rate: float = 0.5,
        max_rate: float = 10.0,
        increase_factor: float = 1.1,
        decrease_factor: float = 0.5,
    ):
        """
        Initialize adaptive rate limiter.

        Args:
            initial_rate: Starting requests per second
            min_rate: Minimum rate (floor)
            max_rate: Maximum rate (ceiling)
            increase_factor: Multiply rate by this on success
            decrease_factor: Multiply rate by this on rate limit hit
        """
        self.current_rate = initial_rate
        self.min_rate = min_rate
        self.max_rate = max_rate
        self.increase_factor = increase_factor
        self.decrease_factor = decrease_factor

        self._lock = Lock()
        self._last_request = 0.0
        self._success_count = 0
        self._rate_limit_count = 0

    def acquire(self) -> None:
        """Wait for rate limit slot."""
        with self._lock:
            now = time.time()
            min_interval = 1.0 / self.current_rate
            elapsed = now - self._last_request

            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)

            self._last_request = time.time()

    def on_success(self) -> None:
        """Report successful request."""
        with self._lock:
            self._success_count += 1
            # Increase rate every 10 successes
            if self._success_count % 10 == 0:
                new_rate = min(self.max_rate, self.current_rate * self.increase_factor)
                if new_rate != self.current_rate:
                    logger.debug(f"Rate limit increased: {self.current_rate:.2f} -> {new_rate:.2f}")
                    self.current_rate = new_rate

    def on_rate_limit(self) -> None:
        """Report rate limit hit (429 error)."""
        with self._lock:
            self._rate_limit_count += 1
            new_rate = max(self.min_rate, self.current_rate * self.decrease_factor)
            logger.warning(f"Rate limit hit! Decreasing: {self.current_rate:.2f} -> {new_rate:.2f}")
            self.current_rate = new_rate
            self._success_count = 0  # Reset success counter

    @property
    def stats(self) -> Dict[str, Any]:
        """Get rate limiter statistics."""
        with self._lock:
            return {
                'current_rate': self.current_rate,
                'success_count': self._success_count,
                'rate_limit_count': self._rate_limit_count,
            }
