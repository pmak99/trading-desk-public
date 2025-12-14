"""
Metrics collection and Grafana Cloud push.

Uses Grafana Cloud Graphite JSON API for metrics ingestion.
Metrics are pushed asynchronously via ThreadPoolExecutor to avoid
blocking the async event loop.

Environment Variables:
    GRAFANA_GRAPHITE_URL: Graphite metrics endpoint URL
    GRAFANA_USER: Grafana Cloud instance ID (numeric)
    GRAFANA_API_KEY: Grafana Cloud API key (glc_xxx format)

Example Usage:
    # Simple counter
    metrics.count("ivcrush.requests", {"endpoint": "analyze"})

    # Timer context manager
    with metrics.timer("ivcrush.duration", {"op": "fetch"}):
        await do_work()

    # Timer decorator
    @metrics.timed("ivcrush.api.latency", {"provider": "tradier"})
    async def fetch_quote(ticker): ...
"""

import inspect
import json
import os
import time
import httpx
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List
from functools import wraps
from contextlib import contextmanager

from src.core.logging import log

# Thread pool for non-blocking metrics push
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="metrics")


# Grafana Cloud config
GRAFANA_GRAPHITE_URL = os.getenv("GRAFANA_GRAPHITE_URL", "")
GRAFANA_USER = os.getenv("GRAFANA_USER", "")
GRAFANA_API_KEY = os.getenv("GRAFANA_API_KEY", "")

# Default interval for metrics in seconds.
# Grafana Cloud Graphite requires an interval field. 10 seconds is the
# standard scrape interval and matches Grafana's default for time series.
# This affects resolution of rate() calculations in queries.
DEFAULT_INTERVAL = 10


def _is_enabled() -> bool:
    """
    Check if Grafana Cloud is configured.

    Returns True only when all three environment variables are set:
    GRAFANA_GRAPHITE_URL, GRAFANA_USER, and GRAFANA_API_KEY.
    """
    return bool(GRAFANA_GRAPHITE_URL and GRAFANA_USER and GRAFANA_API_KEY)


def _format_tags(tags: dict) -> List[str]:
    """
    Format tags as list of key=value strings for Graphite JSON API.

    Args:
        tags: Dictionary of tag key-value pairs

    Returns:
        List of "key=value" strings, filtering out None values

    Example:
        _format_tags({"endpoint": "analyze", "status": None})
        # Returns: ["endpoint=analyze"]
    """
    if not tags:
        return []
    return [f"{k}={v}" for k, v in tags.items() if v is not None]


def record(name: str, value: float, tags: Optional[dict] = None) -> None:
    """
    Record a metric and push to Grafana Cloud.

    Args:
        name: Metric name (e.g., "ivcrush.request.duration")
        value: Metric value
        tags: Optional tags dict (e.g., {"endpoint": "analyze", "status": "success"})
    """
    if not _is_enabled():
        return

    try:
        metric = {
            "name": name,
            "interval": DEFAULT_INTERVAL,
            "value": value,
            "time": int(time.time()),
        }

        tag_list = _format_tags(tags or {})
        if tag_list:
            metric["tags"] = tag_list

        # Push in background thread (non-blocking)
        _executor.submit(_push_metric, [metric])

    except Exception as e:
        # Don't let metrics failures break the app
        log("warn", "Metrics record failed", error=str(e), metric=name)


def _push_metric(metrics: List[dict]) -> None:
    """
    Push metrics to Grafana Cloud Graphite endpoint.

    Called from ThreadPoolExecutor to avoid blocking async event loop.
    Uses Basic auth with GRAFANA_USER:GRAFANA_API_KEY credentials.

    Args:
        metrics: List of metric dicts with name, interval, value, time, and optional tags

    Note:
        Failures are logged but never raised - metrics should not break the app.
    """
    if not _is_enabled():
        log("debug", "Metrics disabled", url=bool(GRAFANA_GRAPHITE_URL), user=bool(GRAFANA_USER), key=bool(GRAFANA_API_KEY))
        return

    try:
        # Use short timeout, runs in background thread
        with httpx.Client(timeout=2.0) as client:
            response = client.post(
                GRAFANA_GRAPHITE_URL,
                content=json.dumps(metrics),
                auth=(GRAFANA_USER, GRAFANA_API_KEY),  # httpx handles Basic auth
                headers={"Content-Type": "application/json"},
            )
            if response.status_code >= 400:
                log("warn", "Metrics push failed",
                    status=response.status_code,
                    body=response.text[:100])
            else:
                log("debug", "Metrics pushed", count=len(metrics))
    except Exception as e:
        # Log at warn level so we can see metrics failures in production
        log("warn", "Metrics push error", error=str(e))


@contextmanager
def timer(name: str, tags: Optional[dict] = None):
    """
    Context manager to time a block and record duration.

    Usage:
        with metrics.timer("ivcrush.request.duration", {"endpoint": "analyze"}):
            do_work()
    """
    start = time.time()
    try:
        yield
    finally:
        duration_ms = (time.time() - start) * 1000
        record(name, duration_ms, tags)


def timed(name: str, tags: Optional[dict] = None):
    """
    Decorator to time a function and record duration.

    Usage:
        @metrics.timed("ivcrush.api.latency", {"provider": "tradier"})
        async def get_quote(ticker):
            ...
    """
    def decorator(func):
        # Use inspect.iscoroutinefunction for reliable async detection
        if inspect.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                start = time.time()
                try:
                    return await func(*args, **kwargs)
                finally:
                    duration_ms = (time.time() - start) * 1000
                    record(name, duration_ms, tags)
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                start = time.time()
                try:
                    return func(*args, **kwargs)
                finally:
                    duration_ms = (time.time() - start) * 1000
                    record(name, duration_ms, tags)
            return sync_wrapper
    return decorator


def count(name: str, tags: Optional[dict] = None, value: float = 1) -> None:
    """Record a count metric (convenience wrapper)."""
    record(name, value, tags)


def gauge(name: str, value: float, tags: Optional[dict] = None) -> None:
    """Record a gauge metric (convenience wrapper)."""
    record(name, value, tags)


# Pre-defined metric helpers
def request_success(endpoint: str, duration_ms: float) -> None:
    """Record successful request."""
    record("ivcrush.request.duration", duration_ms, {"endpoint": endpoint})
    count("ivcrush.request.status", {"endpoint": endpoint, "status": "success"})


def request_error(endpoint: str, duration_ms: float, error_type: str = "error") -> None:
    """Record failed request."""
    record("ivcrush.request.duration", duration_ms, {"endpoint": endpoint})
    count("ivcrush.request.status", {"endpoint": endpoint, "status": error_type})


def vrp_analyzed(ticker: str, ratio: float, tier: str) -> None:
    """Record VRP analysis."""
    gauge("ivcrush.vrp.ratio", ratio, {"ticker": ticker})
    count("ivcrush.vrp.tier", {"tier": tier})


def liquidity_checked(tier: str) -> None:
    """Record liquidity tier."""
    count("ivcrush.liquidity.tier", {"tier": tier})


def sentiment_fetched(ticker: str, score: float) -> None:
    """Record sentiment analysis."""
    gauge("ivcrush.sentiment.score", score, {"ticker": ticker})


def api_call(provider: str, duration_ms: float, success: bool = True) -> None:
    """Record external API call."""
    count("ivcrush.api.calls", {"provider": provider, "status": "success" if success else "error"})
    record("ivcrush.api.latency", duration_ms, {"provider": provider})


def budget_update(remaining_calls: int, remaining_dollars: float) -> None:
    """Record budget status."""
    gauge("ivcrush.budget.calls_remaining", remaining_calls)
    gauge("ivcrush.budget.dollars_remaining", remaining_dollars)


def tickers_qualified(count_val: int) -> None:
    """Record number of qualified tickers from a scan."""
    gauge("ivcrush.tickers.qualified", count_val)
