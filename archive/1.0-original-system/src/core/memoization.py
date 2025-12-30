"""
Memoization utilities for caching expensive function calls.

Provides decorators and caching strategies for improving performance
of frequently called functions with deterministic results.
"""

import functools
import hashlib
import json
import logging
import threading
from typing import Any, Callable, Dict, Optional, TypeVar, cast
import numpy as np

logger = logging.getLogger(__name__)

F = TypeVar('F', bound=Callable[..., Any])


def _json_serializer(obj: Any) -> Any:
    """
    Custom JSON serializer for memoization key generation.

    Handles numpy types from pandas/yfinance data.
    """
    if isinstance(obj, (np.integer, np.floating)):
        return obj.item()  # Convert to native Python type
    elif isinstance(obj, np.ndarray):
        return obj.tolist()  # Convert arrays to lists
    else:
        return str(obj)


def memoize(maxsize: int = 128) -> Callable[[F], F]:
    """
    Memoization decorator with LRU cache for scorer functions.

    Caches function results based on arguments to avoid redundant calculations.
    Particularly useful for scoring functions that are called multiple times
    with the same ticker data.

    Args:
        maxsize: Maximum cache size (default: 128). Use None for unlimited.

    Returns:
        Decorated function with caching

    Example:
        @memoize(maxsize=256)
        def calculate_iv_rank(ticker: str, iv: float) -> float:
            # Expensive calculation
            return result
    """
    def decorator(func: F) -> F:
        # Use functools.lru_cache for built-in LRU eviction
        cached_func = functools.lru_cache(maxsize=maxsize)(func)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return cached_func(*args, **kwargs)
            except TypeError:
                # If args aren't hashable (e.g., dicts), fall back to no caching
                logger.debug(f"Memoization skipped for {func.__name__} - unhashable args")
                return func(*args, **kwargs)

        # Expose cache_info and cache_clear for debugging
        wrapper.cache_info = cached_func.cache_info  # type: ignore
        wrapper.cache_clear = cached_func.cache_clear  # type: ignore

        return cast(F, wrapper)

    return decorator


def memoize_with_dict_key(maxsize: int = 128) -> Callable[[F], F]:
    """
    Memoization decorator that can handle dictionary arguments.

    Converts dict arguments to JSON strings for hashing. Useful for
    scorer functions that accept TickerData dictionaries.

    Args:
        maxsize: Maximum cache size (default: 128)

    Returns:
        Decorated function with caching

    Example:
        @memoize_with_dict_key(maxsize=256)
        def score_ticker(data: TickerData) -> float:
            # Expensive scoring calculation
            return result
    """
    def decorator(func: F) -> F:
        cache: Dict[str, Any] = {}
        cache_order: list = []  # Track insertion order for LRU
        cache_lock = threading.Lock()  # Thread-safety for concurrent access

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # Create cache key from arguments
            cache_key = _make_cache_key(args, kwargs)

            # Check cache
            with cache_lock:
                if cache_key in cache:
                    # Move to end (most recently used)
                    cache_order.remove(cache_key)
                    cache_order.append(cache_key)
                    return cache[cache_key]

            # Compute result (outside lock to allow concurrent computation)
            result = func(*args, **kwargs)

            # Store in cache
            with cache_lock:
                cache[cache_key] = result
                cache_order.append(cache_key)

                # Evict oldest if over maxsize
                if maxsize is not None and len(cache) > maxsize:
                    oldest_key = cache_order.pop(0)
                    del cache[oldest_key]

            return result

        def cache_info() -> Dict[str, int]:
            """Get cache statistics."""
            with cache_lock:
                return {
                    'size': len(cache),
                    'maxsize': maxsize or -1,
                    'hits': 0,  # Would need additional tracking
                    'misses': 0
                }

        def cache_clear() -> None:
            """Clear the cache."""
            with cache_lock:
                cache.clear()
                cache_order.clear()

        wrapper.cache_info = cache_info  # type: ignore
        wrapper.cache_clear = cache_clear  # type: ignore

        return cast(F, wrapper)

    return decorator


def _make_cache_key(args: tuple, kwargs: Dict[str, Any]) -> str:
    """
    Create a cache key from function arguments.

    Handles both hashable and dict arguments by converting to JSON.

    Args:
        args: Positional arguments
        kwargs: Keyword arguments

    Returns:
        Cache key string
    """
    try:
        # Try simple tuple hashing first (fastest)
        return str(hash((args, tuple(sorted(kwargs.items())))))
    except TypeError:
        # Fall back to JSON serialization for complex types
        key_parts = []

        for arg in args:
            if isinstance(arg, dict):
                # Sort dict keys for consistent hashing
                # Use custom serializer to handle numpy types
                key_parts.append(json.dumps(arg, sort_keys=True, default=_json_serializer))
            elif hasattr(arg, '__dict__'):
                # Handle objects with __dict__
                key_parts.append(json.dumps(arg.__dict__, sort_keys=True, default=_json_serializer))
            else:
                key_parts.append(str(arg))

        for key, value in sorted(kwargs.items()):
            if isinstance(value, dict):
                # Use custom serializer to handle numpy types
                key_parts.append(f"{key}:{json.dumps(value, sort_keys=True, default=_json_serializer)}")
            else:
                key_parts.append(f"{key}:{value}")

        # Hash the combined string for consistent length
        combined = '|'.join(key_parts)
        return hashlib.md5(combined.encode()).hexdigest()


class MemoizedProperty:
    """
    Descriptor for memoized properties.

    Computes the property value once and caches it on the instance.
    Useful for expensive property calculations that don't change.

    Example:
        class Analyzer:
            @MemoizedProperty
            def expensive_calculation(self) -> float:
                # Only computed once per instance
                return sum(range(1000000))
    """

    def __init__(self, func: Callable[[Any], Any]) -> None:
        self.func = func
        self.attr_name = f'_memoized_{func.__name__}'

    def __get__(self, obj: Any, objtype: Optional[type] = None) -> Any:
        if obj is None:
            return self

        # Check if already computed
        if not hasattr(obj, self.attr_name):
            # Compute and cache
            value = self.func(obj)
            setattr(obj, self.attr_name, value)

        return getattr(obj, self.attr_name)

    def __set__(self, obj: Any, value: Any) -> None:
        # Allow setting the cached value
        setattr(obj, self.attr_name, value)


def cache_result_by_ticker(maxsize: int = 100) -> Callable[[F], F]:
    """
    Specialized memoization for ticker-based functions.

    Caches results based on the ticker symbol (first argument).
    Useful for API calls and data fetching functions.

    Args:
        maxsize: Maximum number of tickers to cache

    Returns:
        Decorated function with ticker-based caching

    Example:
        @cache_result_by_ticker(maxsize=200)
        def fetch_ticker_data(ticker: str, date: str) -> TickerData:
            # Expensive API call
            return data
    """
    def decorator(func: F) -> F:
        cache: Dict[str, Any] = {}
        cache_order: list = []
        cache_lock = threading.Lock()  # Thread-safety for concurrent access

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            # First argument should be ticker symbol
            if not args:
                return func(*args, **kwargs)

            ticker = str(args[0])

            # Create cache key with ticker + other args
            cache_key = f"{ticker}:{_make_cache_key(args[1:], kwargs)}"

            # Check cache
            with cache_lock:
                if cache_key in cache:
                    cache_order.remove(cache_key)
                    cache_order.append(cache_key)
                    logger.debug(f"Cache hit for ticker: {ticker}")
                    return cache[cache_key]

            # Compute result (outside lock to allow concurrent computation)
            result = func(*args, **kwargs)

            # Store in cache
            with cache_lock:
                cache[cache_key] = result
                cache_order.append(cache_key)

                # Evict oldest if over maxsize
                if len(cache) > maxsize:
                    oldest_key = cache_order.pop(0)
                    del cache[oldest_key]
                    logger.debug(f"Evicted oldest entry from ticker cache")

            return result

        def cache_info() -> Dict[str, int]:
            with cache_lock:
                return {'size': len(cache), 'maxsize': maxsize}

        def cache_clear() -> None:
            with cache_lock:
                cache.clear()
                cache_order.clear()

        wrapper.cache_info = cache_info  # type: ignore
        wrapper.cache_clear = cache_clear  # type: ignore

        return cast(F, wrapper)

    return decorator
