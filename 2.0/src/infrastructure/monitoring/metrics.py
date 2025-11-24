"""
Metrics collection and aggregation.

Lightweight metrics system for tracking system performance:
- Counters: Monotonically increasing values (e.g., total requests)
- Gauges: Point-in-time values (e.g., active connections)
- Histograms: Distribution of values (e.g., latency percentiles)
- Timers: Duration measurements (e.g., API call duration)
"""

import time
import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)


class MetricType(Enum):
    """Type of metric being collected."""
    COUNTER = "counter"      # Monotonically increasing
    GAUGE = "gauge"          # Point-in-time value
    HISTOGRAM = "histogram"  # Distribution of values
    TIMER = "timer"          # Duration measurement


@dataclass
class Metric:
    """A single metric data point."""
    name: str
    value: float
    type: MetricType
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)


class MetricsCollector:
    """
    Collects and aggregates metrics.

    Thread-safe metrics collection for monitoring system performance.
    Metrics are stored in-memory and can be exported via exporters.

    Usage:
        collector = MetricsCollector()

        # Count operations
        collector.increment("api.requests", labels={"endpoint": "vrp"})

        # Record values
        collector.gauge("connections.active", 15)

        # Time operations
        with collector.timer("db.query.duration"):
            execute_query()

        # Export metrics
        metrics = collector.get_all_metrics()
    """

    def __init__(self):
        """Initialize metrics collector."""
        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._timers: Dict[str, List[float]] = defaultdict(list)

    def increment(self, name: str, value: float = 1.0, labels: Dict[str, str] | None = None):
        """
        Increment a counter metric.

        Args:
            name: Metric name (e.g., "api.requests.total")
            value: Amount to increment (default: 1.0)
            labels: Optional labels for metric segmentation
        """
        key = self._make_key(name, labels)
        self._counters[key] += value
        logger.debug(f"Incremented counter {key} by {value}")

    def gauge(self, name: str, value: float, labels: Dict[str, str] | None = None):
        """
        Set a gauge metric to a specific value.

        Args:
            name: Metric name (e.g., "connections.pool.active")
            value: Current value
            labels: Optional labels for metric segmentation
        """
        key = self._make_key(name, labels)
        self._gauges[key] = value
        logger.debug(f"Set gauge {key} = {value}")

    def histogram(self, name: str, value: float, labels: Dict[str, str] | None = None):
        """
        Record a value in a histogram.

        Args:
            name: Metric name (e.g., "api.latency.ms")
            value: Observed value
            labels: Optional labels for metric segmentation
        """
        key = self._make_key(name, labels)
        self._histograms[key].append(value)
        logger.debug(f"Recorded histogram value {key} = {value}")

    def timer(self, name: str, labels: Dict[str, str] | None = None):
        """
        Context manager for timing operations.

        Args:
            name: Metric name (e.g., "db.query.duration.ms")
            labels: Optional labels for metric segmentation

        Returns:
            Context manager that records duration in milliseconds

        Usage:
            with collector.timer("operation.duration"):
                do_work()
        """
        return Timer(self, name, labels)

    def get_counter(self, name: str, labels: Dict[str, str] | None = None) -> float:
        """Get current counter value."""
        key = self._make_key(name, labels)
        return self._counters.get(key, 0.0)

    def get_gauge(self, name: str, labels: Dict[str, str] | None = None) -> float | None:
        """Get current gauge value."""
        key = self._make_key(name, labels)
        return self._gauges.get(key)

    def get_histogram_stats(self, name: str, labels: Dict[str, str] | None = None) -> Dict[str, float] | None:
        """
        Get histogram statistics.

        Returns:
            Dict with min, max, mean, median, p95, p99, count
        """
        key = self._make_key(name, labels)
        values = self._histograms.get(key)

        if not values:
            return None

        sorted_values = sorted(values)
        count = len(sorted_values)

        return {
            'count': count,
            'min': min(sorted_values),
            'max': max(sorted_values),
            'mean': sum(sorted_values) / count,
            'median': sorted_values[count // 2],
            'p95': sorted_values[int(count * 0.95)] if count >= 20 else sorted_values[-1],
            'p99': sorted_values[int(count * 0.99)] if count >= 100 else sorted_values[-1],
        }

    def get_all_metrics(self) -> List[Metric]:
        """
        Get all collected metrics.

        Returns:
            List of Metric objects
        """
        metrics = []

        # Counters
        for key, value in self._counters.items():
            name, labels = self._parse_key(key)
            metrics.append(Metric(
                name=name,
                value=value,
                type=MetricType.COUNTER,
                labels=labels
            ))

        # Gauges
        for key, value in self._gauges.items():
            name, labels = self._parse_key(key)
            metrics.append(Metric(
                name=name,
                value=value,
                type=MetricType.GAUGE,
                labels=labels
            ))

        # Histograms (export as summary stats)
        for key, values in self._histograms.items():
            if values:
                name, labels = self._parse_key(key)
                stats = self.get_histogram_stats(name, labels)
                if stats:
                    for stat_name, stat_value in stats.items():
                        metrics.append(Metric(
                            name=f"{name}.{stat_name}",
                            value=stat_value,
                            type=MetricType.HISTOGRAM,
                            labels=labels
                        ))

        return metrics

    def reset(self):
        """Reset all metrics (useful for testing)."""
        self._counters.clear()
        self._gauges.clear()
        self._histograms.clear()
        self._timers.clear()
        logger.info("Metrics collector reset")

    def _make_key(self, name: str, labels: Dict[str, str] | None) -> str:
        """Create a unique key for a metric with labels."""
        if not labels:
            return name

        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def _parse_key(self, key: str) -> tuple[str, Dict[str, str]]:
        """Parse a metric key back into name and labels."""
        if '{' not in key:
            return key, {}

        name, label_str = key.split('{', 1)
        label_str = label_str.rstrip('}')

        labels = {}
        for pair in label_str.split(','):
            if '=' in pair:
                k, v = pair.split('=', 1)
                labels[k] = v

        return name, labels


class Timer:
    """Context manager for timing operations."""

    def __init__(self, collector: MetricsCollector, name: str, labels: Dict[str, str] | None = None):
        """
        Initialize timer.

        Args:
            collector: MetricsCollector instance
            name: Metric name
            labels: Optional labels
        """
        self.collector = collector
        self.name = name
        self.labels = labels
        self.start_time = None

    def __enter__(self):
        """Start timer."""
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop timer and record duration."""
        if self.start_time is not None:
            duration_ms = (time.time() - self.start_time) * 1000
            self.collector.histogram(self.name, duration_ms, self.labels)


# Global metrics collector (optional singleton)
_global_collector: MetricsCollector | None = None


def get_global_collector() -> MetricsCollector:
    """
    Get global metrics collector instance.

    Returns:
        Shared MetricsCollector instance
    """
    global _global_collector
    if _global_collector is None:
        _global_collector = MetricsCollector()
    return _global_collector
