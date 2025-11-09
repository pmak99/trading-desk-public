"""
LRU (Least Recently Used) Cache implementation.

Provides bounded memory usage by evicting least recently used items
when the cache size exceeds the maximum limit.
"""

from collections import OrderedDict
from typing import Any, Optional, Tuple
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class LRUCache:
    """
    LRU cache with optional TTL (time-to-live) support.

    Features:
    - Bounded size (prevents memory leaks)
    - O(1) get/set operations
    - Optional TTL for cache entries
    - Automatic eviction of oldest entries when full

    Usage:
        cache = LRUCache(max_size=1000, ttl_minutes=15)
        cache.set('key', 'value')
        value = cache.get('key')  # Returns 'value' or None if expired/missing
    """

    def __init__(self, max_size: int = 1000, ttl_minutes: Optional[int] = None):
        """
        Initialize LRU cache.

        Args:
            max_size: Maximum number of entries (default: 1000)
            ttl_minutes: Time-to-live in minutes (None = no expiration)
        """
        self.max_size = max_size
        self.ttl = timedelta(minutes=ttl_minutes) if ttl_minutes else None
        self.cache = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: Any) -> Optional[Any]:
        """
        Get value from cache.

        Args:
            key: Cache key

        Returns:
            Cached value or None if not found/expired
        """
        if key not in self.cache:
            self._misses += 1
            return None

        # Check TTL if enabled
        value, timestamp = self.cache[key]
        if self.ttl and (datetime.now() - timestamp) > self.ttl:
            # Entry expired - remove it
            del self.cache[key]
            self._misses += 1
            return None

        # Move to end (mark as recently used)
        self.cache.move_to_end(key)
        self._hits += 1
        return value

    def set(self, key: Any, value: Any) -> None:
        """
        Set value in cache.

        Args:
            key: Cache key
            value: Value to cache
        """
        timestamp = datetime.now()

        # Update existing entry
        if key in self.cache:
            self.cache.move_to_end(key)
            self.cache[key] = (value, timestamp)
            return

        # Add new entry
        self.cache[key] = (value, timestamp)

        # Evict oldest entry if over limit
        if len(self.cache) > self.max_size:
            evicted_key = next(iter(self.cache))
            del self.cache[evicted_key]
            self._evictions += 1
            logger.debug(f"LRU cache evicted key: {evicted_key} (size: {len(self.cache)}/{self.max_size})")

    def clear(self) -> None:
        """Clear all cache entries."""
        self.cache.clear()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def size(self) -> int:
        """Get current cache size."""
        return len(self.cache)

    def stats(self) -> dict:
        """
        Get cache statistics.

        Returns:
            Dict with hits, misses, evictions, size, hit_rate
        """
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0

        return {
            'size': len(self.cache),
            'max_size': self.max_size,
            'hits': self._hits,
            'misses': self._misses,
            'evictions': self._evictions,
            'hit_rate': round(hit_rate, 1)
        }

    def __contains__(self, key: Any) -> bool:
        """Check if key exists in cache (without updating LRU order)."""
        if key not in self.cache:
            return False

        # Check TTL if enabled
        if self.ttl:
            _, timestamp = self.cache[key]
            if (datetime.now() - timestamp) > self.ttl:
                return False

        return True

    def __len__(self) -> int:
        """Get cache size."""
        return len(self.cache)

    def __repr__(self) -> str:
        stats = self.stats()
        return f"LRUCache(size={stats['size']}/{stats['max_size']}, hit_rate={stats['hit_rate']}%)"
