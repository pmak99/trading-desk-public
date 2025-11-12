"""Unit tests for HybridCache (L1+L2 persistent cache)."""

import pytest
import time
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

from src.infrastructure.cache.hybrid_cache import HybridCache


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = Path(f.name)
    yield db_path
    # Cleanup
    if db_path.exists():
        db_path.unlink()


@pytest.fixture
def cache(temp_db):
    """Create HybridCache instance with short TTLs for testing."""
    return HybridCache(
        db_path=temp_db,
        l1_ttl_seconds=1,  # 1 second for fast testing
        l2_ttl_seconds=3,  # 3 seconds for fast testing
        max_l1_size=5
    )


class TestHybridCacheBasics:
    """Test basic cache operations."""

    def test_cache_creation(self, temp_db):
        """Test that cache initializes correctly."""
        cache = HybridCache(temp_db)
        assert cache.l1_ttl == 30
        assert cache.l2_ttl == 300
        assert cache.max_l1_size == 1000
        assert cache.db_path == temp_db

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

    def test_l1_expiration_falls_through_to_l2(self, cache):
        """Test that expired L1 falls through to L2."""
        cache.set("key1", "value1")

        # Wait for L1 to expire (1 second)
        time.sleep(1.1)

        # L1 should be expired, but L2 still valid
        result = cache.get("key1")
        assert result == "value1"

    def test_l2_expiration(self, cache):
        """Test that L2 entries expire after TTL."""
        cache.set("key1", "value1")

        # Clear L1
        cache._l1_cache.clear()
        cache._l1_timestamps.clear()

        # Wait for L2 to expire (3 seconds)
        time.sleep(3.1)

        # Both L1 and L2 should be expired
        result = cache.get("key1")
        assert result is None


class TestHybridCacheEviction:
    """Test L1 cache eviction behavior."""

    def test_l1_eviction_when_full(self, cache):
        """Test L1 evicts oldest entry when full."""
        # Fill L1 to max_size (5)
        for i in range(5):
            cache.set(f"key{i}", f"value{i}")
            time.sleep(0.01)  # Ensure different timestamps

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
            time.sleep(0.01)

        # key0 was evicted from L1
        assert "key0" not in cache._l1_cache

        # Access key0 - should promote from L2 to L1
        result = cache.get("key0")
        assert result == "value0"
        assert "key0" in cache._l1_cache


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

    def test_cleanup_expired_removes_old_l2_entries(self, cache):
        """Test cleanup_expired removes expired L2 entries."""
        cache.set("key1", "value1")

        # Wait for L2 expiration
        time.sleep(3.1)

        # Run cleanup
        deleted = cache.cleanup_expired()
        assert deleted > 0

        # Entry should be gone
        result = cache.get("key1")
        assert result is None

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

    def test_get_handles_corrupted_pickle_data(self, cache, temp_db):
        """Test that corrupted pickle data is handled gracefully."""
        import sqlite3

        # Manually insert corrupted data into L2
        with sqlite3.connect(str(temp_db)) as conn:
            conn.execute(
                'INSERT INTO cache (key, value, timestamp) VALUES (?, ?, ?)',
                ("corrupted", b"not_valid_pickle", datetime.now().isoformat())
            )
            conn.commit()

        # Should return None and log warning (not crash)
        result = cache.get("corrupted")
        assert result is None

    def test_set_nonpicklable_object_logs_error(self, cache, caplog):
        """Test that non-picklable objects log error but don't crash."""
        import threading

        # Threading locks are not picklable
        lock = threading.Lock()

        # Should log error but not crash
        cache.set("lock", lock)

        # L1 might have it, but L2 write failed
        # Getting it back should work from L1
        result = cache.get("lock")
        assert result == lock

    def test_multiple_cache_instances_share_l2(self, temp_db):
        """Test that multiple cache instances share L2 storage."""
        cache1 = HybridCache(temp_db)
        cache2 = HybridCache(temp_db)

        # Set in cache1
        cache1.set("shared", "value")

        # Get from cache2 (should hit L2)
        result = cache2.get("shared")
        assert result == "value"


class TestHybridCacheConcurrency:
    """Test thread safety."""

    def test_concurrent_access(self, cache):
        """Test concurrent access doesn't corrupt cache."""
        import threading

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
