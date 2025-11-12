"""
Performance tracking and monitoring (Phase 2, Session 6).

Tracks execution times for key operations and logs warnings when
operations exceed configurable thresholds.
"""

import time
import asyncio
import logging
from functools import wraps
from collections import defaultdict, deque
from typing import Dict, Deque, List, Optional, Callable, Any
from threading import Lock
import statistics

logger = logging.getLogger(__name__)


class PerformanceMonitor:
    """
    Tracks performance metrics for functions.

    Features:
    - Configurable thresholds per function
    - Automatic slow operation warnings
    - Statistics calculation (avg, min, max, p95, p99)
    - Thread-safe metric storage
    - Support for both sync and async functions
    - Bounded storage to prevent memory leaks
    """

    def __init__(self, default_threshold_ms: float = 10000, max_samples: int = 10000):
        """
        Initialize performance monitor.

        Args:
            default_threshold_ms: Default threshold for operations (milliseconds)
            max_samples: Maximum samples to keep per function (prevents memory leak)
        """
        self.max_samples = max_samples
        self.metrics: Dict[str, Deque[float]] = defaultdict(
            lambda: deque(maxlen=self.max_samples)
        )
        self._lock = Lock()
        self.default_threshold_ms = default_threshold_ms

        # Operation-specific thresholds (milliseconds)
        self.thresholds: Dict[str, float] = {
            "analyze_ticker": 1000,
            "get_option_chain": 500,
            "get_stock_price": 300,
            "calculate_vrp": 200,
            "calculate_implied_move": 100,
            "database_query": 50,
            "cache_operation": 10,
        }

    def set_threshold(self, func_name: str, threshold_ms: float) -> None:
        """Set custom threshold for a specific function."""
        self.thresholds[func_name] = threshold_ms

    def track(self, func_name: str, duration_ms: float) -> None:
        """
        Track execution time for a function.

        Args:
            func_name: Name of the function
            duration_ms: Execution duration in milliseconds
        """
        with self._lock:
            self.metrics[func_name].append(duration_ms)

        threshold = self.thresholds.get(func_name, self.default_threshold_ms)
        if duration_ms > threshold:
            logger.warning(
                f"SLOW: {func_name} took {duration_ms:.1f}ms "
                f"(threshold: {threshold}ms)"
            )
        else:
            logger.debug(f"PERF: {func_name} took {duration_ms:.1f}ms")

    def get_stats(self, func_name: str) -> Optional[Dict[str, float]]:
        """
        Get statistics for a specific function.

        Args:
            func_name: Name of the function

        Returns:
            Dictionary with statistics or None if no data
        """
        with self._lock:
            durations = self.metrics.get(func_name, [])

        if not durations:
            return None

        return {
            "count": len(durations),
            "avg": statistics.mean(durations),
            "min": min(durations),
            "max": max(durations),
            "median": statistics.median(durations),
            "p95": statistics.quantiles(durations, n=20)[18] if len(durations) >= 20 else max(durations),
            "p99": statistics.quantiles(durations, n=100)[98] if len(durations) >= 100 else max(durations),
        }

    def get_all_stats(self) -> Dict[str, Dict[str, float]]:
        """Get statistics for all tracked functions."""
        with self._lock:
            func_names = list(self.metrics.keys())

        return {
            func_name: self.get_stats(func_name)
            for func_name in func_names
            if self.get_stats(func_name) is not None
        }

    def reset(self, func_name: Optional[str] = None) -> None:
        """
        Reset metrics.

        Args:
            func_name: Specific function to reset, or None to reset all
        """
        with self._lock:
            if func_name:
                self.metrics[func_name] = deque(maxlen=self.max_samples)
            else:
                self.metrics.clear()

    def get_slow_operations(self, threshold_multiplier: float = 1.0) -> List[tuple]:
        """
        Get list of slow operations that exceeded their thresholds.

        Args:
            threshold_multiplier: Multiplier for thresholds (1.0 = normal)

        Returns:
            List of (func_name, avg_duration_ms, threshold_ms) tuples
        """
        slow_ops = []
        all_stats = self.get_all_stats()

        for func_name, stats in all_stats.items():
            threshold = self.thresholds.get(func_name, self.default_threshold_ms)
            threshold *= threshold_multiplier

            if stats["avg"] > threshold:
                slow_ops.append((func_name, stats["avg"], threshold))

        return sorted(slow_ops, key=lambda x: x[1], reverse=True)


# Global monitor instance
_monitor = PerformanceMonitor()


def get_monitor() -> PerformanceMonitor:
    """Get the global performance monitor instance."""
    return _monitor


def track_performance(func: Optional[Callable] = None, *, name: Optional[str] = None):
    """
    Decorator to track function performance.

    Can be used with or without arguments:
        @track_performance
        def my_func(): ...

        @track_performance(name="custom_name")
        def my_func(): ...

    Args:
        func: Function to decorate (when used without arguments)
        name: Custom name for tracking (optional)
    """

    def decorator(f: Callable) -> Callable:
        func_name = name or f.__name__

        # Handle async functions
        if asyncio.iscoroutinefunction(f):

            @wraps(f)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                try:
                    result = await f(*args, **kwargs)
                    return result
                finally:
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    _monitor.track(func_name, elapsed_ms)

            return async_wrapper

        # Handle sync functions
        else:

            @wraps(f)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = time.perf_counter()
                try:
                    result = f(*args, **kwargs)
                    return result
                finally:
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    _monitor.track(func_name, elapsed_ms)

            return sync_wrapper

    # Handle both @track_performance and @track_performance()
    if func is None:
        return decorator
    else:
        return decorator(func)


def log_performance_summary() -> None:
    """Log a summary of all performance metrics."""
    all_stats = _monitor.get_all_stats()

    if not all_stats:
        logger.info("No performance metrics collected")
        return

    logger.info("=" * 80)
    logger.info("PERFORMANCE SUMMARY")
    logger.info("=" * 80)

    for func_name, stats in sorted(all_stats.items()):
        threshold = _monitor.thresholds.get(func_name, _monitor.default_threshold_ms)
        exceeded = "⚠️ SLOW" if stats["avg"] > threshold else "✓"

        logger.info(
            f"{exceeded} {func_name:30s} | "
            f"count: {stats['count']:4d} | "
            f"avg: {stats['avg']:6.1f}ms | "
            f"p95: {stats['p95']:6.1f}ms | "
            f"max: {stats['max']:6.1f}ms | "
            f"threshold: {threshold:.0f}ms"
        )

    logger.info("=" * 80)
