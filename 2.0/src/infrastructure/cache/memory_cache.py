"""
In-memory cache with TTL support.

Simple L1 cache for MVP. Will be enhanced to HybridCache in Phase 2.
"""

import logging
from datetime import date, datetime, timedelta
from threading import Lock
from typing import Optional, Any, Dict

from src.domain.errors import Ok, Err, AppError, ErrorCode

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
        self._lock = Lock()  # Thread safety for all cache operations

    def get(self, key: str) -> Optional[Any]:
        """
        Get cached value by key.

        Args:
            key: Cache key

        Returns:
            Cached value or None if expired/missing
        """
        with self._lock:
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
        with self._lock:
            # Enforce max size (simple LRU: remove oldest entries until under limit)
            while len(self._cache) >= self.max_size and key not in self._cache:
                self._evict_oldest()

            self._cache[key] = value
            self._timestamps[key] = datetime.now()

            effective_ttl = ttl if ttl is not None else self.ttl_seconds
            logger.debug(f"Cache SET: {key} (TTL: {effective_ttl}s)")

    def delete(self, key: str) -> None:
        """Delete cached value."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                del self._timestamps[key]
                logger.debug(f"Cache DELETE: {key}")

    def clear(self) -> None:
        """Clear all cached values."""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            self._timestamps.clear()
            logger.info(f"Cache CLEARED: {count} entries removed")

    def size(self) -> int:
        """Get current cache size."""
        with self._lock:
            return len(self._cache)

    def _evict_oldest(self) -> None:
        """
        Evict oldest entry (LRU).

        Note: Called with lock already held by set().
        """
        if not self._timestamps:
            return

        oldest_key = min(self._timestamps.items(), key=lambda x: x[1])[0]
        del self._cache[oldest_key]
        del self._timestamps[oldest_key]
        logger.debug(f"Cache EVICTED: {oldest_key} (max size reached)")

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            size = len(self._cache)
            return {
                "size": size,
                "max_size": self.max_size,
                "ttl_seconds": self.ttl_seconds,
                "utilization_pct": (size / self.max_size * 100)
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

        # Inject the cached stock price so the provider skips its internal
        # (uncached) price fetch — one rate-limit token saved per chain call
        # after the first for a ticker.
        stock_price = None
        price_key = f"{CACHE_VERSION}:stock_price:{ticker_normalized}"
        cached_price = self.cache.get(price_key)
        if cached_price is not None and cached_price.is_ok:
            stock_price = cached_price.value

        result = self.provider.get_option_chain(
            ticker, expiration, stock_price=stock_price
        )
        if result.is_ok:
            self.cache.set(key, result, ttl=60)  # 60 second TTL for chains
            # A fresh chain fetch also fetched a fresh price — cache it
            if stock_price is None:
                self.cache.set(
                    price_key, Ok(result.value.stock_price), ttl=30
                )

        return result

    def get_expirations(self, ticker: str):
        """Get available option expirations with caching (listings change rarely)."""
        ticker_normalized = ticker.upper()
        key = f"{CACHE_VERSION}:expirations:{ticker_normalized}"
        cached = self.cache.get(key)

        if cached is not None:
            return cached

        result = self.provider.get_expirations(ticker)
        if result.is_ok:
            self.cache.set(key, result, ttl=3600)  # 1 hour TTL for listings

        return result

    def warm_stock_prices(self, tickers: list, ttl: int = 300) -> int:
        """
        Pre-fetch stock prices for many tickers via the batch quote endpoint
        and seed the price cache.

        One batch call covers up to 100 tickers for a single rate-limit
        token, versus one token per ticker fetched individually. Intended
        for scan startup; the longer TTL (default 300s) accepts that a
        screening pass tolerates a few minutes of price staleness — the
        alternative is each ticker re-fetching mid-scan anyway.

        Returns:
            Number of prices cached (0 if the provider lacks batch support
            or every batch call failed — individual fetches then take over).
        """
        batch_fetch = getattr(self.provider, 'get_stock_prices_batch', None)
        if batch_fetch is None or not tickers:
            return 0

        warmed = 0
        for i in range(0, len(tickers), 100):
            batch = tickers[i:i + 100]
            result = batch_fetch(batch)
            if result.is_err:
                logger.warning(f"Price pre-warm batch failed: {result.error}")
                continue
            for ticker, price in result.value.items():
                key = f"{CACHE_VERSION}:stock_price:{ticker.upper()}"
                self.cache.set(key, Ok(price), ttl=ttl)
                warmed += 1

        if warmed:
            logger.info(f"Pre-warmed {warmed}/{len(tickers)} stock prices")
        return warmed

    def find_nearest_expiration(self, ticker: str, target_date: date):
        """Find the nearest available expiration >= target_date, via cached expirations."""
        expirations_result = self.get_expirations(ticker)
        if expirations_result.is_err:
            return Err(expirations_result.error)

        expirations = sorted(expirations_result.value)

        nearest = None
        for exp in expirations:
            if exp >= target_date:
                nearest = exp
                break

        if nearest is None:
            if expirations:
                nearest = expirations[-1]
                logger.warning(
                    f"{ticker}: No expiration >= {target_date}, using {nearest}"
                )
            else:
                return Err(
                    AppError(ErrorCode.NODATA, f"No expirations for {ticker}")
                )

        if nearest != target_date:
            logger.info(f"{ticker}: Adjusted expiration {target_date} → {nearest}")

        return Ok(nearest)
