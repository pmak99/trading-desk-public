"""
In-memory cache with TTL support.

Simple L1 cache for MVP. Will be enhanced to HybridCache in Phase 2.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, Any, Dict

logger = logging.getLogger(__name__)

# Cache version for key namespacing
# Increment this to invalidate all caches after schema/format changes
CACHE_VERSION = "v1"


class MemoryCache:
    """
    Thread-safe in-memory cache with TTL.

    Phase: MVP
    Enhancement: Phase 2 will add L2 persistent layer (HybridCache)
    """

    def __init__(self, ttl_seconds: int = 30, max_size: int = 1000):
        """
        Initialize memory cache.

        Args:
            ttl_seconds: Default time-to-live for cache entries
            max_size: Maximum number of entries (LRU eviction)
        """
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._cache: Dict[str, Any] = {}
        self._timestamps: Dict[str, datetime] = {}

    def get(self, key: str) -> Optional[Any]:
        """
        Get cached value by key.

        Args:
            key: Cache key

        Returns:
            Cached value or None if expired/missing
        """
        if key not in self._cache:
            logger.debug(f"Cache MISS: {key}")
            return None

        # Check if expired
        stored_time = self._timestamps.get(key)
        if stored_time is None:
            del self._cache[key]
            return None

        now = datetime.now()
        elapsed = (now - stored_time).total_seconds()

        if elapsed > self.ttl_seconds:
            # Expired
            logger.debug(f"Cache EXPIRED: {key} (age: {elapsed:.1f}s)")
            del self._cache[key]
            del self._timestamps[key]
            return None

        logger.debug(f"Cache HIT: {key} (age: {elapsed:.1f}s)")
        return self._cache[key]

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        Set cached value with optional custom TTL.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Optional custom TTL in seconds
        """
        # Enforce max size (simple LRU: remove oldest)
        if len(self._cache) >= self.max_size and key not in self._cache:
            self._evict_oldest()

        self._cache[key] = value
        self._timestamps[key] = datetime.now()

        effective_ttl = ttl if ttl is not None else self.ttl_seconds
        logger.debug(f"Cache SET: {key} (TTL: {effective_ttl}s)")

    def delete(self, key: str) -> None:
        """Delete cached value."""
        if key in self._cache:
            del self._cache[key]
            del self._timestamps[key]
            logger.debug(f"Cache DELETE: {key}")

    def clear(self) -> None:
        """Clear all cached values."""
        count = len(self._cache)
        self._cache.clear()
        self._timestamps.clear()
        logger.info(f"Cache CLEARED: {count} entries removed")

    def size(self) -> int:
        """Get current cache size."""
        return len(self._cache)

    def _evict_oldest(self) -> None:
        """Evict oldest entry (LRU)."""
        if not self._timestamps:
            return

        oldest_key = min(self._timestamps.items(), key=lambda x: x[1])[0]
        del self._cache[oldest_key]
        del self._timestamps[oldest_key]
        logger.debug(f"Cache EVICTED: {oldest_key} (max size reached)")

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            "size": len(self._cache),
            "max_size": self.max_size,
            "ttl_seconds": self.ttl_seconds,
            "utilization_pct": (len(self._cache) / self.max_size * 100)
            if self.max_size > 0
            else 0,
        }


class CachedOptionsDataProvider:
    """
    Wrapper that adds caching to any OptionsDataProvider.

    Usage:
        provider = TradierAPI(api_key)
        cached_provider = CachedOptionsDataProvider(provider, cache)
    """

    def __init__(self, provider, cache: MemoryCache):
        self.provider = provider
        self.cache = cache

    def get_stock_price(self, ticker: str):
        """Get stock price with caching."""
        # Normalize ticker and use versioned cache key
        ticker_normalized = ticker.upper()
        key = f"{CACHE_VERSION}:stock_price:{ticker_normalized}"
        cached = self.cache.get(key)

        if cached is not None:
            return cached

        result = self.provider.get_stock_price(ticker)
        if result.is_ok:
            self.cache.set(key, result, ttl=30)  # 30 second TTL for prices

        return result

    def get_option_chain(self, ticker: str, expiration):
        """Get option chain with caching."""
        # Normalize ticker and use versioned cache key
        ticker_normalized = ticker.upper()
        key = f"{CACHE_VERSION}:option_chain:{ticker_normalized}:{expiration}"
        cached = self.cache.get(key)

        if cached is not None:
            return cached

        result = self.provider.get_option_chain(ticker, expiration)
        if result.is_ok:
            self.cache.set(key, result, ttl=60)  # 60 second TTL for chains

        return result
