"""Unit tests for HybridCache (L1 memory + L2 Postgres `whisper_cache`).

L2 moved from a per-machine SQLite file to the shared Postgres table at the
tail of the 2026-09 migration, so these run against a REAL Postgres in a
throwaway schema rather than a temp file.

⚠️ Every test injects the `pg_pool` fixture's pool rather than letting
HybridCache fall back to the process-wide `get_pool()`. That is not ceremony:
the production singleton points at the real database, so a cache test that
forgot it would write rows into production and still pass.

Nothing here sleeps: L1 runs on a fake clock, and an L2 row is expired by moving
its `expires_at` into the past (L2 compares against the database's `now()`).
"""

import math
import threading

import pytest

from common.pgtesting import baseline_ddl
from src.infrastructure.cache.hybrid_cache import CACHE_VERSION, HybridCache

pytestmark = pytest.mark.pg


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def whisper_table(pg_pool):
    """Create `whisper_cache` in the throwaway schema, from the real baseline."""
    with pg_pool.connection() as conn:
        conn.execute(baseline_ddl("whisper_cache"))
    return pg_pool


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def cache(whisper_table, clock):
    """HybridCache with short TTLs, bound to the throwaway schema."""
    return HybridCache(
        l1_ttl_seconds=1,
        l2_ttl_seconds=3,
        max_l1_size=5,
        pool=whisper_table,
        clock=clock,
    )


def _expire_l2(pool, key):
    """Move one L2 row's expiry into the past, as time passing would."""
    with pool.connection() as conn:
        conn.execute(
            "UPDATE whisper_cache SET expires_at = now() - interval '1 second' "
            "WHERE cache_key = %s",
            (f"{CACHE_VERSION}:{key}",),
        )


class TestHybridCacheBasics:
    """Test basic cache operations."""

    def test_cache_creation(self):
        """Defaults are as documented, and construction opens NO connection.

        The pool is resolved lazily on first L2 access. Constructing a cache
        must not open a production pool as a side effect -- which is also what
        makes this assertion safe to run with no `pool=` argument.
        """
        cache = HybridCache()
        assert cache.l1_ttl == 30
        assert cache.l2_ttl == 300
        assert cache.max_l1_size == 1000
        assert cache._pool is None

    def test_set_and_get_l1_hit(self, cache):
        """Test basic set/get with L1 cache hit."""
        cache.set("key1", "value1")
        result = cache.get("key1")
        assert result == "value1"

    def test_set_and_get_complex_object(self, cache):
        """Test caching complex Python objects."""
        data = {
            'string': 'test',
            'number': 42,
            'list': [1, 2, 3],
            'nested': {'a': 1, 'b': 2}
        }
        cache.set("complex", data)
        result = cache.get("complex")
        assert result == data

    def test_cache_miss(self, cache):
        """Test cache miss returns None."""
        result = cache.get("nonexistent")
        assert result is None


class TestHybridCacheTiers:
    """Test L1 and L2 cache tiers."""

    def test_l2_hit_promotes_to_l1(self, cache):
        """Test that L2 hit promotes value to L1."""
        # Set value
        cache.set("key1", "value1")

        # Clear L1 but keep L2
        cache._l1_cache.clear()
        cache._l1_timestamps.clear()

        # Get should hit L2 and promote to L1
        result = cache.get("key1")
        assert result == "value1"

        # Verify it's now in L1
        assert "key1" in cache._l1_cache
        assert cache._l1_cache["key1"] == "value1"

    def test_l1_expiration_falls_through_to_l2(self, cache, clock, whisper_table):
        """An expired L1 entry is served from L2, and promoted back to L1."""
        cache.set("key1", "value1")
        clock.advance(1.1)                   # past the 1 s L1 TTL

        with whisper_table.connection() as conn:
            conn.execute("UPDATE whisper_cache SET payload = %s::jsonb",
                         ('"from-l2"',))
        assert cache.get("key1") == "from-l2", "an expired L1 entry must not be served"
        assert cache._l1_cache["key1"] == "from-l2"

    def test_l1_entry_is_served_within_its_ttl(self, cache, clock, whisper_table):
        cache.set("key1", "value1")
        clock.advance(0.9)

        with whisper_table.connection() as conn:
            conn.execute("DELETE FROM whisper_cache")
        assert cache.get("key1") == "value1"

    def test_l2_expiration(self, cache, whisper_table):
        """An expired L2 row is a miss, and is deleted when read."""
        cache.set("key1", "value1")
        cache._l1_cache.clear()
        cache._l1_timestamps.clear()
        _expire_l2(whisper_table, "key1")

        assert cache.get("key1") is None
        with whisper_table.connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM whisper_cache").fetchone()[0] == 0

    def test_l2_rows_carry_the_cache_version(self, cache, whisper_table):
        """The table is shared by hosts running different revisions. The version
        in the key is what keeps a payload format change from being read back by
        code that cannot decode it: bump CACHE_VERSION and old rows are misses."""
        cache.set("whisper_tickers:2026-09-14", ["NVDA"])
        with whisper_table.connection() as conn:
            keys = [r[0] for r in conn.execute("SELECT cache_key FROM whisper_cache")]
        assert keys == [f"{CACHE_VERSION}:whisper_tickers:2026-09-14"]

        cache.delete("whisper_tickers:2026-09-14")
        with whisper_table.connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM whisper_cache").fetchone()[0] == 0

    def test_a_row_under_another_version_is_not_served(self, cache, whisper_table):
        """Rows written before keys were versioned (production held
        `whisper_tickers:2026-09-14` on 2026-09-14) or under another version miss."""
        with whisper_table.connection() as conn:
            for key in ("whisper_tickers:2026-09-14", "v0:whisper_tickers:2026-09-14"):
                conn.execute(
                    "INSERT INTO whisper_cache (cache_key, payload, expires_at) "
                    "VALUES (%s, '[\"OLD\"]'::jsonb, now() + interval '1 hour')",
                    (key,),
                )
        assert cache.get("whisper_tickers:2026-09-14") is None


class TestHybridCacheEviction:
    """Test L1 cache eviction behavior."""

    def test_l1_eviction_when_full(self, cache):
        """Test L1 evicts oldest entry when full."""
        # Fill L1 to max_size (5)
        for i in range(5):
            cache.set(f"key{i}", f"value{i}")

        # Add one more - should evict oldest (key0)
        cache.set("key5", "value5")

        # key0 should be evicted from L1 (but still in L2)
        assert "key0" not in cache._l1_cache
        assert "key5" in cache._l1_cache

        # key0 should still be in L2
        result = cache.get("key0")
        assert result == "value0"

    def test_eviction_promotes_from_l2(self, cache):
        """Test that evicted L1 entries can be promoted back from L2."""
        # Fill L1
        for i in range(6):
            cache.set(f"key{i}", f"value{i}")

        # key0 was evicted from L1
        assert "key0" not in cache._l1_cache

        # Access key0 - should promote from L2 to L1
        result = cache.get("key0")
        assert result == "value0"
        assert "key0" in cache._l1_cache

    def test_l1_evicts_the_least_recently_used_entry(self, cache):
        for i in range(5):
            cache.set(f"key{i}", f"value{i}")
        cache.get("key0")                    # key1 is now least recently used

        cache.set("key5", "value5")
        assert "key0" in cache._l1_cache
        assert "key1" not in cache._l1_cache


class TestHybridCacheOperations:
    """Test cache operations (delete, clear, cleanup)."""

    def test_delete_removes_from_both_tiers(self, cache):
        """Test delete removes from L1 and L2."""
        cache.set("key1", "value1")

        # Delete
        cache.delete("key1")

        # Should be gone from both
        assert "key1" not in cache._l1_cache
        result = cache.get("key1")
        assert result is None

    def test_clear_removes_all_entries(self, cache):
        """Test clear removes all cached entries."""
        # Add multiple entries
        for i in range(3):
            cache.set(f"key{i}", f"value{i}")

        # Clear
        cache.clear()

        # All should be gone
        assert len(cache._l1_cache) == 0
        for i in range(3):
            assert cache.get(f"key{i}") is None

    def test_cleanup_expired_removes_old_l2_entries(self, cache, clock, whisper_table):
        """cleanup_expired deletes expired rows and leaves fresh ones."""
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        _expire_l2(whisper_table, "key1")

        assert cache.cleanup_expired() == 1

        clock.advance(1.1)                   # L1 expired too: only L2 can answer
        assert cache.get("key1") is None
        assert cache.get("key2") == "value2"

    def test_stats_returns_counts(self, cache):
        """Test stats returns L1 and L2 counts."""
        # Add some entries
        cache.set("key1", "value1")
        cache.set("key2", "value2")

        stats = cache.stats()
        assert stats['l1_count'] == 2
        assert stats['l2_count'] == 2
        assert stats['l1_max'] == 5
        assert stats['l1_ttl'] == 1
        assert stats['l2_ttl'] == 3


class TestHybridCacheErrorHandling:
    """Test error handling and edge cases."""

    def test_get_handles_undecodable_payload(self, cache, whisper_table):
        """A KNOWN domain type carrying a field that cannot be rebuilt.

        jsonb rejects malformed documents at write time, and an UNKNOWN
        `__type__` is deliberately passed through as a plain dict (see the
        security test below). So the reachable decode failure is a recognised
        type whose payload has gone bad -- what a schema change or a bug
        actually produces.

        It must return None rather than raise, and must DELETE the row: one
        that fails to decode will fail identically on every future lookup.
        """
        with whisper_table.connection() as conn:
            conn.execute(
                "INSERT INTO whisper_cache (cache_key, payload, expires_at) "
                "VALUES (%s, %s::jsonb, now() + interval '1 hour')",
                (f"{CACHE_VERSION}:corrupted",
                 '{"__type__": "datetime", "value": "not-a-timestamp"}'),
            )

        assert cache.get("corrupted") is None

        with whisper_table.connection() as conn:
            left = conn.execute(
                "SELECT COUNT(*) FROM whisper_cache WHERE cache_key = %s",
                (f"{CACHE_VERSION}:corrupted",),
            ).fetchone()[0]
        assert left == 0, "an undecodable row must be deleted, not left to fail forever"

    def test_unknown_type_tag_is_not_reconstructed(self, cache, whisper_table):
        """An unrecognised `__type__` comes back as a plain dict, never an object.

        This is a SECURITY property, not an accident: it is why JSON replaced
        pickle here. Only whitelisted domain types are reconstructed, so a
        cache row cannot be made to instantiate arbitrary classes. Moving L2
        into a SHARED database raises the stakes -- the row is now reachable
        from more than one host.
        """
        with whisper_table.connection() as conn:
            conn.execute(
                "INSERT INTO whisper_cache (cache_key, payload, expires_at) "
                "VALUES (%s, %s::jsonb, now() + interval '1 hour')",
                (f"{CACHE_VERSION}:evil", '{"__type__": "NoSuchDomainType", "x": 1}'),
            )

        got = cache.get("evil")
        assert isinstance(got, dict), f"must stay a dict, got {type(got)}"
        assert got == {"__type__": "NoSuchDomainType", "x": 1}

    def test_set_nonserialisable_object_logs_error(self, cache, caplog):
        """A value JSON cannot represent must not crash the caller."""
        # Threading locks are not JSON-serialisable
        lock = threading.Lock()

        # Should log error but not crash
        cache.set("lock", lock)

        # L1 might have it, but L2 write failed
        # Getting it back should work from L1
        result = cache.get("lock")
        assert result == lock

    def test_multiple_cache_instances_share_l2(self, whisper_table):
        """Two instances share L2 — the whole point of moving it to Postgres.

        Under SQLite this proved two objects could share a FILE. It now proves
        they share a TABLE, which is what lets the Mac and the server stop each
        paying their own scrape of the same endpoint.
        """
        cache1 = HybridCache(pool=whisper_table)
        cache2 = HybridCache(pool=whisper_table)

        cache1.set("shared", "value")

        # cache2 has a cold L1, so this can only come from L2.
        assert cache2.get("shared") == "value"
        assert "shared" in cache2._l1_cache, "an L2 hit must promote to L1"


class TestHybridCacheConcurrency:
    """Test thread safety."""

    def test_concurrent_access(self, cache):
        """Test concurrent access doesn't corrupt cache."""
        def worker(thread_id):
            for i in range(10):
                cache.set(f"thread{thread_id}_key{i}", f"value{i}")
                cache.get(f"thread{thread_id}_key{i}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Cache should be consistent (no crashes)
        stats = cache.stats()
        assert stats['l1_count'] >= 0
        assert stats['l2_count'] >= 0


class TestNonFiniteFloats:
    """A NaN or Infinity in the payload must not produce a database error.

    Regression from the SQLite -> Postgres move, found in review. `serialize()`
    used json.dumps' default allow_nan=True, which emits the NON-STANDARD
    tokens NaN / Infinity. SQLite stored that text happily; Postgres `jsonb` is
    a strict parser and rejected it, so `set()` failed at write time with
    `invalid input syntax for type json` and the value silently became
    L1-only — logged as an ERROR naming a JSON syntax problem rather than the
    actual cause, a float that is not a number.

    The value is genuinely not cacheable in jsonb. What this pins is that the
    caller is unaffected and the diagnosis is honest.
    """

    def test_nan_does_not_raise_and_stays_in_l1(self, cache):
        payload = {"ticker": "TEST", "vrp": float("nan")}
        cache.set("_nan", payload)                       # must not raise

        got = cache.get("_nan")
        assert got is not None, "L1 must still serve a value L2 cannot hold"
        assert got["ticker"] == "TEST"
        assert math.isnan(got["vrp"]), "L1 returns the object as given, NaN and all"

    def test_nan_is_not_written_to_l2(self, cache, whisper_table):
        cache.set("_nan2", {"v": float("nan")})
        with whisper_table.connection() as conn:
            n = conn.execute("SELECT COUNT(*) FROM whisper_cache").fetchone()[0]
        assert n == 0, "a non-JSON-representable payload must not reach jsonb"

    def test_infinity_is_handled_the_same_way(self, cache):
        cache.set("_inf", {"ratio": float("inf")})       # must not raise

    def test_serialize_rejects_non_finite_rather_than_emitting_bad_json(self):
        """The root fix: serialize() returns JSON, or raises. Never `NaN`."""
        from src.utils.serialization import serialize

        with pytest.raises(ValueError):
            serialize({"x": float("nan")})
        # ...and ordinary floats are untouched.
        assert '"x":1.5' in serialize({"x": 1.5})
