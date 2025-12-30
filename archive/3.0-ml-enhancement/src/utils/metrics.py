"""
Prometheus-compatible metrics collection for 3.0 system.

Provides counters, gauges, and histograms for monitoring.
"""

import time
import threading
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from collections import defaultdict
from contextlib import contextmanager
from functools import wraps

__all__ = [
    'Counter',
    'Gauge',
    'Histogram',
    'MetricsRegistry',
    'get_registry',
    'timed_histogram',
]


@dataclass
class Counter:
    """
    A monotonically increasing counter.

    Example:
        requests = Counter('api_requests_total', 'Total API requests')
        requests.inc()
        requests.inc(5)
    """
    name: str
    description: str
    labels: Dict[str, str] = field(default_factory=dict)
    _value: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def inc(self, amount: float = 1.0) -> None:
        """Increment the counter."""
        with self._lock:
            self._value += amount

    def get(self) -> float:
        """Get current value."""
        with self._lock:
            return self._value

    def labels(self, **kwargs) -> 'Counter':
        """Create a new counter with labels."""
        return Counter(
            name=self.name,
            description=self.description,
            labels={**self.labels, **kwargs}
        )


@dataclass
class Gauge:
    """
    A value that can go up or down.

    Example:
        temperature = Gauge('temperature', 'Current temperature')
        temperature.set(25.5)
        temperature.inc()
        temperature.dec(2)
    """
    name: str
    description: str
    labels: Dict[str, str] = field(default_factory=dict)
    _value: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def set(self, value: float) -> None:
        """Set the gauge value."""
        with self._lock:
            self._value = value

    def inc(self, amount: float = 1.0) -> None:
        """Increment the gauge."""
        with self._lock:
            self._value += amount

    def dec(self, amount: float = 1.0) -> None:
        """Decrement the gauge."""
        with self._lock:
            self._value -= amount

    def get(self) -> float:
        """Get current value."""
        with self._lock:
            return self._value

    @contextmanager
    def track_inprogress(self):
        """Context manager to track in-progress operations."""
        self.inc()
        try:
            yield
        finally:
            self.dec()


@dataclass
class Histogram:
    """
    Track distribution of values.

    Example:
        latency = Histogram('request_latency', 'Request latency in ms')
        latency.observe(45.2)

        with latency.time():
            do_something()
    """
    name: str
    description: str
    labels: Dict[str, str] = field(default_factory=dict)
    buckets: List[float] = field(default_factory=lambda: [
        0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0
    ])
    _counts: Dict[float, int] = field(default_factory=lambda: defaultdict(int))
    _sum: float = 0.0
    _count: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def observe(self, value: float) -> None:
        """Observe a value."""
        with self._lock:
            self._sum += value
            self._count += 1

            for bucket in self.buckets:
                if value <= bucket:
                    self._counts[bucket] += 1

    @contextmanager
    def time(self):
        """Context manager to time an operation."""
        start = time.time()
        try:
            yield
        finally:
            self.observe(time.time() - start)

    def get_stats(self) -> Dict:
        """Get histogram statistics."""
        with self._lock:
            return {
                'sum': self._sum,
                'count': self._count,
                'avg': self._sum / self._count if self._count > 0 else 0,
                'buckets': dict(self._counts)
            }


class MetricsRegistry:
    """
    Central registry for all metrics.

    Example:
        registry = MetricsRegistry()
        requests = registry.counter('api_requests', 'Total requests')
        latency = registry.histogram('api_latency', 'Request latency')
    """

    def __init__(self):
        self._metrics: Dict[str, object] = {}
        self._lock = threading.Lock()

    def counter(self, name: str, description: str = "") -> Counter:
        """Get or create a counter."""
        with self._lock:
            if name not in self._metrics:
                self._metrics[name] = Counter(name, description)
            return self._metrics[name]

    def gauge(self, name: str, description: str = "") -> Gauge:
        """Get or create a gauge."""
        with self._lock:
            if name not in self._metrics:
                self._metrics[name] = Gauge(name, description)
            return self._metrics[name]

    def histogram(self, name: str, description: str = "", buckets: List[float] = None) -> Histogram:
        """Get or create a histogram."""
        with self._lock:
            if name not in self._metrics:
                kwargs = {'name': name, 'description': description}
                if buckets:
                    kwargs['buckets'] = buckets
                self._metrics[name] = Histogram(**kwargs)
            return self._metrics[name]

    def get_all(self) -> Dict[str, Dict]:
        """Get all metrics as a dict."""
        result = {}
        with self._lock:
            for name, metric in self._metrics.items():
                if isinstance(metric, Counter):
                    result[name] = {
                        'type': 'counter',
                        'value': metric.get(),
                        'description': metric.description
                    }
                elif isinstance(metric, Gauge):
                    result[name] = {
                        'type': 'gauge',
                        'value': metric.get(),
                        'description': metric.description
                    }
                elif isinstance(metric, Histogram):
                    stats = metric.get_stats()
                    result[name] = {
                        'type': 'histogram',
                        'description': metric.description,
                        **stats
                    }
        return result

    def export_prometheus(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = []
        with self._lock:
            for name, metric in self._metrics.items():
                if isinstance(metric, Counter):
                    if metric.description:
                        lines.append(f"# HELP {name} {metric.description}")
                    lines.append(f"# TYPE {name} counter")
                    lines.append(f"{name} {metric.get()}")

                elif isinstance(metric, Gauge):
                    if metric.description:
                        lines.append(f"# HELP {name} {metric.description}")
                    lines.append(f"# TYPE {name} gauge")
                    lines.append(f"{name} {metric.get()}")

                elif isinstance(metric, Histogram):
                    if metric.description:
                        lines.append(f"# HELP {name} {metric.description}")
                    lines.append(f"# TYPE {name} histogram")
                    stats = metric.get_stats()
                    for bucket, count in sorted(stats['buckets'].items()):
                        lines.append(f'{name}_bucket{{le="{bucket}"}} {count}')
                    lines.append(f'{name}_bucket{{le="+Inf"}} {stats["count"]}')
                    lines.append(f"{name}_sum {stats['sum']}")
                    lines.append(f"{name}_count {stats['count']}")

                lines.append("")

        return "\n".join(lines)


# Global registry
_registry: Optional[MetricsRegistry] = None


def get_registry() -> MetricsRegistry:
    """Get the global metrics registry."""
    global _registry
    if _registry is None:
        _registry = MetricsRegistry()
    return _registry


def timed_histogram(histogram: Histogram):
    """
    Decorator to time function execution.

    Example:
        latency = registry.histogram('api_latency', 'API latency')

        @timed_histogram(latency)
        def api_call():
            ...
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            with histogram.time():
                return func(*args, **kwargs)
        return wrapper
    return decorator


# Pre-defined metrics for 3.0 system
class SystemMetrics:
    """Pre-configured metrics for 3.0 system."""

    def __init__(self, registry: MetricsRegistry = None):
        self.registry = registry or get_registry()

        # Scan metrics
        self.scans_total = self.registry.counter(
            'scanner_scans_total',
            'Total number of ticker scans'
        )
        self.scan_duration = self.registry.histogram(
            'scanner_scan_duration_seconds',
            'Time to scan a single ticker',
            buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
        )
        self.scans_in_progress = self.registry.gauge(
            'scanner_scans_in_progress',
            'Number of scans currently in progress'
        )

        # API metrics
        self.api_requests = self.registry.counter(
            'api_requests_total',
            'Total API requests'
        )
        self.api_errors = self.registry.counter(
            'api_errors_total',
            'Total API errors'
        )
        self.api_latency = self.registry.histogram(
            'api_latency_seconds',
            'API request latency',
            buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
        )

        # VRP metrics
        self.vrp_distribution = self.registry.histogram(
            'vrp_ratio',
            'Distribution of VRP ratios',
            buckets=[0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0]
        )

        # ML metrics
        self.ml_predictions = self.registry.counter(
            'ml_predictions_total',
            'Total ML predictions made'
        )
        self.ml_prediction_latency = self.registry.histogram(
            'ml_prediction_latency_seconds',
            'ML prediction latency'
        )

        # Circuit breaker metrics
        self.circuit_opens = self.registry.counter(
            'circuit_breaker_opens_total',
            'Number of times circuit breaker opened'
        )
