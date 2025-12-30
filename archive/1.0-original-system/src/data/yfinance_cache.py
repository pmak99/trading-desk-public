"""
Simple TTL cache for yfinance data to reduce redundant API calls.

Caches .info results for 15 minutes to avoid hammering yfinance
during development/testing with the same tickers.

Thread-safe implementation with LRU eviction when size limit is reached.
"""

import logging
import threading
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class YFinanceCache:
    """
    Thread-safe time-to-live (TTL) cache for yfinance ticker info.

    Features:
    - Thread-safe with locks for concurrent access
    - TTL-based expiration (default: 15 minutes)
    - LRU eviction when max_size is reached
    - Reduces API calls for repeated queries
    - Faster responses for cached data (instant vs 200-400ms)

    Example:
        cache = YFinanceCache(ttl_minutes=15, max_size=1000)
        info = cache.get_info(ticker)
        if not info:
            info = yf.Ticker(ticker).info
            cache.set_info(ticker, info)
    """

    def __init__(self, ttl_minutes: int = 15, max_size: int = 1000):
        """
        Initialize cache.

        Args:
            ttl_minutes: Time-to-live in minutes (default: 15)
            max_size: Maximum cache entries (default: 1000, LRU eviction)
        """
        self.ttl = timedelta(minutes=ttl_minutes)
        self.max_size = max_size
        self._cache: OrderedDict[str, tuple] = OrderedDict()  # {ticker: (info, timestamp)}
        self._lock = threading.Lock()  # Thread safety
        self._hits = 0
        self._misses = 0

    def get_info(self, ticker: str) -> Optional[Dict]:
        """
        Get cached info for ticker if available and not expired.

        Thread-safe operation with atomic check-then-act.

        Args:
            ticker: Ticker symbol

        Returns:
            Cached info dict or None if not cached/expired
        """
        ticker = ticker.upper()

        with self._lock:  # THREAD SAFETY: Atomic operation
            if ticker not in self._cache:
                self._misses += 1
                return None

            info, timestamp = self._cache[ticker]

            # Check if expired
            if datetime.now() - timestamp > self.ttl:
                # Expired - remove from cache
                del self._cache[ticker]
                self._misses += 1
                logger.debug(f"Cache expired for {ticker}")
                return None

            # Move to end (mark as recently used for LRU)
            self._cache.move_to_end(ticker)
            self._hits += 1
            logger.debug(f"Cache hit for {ticker} (age: {(datetime.now() - timestamp).seconds}s)")

            # Return copy to avoid external mutation
            return info.copy() if isinstance(info, dict) else info

    def set_info(self, ticker: str, info: Dict):
        """
        Cache info for ticker with LRU eviction.

        Thread-safe operation. Evicts least recently used entry if max_size reached.

        Args:
            ticker: Ticker symbol
            info: yfinance .info dict
        """
        ticker = ticker.upper()

        with self._lock:  # THREAD SAFETY: Atomic operation
            # Update existing entry (move to end)
            if ticker in self._cache:
                self._cache.move_to_end(ticker)
                self._cache[ticker] = (info, datetime.now())
                logger.debug(f"Updated cache for {ticker}")
                return

            # Check size limit and evict LRU if needed
            if len(self._cache) >= self.max_size:
                # Evict least recently used (first item)
                evicted_ticker = next(iter(self._cache))
                del self._cache[evicted_ticker]
                logger.debug(f"Cache full, evicted LRU entry: {evicted_ticker}")

            # Add new entry
            self._cache[ticker] = (info, datetime.now())
            logger.debug(f"Cached info for {ticker} (size: {len(self._cache)}/{self.max_size})")

    def clear(self):
        """Clear all cached data. Thread-safe."""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0
            logger.debug("Cache cleared")

    def stats(self) -> Dict:
        """
        Get cache statistics. Thread-safe.

        Returns:
            Dict with hits, misses, size, hit_rate, max_size
        """
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total * 100) if total > 0 else 0

            return {
                'size': len(self._cache),
                'max_size': self.max_size,
                'hits': self._hits,
                'misses': self._misses,
                'hit_rate': round(hit_rate, 1)
            }

    def __len__(self) -> int:
        """Get number of cached entries. Thread-safe."""
        with self._lock:
            return len(self._cache)

    def __repr__(self) -> str:
        """String representation. Thread-safe."""
        stats = self.stats()
        return f"YFinanceCache(size={stats['size']}/{stats['max_size']}, hit_rate={stats['hit_rate']}%)"


# Global cache instance (singleton pattern)
_global_cache: Optional[YFinanceCache] = None


def get_cache(ttl_minutes: int = 15) -> YFinanceCache:
    """
    Get or create global cache instance.

    Args:
        ttl_minutes: Time-to-live in minutes (default: 15)

    Returns:
        Singleton YFinanceCache instance
    """
    global _global_cache
    if _global_cache is None:
        _global_cache = YFinanceCache(ttl_minutes=ttl_minutes)
        logger.debug(f"Created global YFinanceCache with {ttl_minutes}min TTL")
    return _global_cache


def clear_cache():
    """Clear global cache."""
    global _global_cache
    if _global_cache:
        _global_cache.clear()
