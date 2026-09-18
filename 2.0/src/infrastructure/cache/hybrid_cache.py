"""
Hybrid cache with L1 (memory) + L2 (Postgres `whisper_cache`).

L2 moved from SQLite to Postgres at the tail of the 2026-09 migration. The
migration's end-state table called for `whisper_cache` in Postgres with the
SQLite path deleted; the table was created in Task 1 and then nothing ever
wrote to it, so it sat empty with no readers while `scan_cache.db` stayed the
live cache. An empty table waiting for data that never arrives is exactly the
"dead schema in a fresh database" the plan warned about.

**Why a shared cache is worth more than a machine-local one here.** The
principal consumer is `EarningsWhisperScraper`, which fronts an *undocumented*
endpoint that CLAUDE.md flags as rate-limit sensitive and that silently returns
an empty 200 when it is unhappy. With SQLite, the Mac and the server each kept
their own copy and each paid their own scrape. Sharing one cache halves the
traffic against the fragile thing.

Security: JSON serialization, never pickle — the payload is reconstructed only
into explicitly whitelisted domain types (`src/utils/serialization`).

⚠️ **A cache failure must never break a caller.** Every L2 operation is wrapped
so a database problem degrades this to an L1-only cache rather than taking down
a scan. That was true of the SQLite version and is more important now: L2 is a
network hop, so it has failure modes a local file did not.
"""

import json
import logging
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any, Callable, Dict, Optional

from src.infrastructure.database.pg_pool import get_pool
from src.utils.serialization import deserialize, serialize

logger = logging.getLogger(__name__)

# Prefixes every L2 key (`v1:whisper_tickers:2026-09-14`). The table is shared by
# hosts that can run different revisions, so a change to what a payload holds or
# how it is serialized must bump this: rows under the old version then read as
# misses instead of being decoded by code that does not match them. Old rows age
# out through `expires_at` (maintenance step 9b). Until 2026-09-14 this constant
# existed and no key carried it.
CACHE_VERSION = "v1"


def _l2_key(key: str) -> str:
    return f"{CACHE_VERSION}:{key}"


class HybridCache:
    """
    Two-tier cache: L1 (memory, fast) + L2 (Postgres, persistent and shared).

    Lookup flow:
    1. Check L1 (in-memory OrderedDict, true LRU)
    2. Check L2 (`whisper_cache`)
    3. If L2 hit -> promote to L1
    4. If both miss -> return None

    ⚠️ **L1 and L2 expire on different clocks, deliberately.** L1 uses elapsed
    time since this process cached it; L2 uses an absolute `expires_at` written
    by whichever host stored it. That is what makes the cache shareable — a row
    written by the server expires at the same instant for the Mac.
    """

    def __init__(
        self,
        l1_ttl_seconds: int = 30,
        l2_ttl_seconds: int = 300,
        max_l1_size: int = 1000,
        pool=None,
        clock: Callable[[], float] = time.monotonic,
    ):
        """
        Args:
            l1_ttl_seconds: L1 cache TTL (default: 30s)
            l2_ttl_seconds: default L2 TTL, overridable per `set()` call
            max_l1_size: Maximum L1 cache entries before LRU eviction
            pool: connection pool to use for L2. Defaults to the process-wide
                `get_pool()`, resolved LAZILY on first L2 access.
            clock: seconds on a monotonic clock, for L1 ages; tests pass a fake

        ⚠️ `pool` exists so tests are not forced through the production
        singleton. `common/pgtesting.py` builds its own pool bound to a
        throwaway schema precisely so "a test must not be able to write
        through the same pool production code holds" — a HybridCache that
        called `get_pool()` unconditionally would defeat that and write cache
        rows into the real database.

        The default is resolved lazily, not here: constructing a HybridCache
        must not open a connection pool as a side effect.
        """
        self.l1_ttl = l1_ttl_seconds
        self.l2_ttl = l2_ttl_seconds
        self.max_l1_size = max_l1_size
        self._pool = pool
        self._clock = clock

        self._l1_cache: "OrderedDict[str, Any]" = OrderedDict()
        self._l1_timestamps: Dict[str, float] = {}
        self._lock = Lock()

        logger.info(
            f"HybridCache initialized: L1={l1_ttl_seconds}s, L2={l2_ttl_seconds}s, "
            f"L2 backend=postgres:whisper_cache"
        )

    # -- L2 helpers ---------------------------------------------------------

    def _connection(self):
        """A pooled connection for L2, resolving the default pool on first use."""
        pool = self._pool if self._pool is not None else get_pool()
        return pool.connection()

    @staticmethod
    def _payload_to_value(payload: Any) -> Any:
        """Rebuild a domain object from what psycopg hands back for jsonb.

        psycopg decodes jsonb into Python objects, but `deserialize()` takes a
        JSON *string* — it reads the `__type__` tags that make domain
        reconstruction safe. Re-dumping is lossless for this purpose: the tags
        are ordinary keys, and jsonb stores numbers as exact numerics.
        """
        return deserialize(json.dumps(payload))

    # -- public API ---------------------------------------------------------

    def get(self, key: str) -> Optional[Any]:
        """Get a cached value, L1 then L2. Returns None on miss or expiry."""
        now = self._clock()

        with self._lock:
            if key in self._l1_cache:
                stored_time = self._l1_timestamps.get(key)
                if stored_time is not None and now - stored_time < self.l1_ttl:
                    self._l1_cache.move_to_end(key)
                    logger.debug(f"Cache L1 HIT: {key}")
                    return self._l1_cache[key]
                del self._l1_cache[key]
                self._l1_timestamps.pop(key, None)

        try:
            with self._connection() as conn:
                row = conn.execute(
                    "SELECT payload, expires_at > now() AS fresh "
                    "FROM whisper_cache WHERE cache_key = %s",
                    (_l2_key(key),),
                ).fetchone()

                if row is None:
                    logger.debug(f"Cache MISS: {key}")
                    return None

                if not row.fresh:
                    logger.debug(f"Cache L2 EXPIRED: {key}")
                    conn.execute(
                        "DELETE FROM whisper_cache WHERE cache_key = %s", (_l2_key(key),)
                    )
                    return None

                try:
                    value = self._payload_to_value(row.payload)
                except (json.JSONDecodeError, ValueError, KeyError, TypeError) as e:
                    # A row we cannot decode is worse than no row: it will fail
                    # identically on every future lookup. Drop it.
                    logger.warning(f"Undecodable cache payload for {key}: {e}")
                    conn.execute(
                        "DELETE FROM whisper_cache WHERE cache_key = %s", (_l2_key(key),)
                    )
                    return None

            logger.debug(f"Cache L2 HIT: {key}")
            with self._lock:
                if len(self._l1_cache) >= self.max_l1_size:
                    self._evict_oldest_l1()
                self._l1_cache[key] = value
                self._l1_timestamps[key] = now
            return value

        except Exception as e:  # noqa: BLE001 - degrade to L1-only, never raise
            logger.warning(f"L2 cache read error for {key}: {e}")
            return None

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Store in L1 and L2. `ttl` overrides the instance L2 TTL for this key."""
        now = self._clock()
        effective_l2_ttl = ttl if ttl is not None else self.l2_ttl

        with self._lock:
            if len(self._l1_cache) >= self.max_l1_size and key not in self._l1_cache:
                self._evict_oldest_l1()
            self._l1_cache[key] = value
            self._l1_cache.move_to_end(key)
            self._l1_timestamps[key] = now

        try:
            try:
                json_str = serialize(value)
            except ValueError as e:
                # Non-finite float (NaN / Infinity). Not cacheable: `jsonb` is a
                # strict JSON parser and rejects those tokens. Skip L2 rather
                # than make a round-trip that can only fail -- L1 still holds
                # the value, so the caller is unaffected.
                logger.warning(
                    f"Not caching {key} in L2: value is not JSON-representable "
                    f"({e}). Usually a NaN/Inf from a divide-by-zero upstream."
                )
                return

            # UTC, not naive local: expires_at is timestamptz and is compared
            # against now() by whichever host reads it next.
            expires_at = datetime.now(timezone.utc) + timedelta(seconds=effective_l2_ttl)

            with self._connection() as conn:
                conn.execute(
                    "INSERT INTO whisper_cache (cache_key, payload, expires_at) "
                    "VALUES (%s, %s::jsonb, %s) "
                    "ON CONFLICT (cache_key) DO UPDATE "
                    "SET payload = EXCLUDED.payload, expires_at = EXCLUDED.expires_at",
                    (_l2_key(key), json_str, expires_at),
                )
            logger.debug(f"Cache SET: {key} (L1+L2, TTL={effective_l2_ttl}s)")
        except Exception as e:  # noqa: BLE001 - L1 still holds it
            logger.error(f"Failed to write L2 cache for {key}: {e}")
            logger.debug(f"Cache SET: {key} (L1 only, L2 failed)")

    def delete(self, key: str) -> None:
        """Delete from both tiers."""
        with self._lock:
            self._l1_cache.pop(key, None)
            self._l1_timestamps.pop(key, None)

        try:
            with self._connection() as conn:
                conn.execute("DELETE FROM whisper_cache WHERE cache_key = %s", (_l2_key(key),))
            logger.debug(f"Cache DELETE: {key} (L1+L2)")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to delete L2 cache for {key}: {e}")

    def clear(self) -> None:
        """Clear both tiers.

        ⚠️ L2 is SHARED. Clearing it evicts every host's entries, not just this
        process's — which is the point of a shared cache, but worth knowing
        before calling it on a whim.
        """
        with self._lock:
            count_l1 = len(self._l1_cache)
            self._l1_cache.clear()
            self._l1_timestamps.clear()

        try:
            with self._connection() as conn:
                count_l2 = conn.execute(
                    "SELECT COUNT(*) FROM whisper_cache"
                ).fetchone()[0]
                conn.execute("DELETE FROM whisper_cache")
            logger.info(f"Cache CLEAR: {count_l1} L1 entries, {count_l2} L2 entries")
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to clear L2 cache: {e}")

    def cleanup_expired(self) -> int:
        """Delete expired L2 rows. Returns the number removed.

        Simpler than the SQLite version, which needed a fallback for rows whose
        `expiration` was NULL: `expires_at` is NOT NULL here, so there is one
        rule rather than two.
        """
        try:
            with self._connection() as conn:
                deleted = conn.execute(
                    "DELETE FROM whisper_cache WHERE expires_at <= now()"
                ).rowcount
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} expired L2 cache entries")
            return deleted
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to cleanup L2 cache: {e}")
            return 0

    def _evict_oldest_l1(self) -> None:
        """Evict the least recently used L1 entry. Caller holds the lock."""
        if not self._l1_cache:
            return
        oldest_key, _ = self._l1_cache.popitem(last=False)
        self._l1_timestamps.pop(oldest_key, None)
        logger.debug(f"L1 evicted: {oldest_key}")

    def stats(self) -> Dict[str, int]:
        """L1/L2 counts and configured TTLs. l2_count is -1 if L2 is unreachable."""
        with self._lock:
            l1_count = len(self._l1_cache)

        try:
            with self._connection() as conn:
                l2_count = conn.execute(
                    "SELECT COUNT(*) FROM whisper_cache"
                ).fetchone()[0]
        except Exception:  # noqa: BLE001
            l2_count = -1

        return {
            "l1_count": l1_count,
            "l2_count": l2_count,
            "l1_max": self.max_l1_size,
            "l1_ttl": self.l1_ttl,
            "l2_ttl": self.l2_ttl,
        }
