"""
Prometheus-compatible metrics for IV Crush 2.0.

Provides lightweight metrics collection without requiring the full
prometheus_client library. Metrics are exposed as JSON for easy
integration with monitoring systems.

Usage:
    from src.utils.metrics import metrics

    # Counter
    metrics.increment('scans_total', labels={'mode': 'whisper'})

    # Gauge
    metrics.set_gauge('active_positions', 5)

    # Histogram (timing)
    with metrics.timer('api_latency', labels={'api': 'tradier'}):
        response = tradier.get_option_chain(...)

    # Get all metrics as JSON
    metrics.to_json()

    # Get Prometheus text format
    metrics.to_prometheus()
"""

import json
import time
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any


@dataclass
class MetricValue:
    """Single metric value with labels."""
    name: str
    value: float
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    metric_type: str = "gauge"  # counter, gauge, histogram


@dataclass
class HistogramBucket:
    """Histogram bucket for latency tracking."""
    le: float  # less than or equal
    count: int = 0


class MetricsRegistry:
    """
    Thread-safe metrics registry.

    Collects counters, gauges, and histograms for application metrics.
    Designed to be lightweight - no external dependencies.
    """

    # Default histogram buckets for latency (in seconds)
    DEFAULT_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]

    def __init__(self):
        """Initialize metrics registry."""
        self._lock = threading.Lock()
        self._counters: Dict[str, Dict[str, float]] = {}
        self._gauges: Dict[str, Dict[str, float]] = {}
        self._histograms: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._start_time = time.time()

    def _labels_key(self, labels: Optional[Dict[str, str]]) -> str:
        """Convert labels dict to hashable key."""
        if not labels:
            return ""
        return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))

    def increment(
        self,
        name: str,
        value: float = 1.0,
        labels: Optional[Dict[str, str]] = None
    ) -> None:
        """
        Increment a counter.

        Args:
            name: Metric name
            value: Amount to increment (default 1)
            labels: Optional label dict
        """
        key = self._labels_key(labels)
        with self._lock:
            if name not in self._counters:
                self._counters[name] = {}
            if key not in self._counters[name]:
                self._counters[name][key] = 0.0
            self._counters[name][key] += value

    def set_gauge(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None
    ) -> None:
        """
        Set a gauge value.

        Args:
            name: Metric name
            value: Current value
            labels: Optional label dict
        """
        key = self._labels_key(labels)
        with self._lock:
            if name not in self._gauges:
                self._gauges[name] = {}
            self._gauges[name][key] = value

    def observe(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
        buckets: Optional[List[float]] = None
    ) -> None:
        """
        Record a value in a histogram.

        Args:
            name: Metric name
            value: Observed value
            labels: Optional label dict
            buckets: Custom bucket boundaries (uses defaults if None)
        """
        key = self._labels_key(labels)
        if buckets is None:
            buckets = self.DEFAULT_BUCKETS

        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = {}
            if key not in self._histograms[name]:
                self._histograms[name][key] = {
                    'buckets': {str(b): 0 for b in buckets},
                    'sum': 0.0,
                    'count': 0,
                }

            hist = self._histograms[name][key]
            hist['sum'] += value
            hist['count'] += 1

            # Update bucket counts
            for bucket in buckets:
                if value <= bucket:
                    hist['buckets'][str(bucket)] += 1

    @contextmanager
    def timer(
        self,
        name: str,
        labels: Optional[Dict[str, str]] = None
    ):
        """
        Context manager for timing operations.

        Args:
            name: Metric name
            labels: Optional label dict

        Yields:
            None

        Example:
            with metrics.timer('api_latency', labels={'api': 'tradier'}):
                response = api.call()
        """
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            self.observe(name, elapsed, labels)

    def get_counter(
        self,
        name: str,
        labels: Optional[Dict[str, str]] = None
    ) -> float:
        """Get current counter value."""
        key = self._labels_key(labels)
        with self._lock:
            return self._counters.get(name, {}).get(key, 0.0)

    def get_gauge(
        self,
        name: str,
        labels: Optional[Dict[str, str]] = None
    ) -> float:
        """Get current gauge value."""
        key = self._labels_key(labels)
        with self._lock:
            return self._gauges.get(name, {}).get(key, 0.0)

    def to_dict(self) -> Dict[str, Any]:
        """
        Export all metrics as a dictionary.

        Returns:
            Dict containing all metric values
        """
        with self._lock:
            return {
                'uptime_seconds': time.time() - self._start_time,
                'timestamp': datetime.now().isoformat(),
                'counters': dict(self._counters),
                'gauges': dict(self._gauges),
                'histograms': dict(self._histograms),
            }

    def to_json(self) -> str:
        """
        Export all metrics as JSON string.

        Returns:
            JSON string of all metrics
        """
        return json.dumps(self.to_dict(), indent=2)

    def to_prometheus(self) -> str:
        """
        Export metrics in Prometheus text format.

        Returns:
            Prometheus-compatible text format
        """
        lines = []
        timestamp = int(time.time() * 1000)

        with self._lock:
            # Uptime
            lines.append("# HELP ivcrush_uptime_seconds Time since metrics started")
            lines.append("# TYPE ivcrush_uptime_seconds gauge")
            lines.append(f"ivcrush_uptime_seconds {time.time() - self._start_time:.3f}")
            lines.append("")

            # Counters
            for name, values in self._counters.items():
                lines.append(f"# HELP ivcrush_{name} Counter metric")
                lines.append(f"# TYPE ivcrush_{name} counter")
                for labels_key, value in values.items():
                    label_str = f"{{{labels_key}}}" if labels_key else ""
                    lines.append(f"ivcrush_{name}{label_str} {value}")
                lines.append("")

            # Gauges
            for name, values in self._gauges.items():
                lines.append(f"# HELP ivcrush_{name} Gauge metric")
                lines.append(f"# TYPE ivcrush_{name} gauge")
                for labels_key, value in values.items():
                    label_str = f"{{{labels_key}}}" if labels_key else ""
                    lines.append(f"ivcrush_{name}{label_str} {value}")
                lines.append("")

            # Histograms
            for name, values in self._histograms.items():
                lines.append(f"# HELP ivcrush_{name} Histogram metric")
                lines.append(f"# TYPE ivcrush_{name} histogram")
                for labels_key, hist_data in values.items():
                    base_label = f"{{{labels_key}}}" if labels_key else ""

                    # Bucket lines
                    cumulative = 0
                    for le, count in sorted(hist_data['buckets'].items(), key=lambda x: float(x[0])):
                        cumulative += count
                        if labels_key:
                            lines.append(f'ivcrush_{name}_bucket{{{labels_key},le="{le}"}} {cumulative}')
                        else:
                            lines.append(f'ivcrush_{name}_bucket{{le="{le}"}} {cumulative}')

                    # +Inf bucket
                    if labels_key:
                        lines.append(f'ivcrush_{name}_bucket{{{labels_key},le="+Inf"}} {hist_data["count"]}')
                    else:
                        lines.append(f'ivcrush_{name}_bucket{{le="+Inf"}} {hist_data["count"]}')

                    # Sum and count
                    lines.append(f"ivcrush_{name}_sum{base_label} {hist_data['sum']:.6f}")
                    lines.append(f"ivcrush_{name}_count{base_label} {hist_data['count']}")
                lines.append("")

        return "\n".join(lines)

    def reset(self) -> None:
        """Reset all metrics."""
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._start_time = time.time()


# Global metrics instance
metrics = MetricsRegistry()


# Convenience decorators
def track_latency(name: str, labels: Optional[Dict[str, str]] = None):
    """
    Decorator to track function latency.

    Args:
        name: Metric name
        labels: Optional label dict

    Example:
        @track_latency('api_call', labels={'api': 'tradier'})
        def get_option_chain(symbol):
            ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            with metrics.timer(name, labels):
                return func(*args, **kwargs)
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator


def count_calls(name: str, labels: Optional[Dict[str, str]] = None):
    """
    Decorator to count function calls.

    Args:
        name: Metric name
        labels: Optional label dict

    Example:
        @count_calls('scans_total', labels={'mode': 'whisper'})
        def run_whisper_scan():
            ...
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            metrics.increment(name, labels=labels)
            return func(*args, **kwargs)
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator
