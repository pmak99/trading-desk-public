"""
Tests for performance tracking module (Phase 2, Session 6).

Tests comprehensive performance monitoring including decorators,
statistics, thresholds, and both sync/async functions.
"""

import pytest
import asyncio
import time
from unittest.mock import patch

from src.utils.performance import (
    PerformanceMonitor,
    track_performance,
    get_monitor,
    log_performance_summary,
)


@pytest.fixture
def monitor():
    """Create a fresh performance monitor for each test."""
    return PerformanceMonitor(default_threshold_ms=1000)


@pytest.fixture(autouse=True)
def reset_global_monitor():
    """Reset global monitor before and after each test."""
    get_monitor().reset()
    yield
    get_monitor().reset()


class TestPerformanceMonitorBasics:
    """Test basic performance monitor functionality."""

    def test_monitor_creation(self, monitor):
        """Test monitor can be created with default threshold."""
        assert monitor.default_threshold_ms == 1000
        assert len(monitor.metrics) == 0
        assert "analyze_ticker" in monitor.thresholds

    def test_track_single_measurement(self, monitor):
        """Test tracking a single measurement."""
        monitor.track("test_func", 100.0)

        assert "test_func" in monitor.metrics
        assert len(monitor.metrics["test_func"]) == 1
        assert monitor.metrics["test_func"][0] == 100.0

    def test_track_multiple_measurements(self, monitor):
        """Test tracking multiple measurements for same function."""
        monitor.track("test_func", 100.0)
        monitor.track("test_func", 200.0)
        monitor.track("test_func", 150.0)

        assert len(monitor.metrics["test_func"]) == 3
        assert monitor.metrics["test_func"] == [100.0, 200.0, 150.0]

    def test_set_custom_threshold(self, monitor):
        """Test setting custom threshold for a function."""
        monitor.set_threshold("my_func", 500.0)

        assert monitor.thresholds["my_func"] == 500.0

    def test_slow_operation_warning_logged(self, monitor, caplog):
        """Test that slow operations log warnings."""
        monitor.set_threshold("slow_func", 100.0)

        with caplog.at_level("WARNING"):
            monitor.track("slow_func", 200.0)

        assert "SLOW: slow_func took 200.0ms" in caplog.text
        assert "threshold: 100.0ms" in caplog.text

    def test_fast_operation_no_warning(self, monitor, caplog):
        """Test that fast operations don't log warnings."""
        monitor.set_threshold("fast_func", 100.0)

        with caplog.at_level("WARNING"):
            monitor.track("fast_func", 50.0)

        assert "SLOW" not in caplog.text


class TestPerformanceStatistics:
    """Test statistics calculation."""

    def test_get_stats_no_data(self, monitor):
        """Test get_stats returns None when no data."""
        stats = monitor.get_stats("nonexistent")
        assert stats is None

    def test_get_stats_single_measurement(self, monitor):
        """Test get_stats with single measurement."""
        monitor.track("test_func", 100.0)
        stats = monitor.get_stats("test_func")

        assert stats is not None
        assert stats["count"] == 1
        assert stats["avg"] == 100.0
        assert stats["min"] == 100.0
        assert stats["max"] == 100.0
        assert stats["median"] == 100.0

    def test_get_stats_multiple_measurements(self, monitor):
        """Test get_stats with multiple measurements."""
        measurements = [100.0, 200.0, 150.0, 300.0, 250.0]
        for m in measurements:
            monitor.track("test_func", m)

        stats = monitor.get_stats("test_func")

        assert stats["count"] == 5
        assert stats["avg"] == 200.0
        assert stats["min"] == 100.0
        assert stats["max"] == 300.0
        assert stats["median"] == 200.0

    def test_get_stats_percentiles(self, monitor):
        """Test percentile calculations with sufficient data."""
        # Add 100 measurements from 1 to 100
        for i in range(1, 101):
            monitor.track("test_func", float(i))

        stats = monitor.get_stats("test_func")

        assert stats["count"] == 100
        assert stats["p95"] > 90  # p95 should be around 95
        assert stats["p99"] > 95  # p99 should be around 99

    def test_get_all_stats(self, monitor):
        """Test getting stats for all functions."""
        monitor.track("func1", 100.0)
        monitor.track("func2", 200.0)
        monitor.track("func1", 150.0)

        all_stats = monitor.get_all_stats()

        assert "func1" in all_stats
        assert "func2" in all_stats
        assert all_stats["func1"]["count"] == 2
        assert all_stats["func2"]["count"] == 1


class TestPerformanceReset:
    """Test reset functionality."""

    def test_reset_specific_function(self, monitor):
        """Test resetting metrics for specific function."""
        monitor.track("func1", 100.0)
        monitor.track("func2", 200.0)

        monitor.reset("func1")

        assert len(monitor.metrics["func1"]) == 0
        assert len(monitor.metrics["func2"]) == 1

    def test_reset_all_functions(self, monitor):
        """Test resetting all metrics."""
        monitor.track("func1", 100.0)
        monitor.track("func2", 200.0)

        monitor.reset()

        assert len(monitor.metrics) == 0


class TestSlowOperations:
    """Test slow operation detection."""

    def test_get_slow_operations_empty(self, monitor):
        """Test get_slow_operations with no data."""
        slow_ops = monitor.get_slow_operations()
        assert slow_ops == []

    def test_get_slow_operations_none_slow(self, monitor):
        """Test get_slow_operations when all operations are fast."""
        monitor.set_threshold("fast_func", 1000.0)
        monitor.track("fast_func", 100.0)
        monitor.track("fast_func", 200.0)

        slow_ops = monitor.get_slow_operations()
        assert slow_ops == []

    def test_get_slow_operations_identifies_slow(self, monitor):
        """Test get_slow_operations identifies slow operations."""
        monitor.set_threshold("slow_func", 100.0)
        monitor.track("slow_func", 200.0)
        monitor.track("slow_func", 300.0)  # avg = 250

        slow_ops = monitor.get_slow_operations()

        assert len(slow_ops) == 1
        assert slow_ops[0][0] == "slow_func"
        assert slow_ops[0][1] == 250.0  # avg
        assert slow_ops[0][2] == 100.0  # threshold

    def test_get_slow_operations_sorted_by_duration(self, monitor):
        """Test slow operations are sorted by duration (slowest first)."""
        monitor.set_threshold("func1", 100.0)
        monitor.set_threshold("func2", 100.0)

        monitor.track("func1", 200.0)  # avg = 200
        monitor.track("func2", 500.0)  # avg = 500

        slow_ops = monitor.get_slow_operations()

        assert len(slow_ops) == 2
        assert slow_ops[0][0] == "func2"  # Slowest first
        assert slow_ops[1][0] == "func1"

    def test_get_slow_operations_with_multiplier(self, monitor):
        """Test threshold multiplier for slow operations."""
        monitor.set_threshold("func1", 100.0)
        monitor.track("func1", 150.0)  # avg = 150

        # With 2x multiplier, threshold becomes 200ms, so func1 is not slow
        slow_ops = monitor.get_slow_operations(threshold_multiplier=2.0)
        assert slow_ops == []

        # With 1x multiplier, func1 is slow
        slow_ops = monitor.get_slow_operations(threshold_multiplier=1.0)
        assert len(slow_ops) == 1


class TestTrackPerformanceDecorator:
    """Test @track_performance decorator."""

    def test_decorator_basic_sync(self):
        """Test decorator on simple sync function."""

        @track_performance
        def test_func():
            time.sleep(0.01)  # 10ms
            return "result"

        result = test_func()

        assert result == "result"
        stats = get_monitor().get_stats("test_func")
        assert stats is not None
        assert stats["count"] == 1
        assert stats["avg"] >= 10.0  # Should be at least 10ms

    def test_decorator_custom_name(self):
        """Test decorator with custom name."""

        @track_performance(name="custom_name")
        def test_func():
            return "result"

        test_func()

        stats = get_monitor().get_stats("custom_name")
        assert stats is not None
        assert stats["count"] == 1

    def test_decorator_preserves_function_name(self):
        """Test decorator preserves function name and docstring."""

        @track_performance
        def my_function():
            """My docstring."""
            pass

        assert my_function.__name__ == "my_function"
        assert my_function.__doc__ == "My docstring."

    def test_decorator_with_arguments(self):
        """Test decorator works with function arguments."""

        @track_performance
        def add(a, b):
            return a + b

        result = add(2, 3)
        assert result == 5

        stats = get_monitor().get_stats("add")
        assert stats["count"] == 1

    def test_decorator_with_kwargs(self):
        """Test decorator works with keyword arguments."""

        @track_performance
        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}"

        result = greet("Alice", greeting="Hi")
        assert result == "Hi, Alice"

    def test_decorator_multiple_calls(self):
        """Test decorator tracks multiple calls."""

        @track_performance
        def test_func():
            return "result"

        for _ in range(5):
            test_func()

        stats = get_monitor().get_stats("test_func")
        assert stats["count"] == 5

    @pytest.mark.asyncio
    async def test_decorator_async_function(self):
        """Test decorator works with async functions."""

        @track_performance
        async def async_func():
            await asyncio.sleep(0.01)  # 10ms
            return "async_result"

        result = await async_func()

        assert result == "async_result"
        stats = get_monitor().get_stats("async_func")
        assert stats is not None
        assert stats["count"] == 1
        assert stats["avg"] >= 10.0

    @pytest.mark.asyncio
    async def test_decorator_async_with_custom_name(self):
        """Test decorator with async function and custom name."""

        @track_performance(name="my_async")
        async def async_func():
            return "result"

        await async_func()

        stats = get_monitor().get_stats("my_async")
        assert stats is not None

    @pytest.mark.asyncio
    async def test_decorator_async_multiple_calls(self):
        """Test decorator tracks multiple async calls."""

        @track_performance
        async def async_func():
            return "result"

        for _ in range(3):
            await async_func()

        stats = get_monitor().get_stats("async_func")
        assert stats["count"] == 3


class TestPerformanceSummary:
    """Test performance summary logging."""

    def test_log_performance_summary_no_data(self, caplog):
        """Test log_performance_summary with no data."""
        with caplog.at_level("INFO"):
            log_performance_summary()

        assert "No performance metrics collected" in caplog.text

    def test_log_performance_summary_with_data(self, caplog):
        """Test log_performance_summary logs collected metrics."""
        monitor = get_monitor()
        monitor.track("test_func", 100.0)
        monitor.track("test_func", 200.0)

        with caplog.at_level("INFO"):
            log_performance_summary()

        assert "PERFORMANCE SUMMARY" in caplog.text
        assert "test_func" in caplog.text
        assert "count:" in caplog.text
        assert "avg:" in caplog.text

    def test_log_performance_summary_shows_slow_indicator(self, caplog):
        """Test summary shows slow operation indicator."""
        monitor = get_monitor()
        monitor.set_threshold("slow_func", 100.0)
        monitor.track("slow_func", 200.0)

        with caplog.at_level("INFO"):
            log_performance_summary()

        assert "⚠️ SLOW" in caplog.text or "SLOW" in caplog.text


class TestThreadSafety:
    """Test thread safety of performance monitor."""

    def test_concurrent_tracking(self, monitor):
        """Test concurrent tracking from multiple threads."""
        import threading

        def track_measurements():
            for i in range(100):
                monitor.track("test_func", float(i))

        threads = [threading.Thread(target=track_measurements) for _ in range(5)]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        stats = monitor.get_stats("test_func")
        assert stats["count"] == 500  # 5 threads × 100 measurements


class TestGlobalMonitor:
    """Test global monitor instance."""

    def test_get_monitor_returns_same_instance(self):
        """Test get_monitor returns the same instance."""
        monitor1 = get_monitor()
        monitor2 = get_monitor()

        assert monitor1 is monitor2

    def test_decorator_uses_global_monitor(self):
        """Test decorator uses global monitor instance."""

        @track_performance
        def test_func():
            return "result"

        test_func()

        # Should be able to get stats from global monitor
        stats = get_monitor().get_stats("test_func")
        assert stats is not None
