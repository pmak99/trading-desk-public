"""
Hybrid cache with L1 (memory) + L2 (SQLite persistence).

Phase 2: Persistent cache that survives restarts.

Security: Uses JSON serialization instead of pickle to avoid
arbitrary code execution vulnerabilities.
"""

import sqlite3
import logging
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Any, Dict
from threading import Lock
from collections import OrderedDict

from src.utils.serialization import serialize, deserialize

logger = logging.getLogger(__name__)

# Cache version for schema migrations
CACHE_VERSION = "v1"

# Connection timeout for cache database operations (30 seconds)
CONNECTION_TIMEOUT = 30


class HybridCache:
    """
    Two-tier cache: L1 (memory, fast) + L2 (SQLite, persistent).

    Phase: Phase 2 - Data Persistence

    Lookup flow:
    1. Check L1 (in-memory dict)
    2. Check L2 (SQLite)
    3. If L2 hit → promote to L1
    4. If both miss → return None

    TTL:
    - L1: 30 seconds (fast, volatile)
    - L2: 5 minutes (persistent, survives restart)
    """

    def __init__(
        self,
        db_path: Path,
        l1_ttl_seconds: int = 30,
        l2_ttl_seconds: int = 300,
        max_l1_size: int = 1000
    ):
        """
        Initialize hybrid cache.

        Args:
            db_path: Path to SQLite database for L2 cache
            l1_ttl_seconds: L1 cache TTL (default: 30s)
            l2_ttl_seconds: L2 cache TTL (default: 5min)
            max_l1_size: Maximum L1 cache entries
        """
        self.db_path = db_path
        self.l1_ttl = l1_ttl_seconds
        self.l2_ttl = l2_ttl_seconds
        self.max_l1_size = max_l1_size

        # L1 cache (in-memory) - using OrderedDict for O(1) eviction
        self._l1_cache: OrderedDict[str, Any] = OrderedDict()
        self._l1_timestamps: Dict[str, datetime] = {}
        self._lock = Lock()  # Thread safety for L1 mutations

        # Initialize L2 (SQLite)
        self._init_db()

        logger.info(
            f"HybridCache initialized: L1={l1_ttl_seconds}s, L2={l2_ttl_seconds}s, "
            f"db={db_path}"
        )

    def _init_db(self) -> None:
        """Initialize SQLite schema for L2 cache with migration support."""
        try:
            with sqlite3.connect(str(self.db_path), timeout=CONNECTION_TIMEOUT) as conn:
                # Enable WAL mode for better write concurrency
                conn.execute('PRAGMA journal_mode=WAL')

                # Create table with expiration column for per-key TTL
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS cache (
                        key TEXT PRIMARY KEY,
                        value BLOB NOT NULL,
                        timestamp TEXT NOT NULL,
                        version TEXT DEFAULT 'v1',
                        expiration TEXT
                    )
                ''')
                conn.execute(
                    'CREATE INDEX IF NOT EXISTS idx_cache_timestamp ON cache(timestamp)'
                )
                conn.execute(
                    'CREATE INDEX IF NOT EXISTS idx_cache_expiration ON cache(expiration)'
                )

                # Migration: Add expiration column if it doesn't exist (for existing caches)
                cursor = conn.execute("PRAGMA table_info(cache)")
                columns = [row[1] for row in cursor.fetchall()]
                if 'expiration' not in columns:
                    logger.info("Migrating cache schema: adding expiration column")
                    conn.execute('ALTER TABLE cache ADD COLUMN expiration TEXT')

                conn.commit()
                logger.debug(f"L2 cache schema initialized with WAL mode: {self.db_path}")
        except sqlite3.Error as e:
            logger.error(f"Failed to initialize L2 cache schema: {e}")
            raise

    def get(self, key: str) -> Optional[Any]:
        """
        Get cached value with L1 → L2 lookup.

        Args:
            key: Cache key

        Returns:
            Cached value or None if miss/expired
        """
        now = datetime.now()

        # L1 check (in-memory)
        with self._lock:
            if key in self._l1_cache:
                stored_time = self._l1_timestamps.get(key)
                if stored_time and (now - stored_time).total_seconds() < self.l1_ttl:
                    logger.debug(f"Cache L1 HIT: {key}")
                    return self._l1_cache[key]
                else:
                    # Expired
                    del self._l1_cache[key]
                    if key in self._l1_timestamps:
                        del self._l1_timestamps[key]

        # L2 check (SQLite)
        try:
            with sqlite3.connect(str(self.db_path), timeout=CONNECTION_TIMEOUT) as conn:
                row = conn.execute(
                    'SELECT value, timestamp, expiration FROM cache WHERE key = ?',
                    (key,)
                ).fetchone()

            if row:
                value_blob, timestamp_str, expiration_str = row
                stored_time = datetime.fromisoformat(timestamp_str)

                # Use per-key TTL if available, otherwise use instance TTL
                if expiration_str:
                    expiration_time = datetime.fromisoformat(expiration_str)
                    is_expired = now >= expiration_time
                else:
                    # Fallback: use elapsed time
                    elapsed = (now - stored_time).total_seconds()
                    is_expired = elapsed >= self.l2_ttl

                elapsed = (now - stored_time).total_seconds()
                if not is_expired:
                    # L2 hit → promote to L1
                    try:
                        # Deserialize from JSON
                        json_str = value_blob.decode('utf-8')
                        value = deserialize(json_str)
                        logger.debug(f"Cache L2 HIT: {key} (age: {elapsed:.1f}s)")

                        # Promote to L1
                        with self._lock:
                            if len(self._l1_cache) >= self.max_l1_size:
                                self._evict_oldest_l1()
                            self._l1_cache[key] = value
                            self._l1_timestamps[key] = now

                        return value
                    except (json.JSONDecodeError, ValueError, KeyError) as e:
                        logger.warning(f"Failed to deserialize cached value for {key}: {e}")
                        # Delete corrupted entry
                        with sqlite3.connect(str(self.db_path), timeout=CONNECTION_TIMEOUT) as conn:
                            conn.execute('DELETE FROM cache WHERE key = ?', (key,))
                else:
                    # Expired in L2
                    logger.debug(f"Cache L2 EXPIRED: {key} (age: {elapsed:.1f}s)")
                    with sqlite3.connect(str(self.db_path), timeout=CONNECTION_TIMEOUT) as conn:
                        conn.execute('DELETE FROM cache WHERE key = ?', (key,))

        except sqlite3.Error as e:
            logger.warning(f"L2 cache read error for {key}: {e}")

        logger.debug(f"Cache MISS: {key}")
        return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        Set cached value in both L1 and L2.

        Args:
            key: Cache key
            value: Value to cache (must be JSON-serializable)
            ttl: Optional TTL override in seconds. If None, uses configured L2 TTL.

        Note:
            Per-key TTL is stored but L1 still uses instance l1_ttl for consistency.
            L2 respects per-key TTL on retrieval.
        """
        now = datetime.now()
        effective_l2_ttl = ttl if ttl is not None else self.l2_ttl

        # Set in L1 (always uses instance l1_ttl for eviction)
        with self._lock:
            if len(self._l1_cache) >= self.max_l1_size and key not in self._l1_cache:
                self._evict_oldest_l1()

            # Move to end for LRU ordering
            if key in self._l1_cache:
                self._l1_cache.move_to_end(key)
            else:
                self._l1_cache[key] = value

            self._l1_cache[key] = value
            self._l1_timestamps[key] = now

        # Set in L2 (SQLite) with JSON serialization
        try:
            # Serialize to JSON
            json_str = serialize(value)
            value_blob = json_str.encode('utf-8')

            # Calculate expiration timestamp for per-key TTL
            expiration = (now + timedelta(seconds=effective_l2_ttl)).isoformat()

            with sqlite3.connect(str(self.db_path), timeout=CONNECTION_TIMEOUT) as conn:
                conn.execute(
                    '''
                    INSERT OR REPLACE INTO cache (key, value, timestamp, version, expiration)
                    VALUES (?, ?, ?, ?, ?)
                    ''',
                    (key, value_blob, now.isoformat(), CACHE_VERSION, expiration)
                )
                conn.commit()
            logger.debug(f"Cache SET: {key} (L1+L2, TTL={effective_l2_ttl}s)")
        except (ValueError, TypeError, sqlite3.Error) as e:
            logger.error(f"Failed to write to L2 cache for {key}: {e}")
            # L1 still has the value, so partial success
            logger.debug(f"Cache SET: {key} (L1 only, L2 failed)")

    def delete(self, key: str) -> None:
        """Delete cached value from both L1 and L2."""
        # Delete from L1
        with self._lock:
            if key in self._l1_cache:
                del self._l1_cache[key]
            if key in self._l1_timestamps:
                del self._l1_timestamps[key]

        # Delete from L2
        try:
            with sqlite3.connect(str(self.db_path), timeout=CONNECTION_TIMEOUT) as conn:
                conn.execute('DELETE FROM cache WHERE key = ?', (key,))
                conn.commit()
            logger.debug(f"Cache DELETE: {key} (L1+L2)")
        except sqlite3.Error as e:
            logger.warning(f"Failed to delete from L2 cache for {key}: {e}")

    def clear(self) -> None:
        """Clear all cached values from both L1 and L2."""
        # Clear L1
        with self._lock:
            count_l1 = len(self._l1_cache)
            self._l1_cache.clear()
            self._l1_timestamps.clear()

        # Clear L2
        try:
            with sqlite3.connect(str(self.db_path), timeout=CONNECTION_TIMEOUT) as conn:
                cursor = conn.execute('SELECT COUNT(*) FROM cache')
                count_l2 = cursor.fetchone()[0]
                conn.execute('DELETE FROM cache')
                conn.commit()
            logger.info(f"Cache CLEAR: {count_l1} L1 entries, {count_l2} L2 entries")
        except sqlite3.Error as e:
            logger.error(f"Failed to clear L2 cache: {e}")

    def cleanup_expired(self) -> int:
        """
        Remove expired entries from L2 cache.

        Returns:
            Number of entries deleted
        """
        try:
            cutoff = datetime.now() - timedelta(seconds=self.l2_ttl)
            with sqlite3.connect(str(self.db_path), timeout=CONNECTION_TIMEOUT) as conn:
                cursor = conn.execute(
                    'DELETE FROM cache WHERE timestamp < ?',
                    (cutoff.isoformat(),)
                )
                deleted = cursor.rowcount
                conn.commit()

            if deleted > 0:
                logger.info(f"Cleaned up {deleted} expired L2 cache entries")
            return deleted
        except sqlite3.Error as e:
            logger.error(f"Failed to cleanup L2 cache: {e}")
            return 0

    def _evict_oldest_l1(self) -> None:
        """Evict oldest entry from L1 cache (called with lock held).

        Uses OrderedDict for O(1) eviction of least recently used item.
        """
        if not self._l1_cache:
            return

        # OrderedDict: first item is oldest (FIFO)
        oldest_key, _ = self._l1_cache.popitem(last=False)
        if oldest_key in self._l1_timestamps:
            del self._l1_timestamps[oldest_key]
        logger.debug(f"L1 evicted: {oldest_key}")

    def stats(self) -> Dict[str, int]:
        """
        Get cache statistics.

        Returns:
            Dictionary with L1 and L2 counts
        """
        with self._lock:
            l1_count = len(self._l1_cache)

        try:
            with sqlite3.connect(str(self.db_path), timeout=CONNECTION_TIMEOUT) as conn:
                cursor = conn.execute('SELECT COUNT(*) FROM cache')
                l2_count = cursor.fetchone()[0]
        except sqlite3.Error:
            l2_count = -1

        return {
            'l1_count': l1_count,
            'l2_count': l2_count,
            'l1_max': self.max_l1_size,
            'l1_ttl': self.l1_ttl,
            'l2_ttl': self.l2_ttl
        }
