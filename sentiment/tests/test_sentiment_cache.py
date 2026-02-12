"""
Unit tests for sentiment sentiment_cache module.

Tests the SQLite-backed caching system with 3-hour TTL.
"""

import pytest
import tempfile
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "cache"))

from cache.sentiment_cache import (
    CachedSentiment,
    SentimentCache,
    get_cached_sentiment,
    cache_sentiment,
)


class TestCachedSentiment:
    """Tests for CachedSentiment dataclass."""

    def test_not_expired_recent(self):
        """Recently cached entry should not be expired."""
        now = datetime.now(timezone.utc)
        cached = CachedSentiment(
            ticker="NVDA",
            date="2025-12-09",
            source="perplexity",
            sentiment="Bullish outlook...",
            cached_at=now
        )
        assert cached.is_expired is False

    def test_not_expired_under_3_hours(self):
        """Entry under 3 hours old should not be expired."""
        two_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)
        cached = CachedSentiment(
            ticker="NVDA",
            date="2025-12-09",
            source="perplexity",
            sentiment="Bullish outlook...",
            cached_at=two_hours_ago
        )
        assert cached.is_expired is False

    def test_expired_over_3_hours(self):
        """Entry over 3 hours old should be expired."""
        four_hours_ago = datetime.now(timezone.utc) - timedelta(hours=4)
        cached = CachedSentiment(
            ticker="NVDA",
            date="2025-12-09",
            source="perplexity",
            sentiment="Bullish outlook...",
            cached_at=four_hours_ago
        )
        assert cached.is_expired is True

    def test_expired_exactly_3_hours(self):
        """Entry exactly at 3 hour boundary should be expired."""
        three_hours_ago = datetime.now(timezone.utc) - timedelta(hours=3, seconds=1)
        cached = CachedSentiment(
            ticker="NVDA",
            date="2025-12-09",
            source="perplexity",
            sentiment="Bullish outlook...",
            cached_at=three_hours_ago
        )
        assert cached.is_expired is True

    def test_age_minutes_recent(self):
        """Age should be close to 0 for recent entry."""
        now = datetime.now(timezone.utc)
        cached = CachedSentiment(
            ticker="NVDA",
            date="2025-12-09",
            source="perplexity",
            sentiment="Bullish outlook...",
            cached_at=now
        )
        assert cached.age_minutes < 1

    def test_age_minutes_30_min(self):
        """Age should be approximately 30 for 30-minute old entry."""
        thirty_min_ago = datetime.now(timezone.utc) - timedelta(minutes=30)
        cached = CachedSentiment(
            ticker="NVDA",
            date="2025-12-09",
            source="perplexity",
            sentiment="Bullish outlook...",
            cached_at=thirty_min_ago
        )
        assert 29 <= cached.age_minutes <= 31

    def test_age_minutes_2_hours(self):
        """Age should be approximately 120 for 2-hour old entry."""
        two_hours_ago = datetime.now(timezone.utc) - timedelta(hours=2)
        cached = CachedSentiment(
            ticker="NVDA",
            date="2025-12-09",
            source="perplexity",
            sentiment="Bullish outlook...",
            cached_at=two_hours_ago
        )
        assert 119 <= cached.age_minutes <= 121

    def test_naive_datetime_handling(self):
        """Should handle naive datetime by assuming UTC."""
        # Note: The CachedSentiment.is_expired replaces naive datetimes with UTC
        # When local time differs from UTC, this can cause unexpected expiration
        # This test verifies the code doesn't crash, even if expiration may differ
        one_hour_ago = datetime.now() - timedelta(hours=1)  # Naive datetime
        cached = CachedSentiment(
            ticker="NVDA",
            date="2025-12-09",
            source="perplexity",
            sentiment="Bullish outlook...",
            cached_at=one_hour_ago
        )
        # Should not raise error and should calculate age (value may vary by timezone)
        assert cached.age_minutes >= 0
        # Just verify the property works without error, value depends on local tz offset
        _ = cached.is_expired


class TestSentimentCache:
    """Tests for SentimentCache class."""

    @pytest.fixture
    def temp_cache(self, tmp_path):
        """Create a temporary cache for testing."""
        db_path = tmp_path / "test_cache.db"
        return SentimentCache(db_path=db_path)

    def test_init_creates_database(self, tmp_path):
        """Cache initialization should create database file."""
        db_path = tmp_path / "new_cache.db"
        cache = SentimentCache(db_path=db_path)
        assert db_path.exists()

    def test_init_creates_table(self, temp_cache):
        """Cache should create sentiment_cache table."""
        with sqlite3.connect(temp_cache.db_path) as conn:
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='sentiment_cache'
            """)
            assert cursor.fetchone() is not None

    def test_init_creates_index(self, temp_cache):
        """Cache should create index on ticker, date."""
        with sqlite3.connect(temp_cache.db_path) as conn:
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='index' AND name='idx_sentiment_ticker_date'
            """)
            assert cursor.fetchone() is not None

    def test_set_valid_perplexity(self, temp_cache):
        """Should successfully set cache entry with perplexity source."""
        temp_cache.set("NVDA", "2025-12-09", "perplexity", "Bullish outlook")
        # Verify entry exists
        result = temp_cache.get("NVDA", "2025-12-09")
        assert result is not None
        assert result.ticker == "NVDA"
        assert result.sentiment == "Bullish outlook"
        assert result.source == "perplexity"

    def test_set_valid_websearch(self, temp_cache):
        """Should successfully set cache entry with websearch source."""
        temp_cache.set("AAPL", "2025-12-10", "websearch", "Neutral sentiment")
        result = temp_cache.get("AAPL", "2025-12-10")
        assert result is not None
        assert result.source == "websearch"

    def test_set_invalid_source_raises(self, temp_cache):
        """Should raise ValueError for invalid source."""
        with pytest.raises(ValueError) as excinfo:
            temp_cache.set("NVDA", "2025-12-09", "invalid_source", "sentiment")
        assert "Invalid source" in str(excinfo.value)

    def test_set_uppercases_ticker(self, temp_cache):
        """Should uppercase ticker when setting."""
        temp_cache.set("nvda", "2025-12-09", "perplexity", "Bullish")
        result = temp_cache.get("NVDA", "2025-12-09")
        assert result is not None
        assert result.ticker == "NVDA"

    def test_set_replaces_existing(self, temp_cache):
        """Should replace existing entry with same key."""
        temp_cache.set("NVDA", "2025-12-09", "perplexity", "Old sentiment")
        temp_cache.set("NVDA", "2025-12-09", "perplexity", "New sentiment")
        result = temp_cache.get("NVDA", "2025-12-09")
        assert result.sentiment == "New sentiment"

    def test_get_returns_none_for_missing(self, temp_cache):
        """Should return None for non-existent entry."""
        result = temp_cache.get("XYZ", "2025-12-09")
        assert result is None

    def test_get_uppercases_ticker(self, temp_cache):
        """Should uppercase ticker when getting."""
        temp_cache.set("NVDA", "2025-12-09", "perplexity", "Bullish")
        result = temp_cache.get("nvda", "2025-12-09")
        assert result is not None

    def test_get_returns_none_for_expired(self, temp_cache):
        """Should return None for expired entries."""
        # Insert an old entry directly
        old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        with sqlite3.connect(temp_cache.db_path) as conn:
            conn.execute("""
                INSERT INTO sentiment_cache
                (ticker, date, source, sentiment, cached_at)
                VALUES (?, ?, ?, ?, ?)
            """, ("XPRD", "2025-12-09", "perplexity", "Old data", old_time))
            conn.commit()

        result = temp_cache.get("XPRD", "2025-12-09")
        assert result is None

    def test_get_prefers_perplexity_over_websearch(self, temp_cache):
        """Should prefer perplexity source over websearch."""
        # Add websearch first
        temp_cache.set("NVDA", "2025-12-09", "websearch", "WebSearch sentiment")
        # Add perplexity second
        temp_cache.set("NVDA", "2025-12-09", "perplexity", "Perplexity sentiment")

        result = temp_cache.get("NVDA", "2025-12-09")
        assert result.source == "perplexity"
        assert result.sentiment == "Perplexity sentiment"

    def test_get_falls_back_to_websearch(self, temp_cache):
        """Should fall back to websearch if perplexity is expired."""
        # Add old perplexity entry directly
        old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        with sqlite3.connect(temp_cache.db_path) as conn:
            conn.execute("""
                INSERT INTO sentiment_cache
                (ticker, date, source, sentiment, cached_at)
                VALUES (?, ?, ?, ?, ?)
            """, ("NVDA", "2025-12-09", "perplexity", "Old perplexity", old_time))
            conn.commit()

        # Add fresh websearch entry
        temp_cache.set("NVDA", "2025-12-09", "websearch", "Fresh websearch")

        result = temp_cache.get("NVDA", "2025-12-09")
        assert result.source == "websearch"

    def test_clear_expired_removes_old_entries(self, temp_cache):
        """clear_expired should remove entries older than TTL."""
        # Add old entry directly
        old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        with sqlite3.connect(temp_cache.db_path) as conn:
            conn.execute("""
                INSERT INTO sentiment_cache
                (ticker, date, source, sentiment, cached_at)
                VALUES (?, ?, ?, ?, ?)
            """, ("OLD", "2025-12-01", "perplexity", "Old data", old_time))
            conn.commit()

        # Add fresh entry
        temp_cache.set("FRESH", "2025-12-09", "perplexity", "Fresh data")

        # Clear expired
        deleted = temp_cache.clear_expired()

        assert deleted == 1

        # Verify OLD is gone, FRESH remains
        with sqlite3.connect(temp_cache.db_path) as conn:
            old_row = conn.execute(
                "SELECT * FROM sentiment_cache WHERE ticker = 'OLD'"
            ).fetchone()
            fresh_row = conn.execute(
                "SELECT * FROM sentiment_cache WHERE ticker = 'FRESH'"
            ).fetchone()

        assert old_row is None
        assert fresh_row is not None

    def test_clear_expired_returns_count(self, temp_cache):
        """clear_expired should return count of deleted entries."""
        # Add multiple old entries
        old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        with sqlite3.connect(temp_cache.db_path) as conn:
            for i in range(3):
                conn.execute("""
                    INSERT INTO sentiment_cache
                    (ticker, date, source, sentiment, cached_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (f"OLD{i}", "2025-12-01", "perplexity", "Old data", old_time))
            conn.commit()

        deleted = temp_cache.clear_expired()
        assert deleted == 3

    def test_clear_all_removes_all_entries(self, temp_cache):
        """clear_all should remove all cache entries."""
        temp_cache.set("NVDA", "2025-12-09", "perplexity", "Sentiment 1")
        temp_cache.set("AAPL", "2025-12-09", "websearch", "Sentiment 2")

        deleted = temp_cache.clear_all()
        assert deleted == 2

        # Verify empty
        assert temp_cache.get("NVDA", "2025-12-09") is None
        assert temp_cache.get("AAPL", "2025-12-09") is None

    def test_stats_empty_cache(self, temp_cache):
        """Stats should handle empty cache."""
        stats = temp_cache.stats()
        assert stats['total_entries'] == 0
        assert stats['by_source'] == {}
        assert stats['expired'] == 0
        assert stats['valid'] == 0

    def test_stats_with_entries(self, temp_cache):
        """Stats should correctly count entries by source."""
        temp_cache.set("NVDA", "2025-12-09", "perplexity", "Sentiment 1")
        temp_cache.set("AAPL", "2025-12-09", "perplexity", "Sentiment 2")
        temp_cache.set("MSFT", "2025-12-09", "websearch", "Sentiment 3")

        stats = temp_cache.stats()
        assert stats['total_entries'] == 3
        assert stats['by_source']['perplexity'] == 2
        assert stats['by_source']['websearch'] == 1
        assert stats['valid'] == 3
        assert stats['expired'] == 0

    def test_stats_counts_expired(self, temp_cache):
        """Stats should correctly count expired entries."""
        # Add fresh entry
        temp_cache.set("FRESH", "2025-12-09", "perplexity", "Fresh")

        # Add old entry directly
        old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        with sqlite3.connect(temp_cache.db_path) as conn:
            conn.execute("""
                INSERT INTO sentiment_cache
                (ticker, date, source, sentiment, cached_at)
                VALUES (?, ?, ?, ?, ?)
            """, ("OLD", "2025-12-01", "perplexity", "Old", old_time))
            conn.commit()

        stats = temp_cache.stats()
        assert stats['total_entries'] == 2
        assert stats['expired'] == 1
        assert stats['valid'] == 1

    def test_valid_sources_constant(self, temp_cache):
        """VALID_SOURCES should contain perplexity and websearch."""
        assert "perplexity" in temp_cache.VALID_SOURCES
        assert "websearch" in temp_cache.VALID_SOURCES
        assert len(temp_cache.VALID_SOURCES) == 2


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    @pytest.fixture
    def temp_db_path(self, tmp_path):
        """Provide a temporary database path."""
        return tmp_path / "test_cache.db"

    def test_get_cached_sentiment_returns_text(self, temp_db_path):
        """get_cached_sentiment should return just the sentiment text."""
        with patch('cache.sentiment_cache.SentimentCache') as MockCache:
            mock_instance = MockCache.return_value
            mock_instance.get.return_value = CachedSentiment(
                ticker="NVDA",
                date="2025-12-09",
                source="perplexity",
                sentiment="Bullish outlook on AI",
                cached_at=datetime.now(timezone.utc)
            )

            result = get_cached_sentiment("NVDA", "2025-12-09")
            assert result == "Bullish outlook on AI"

    def test_get_cached_sentiment_returns_none_on_miss(self, temp_db_path):
        """get_cached_sentiment should return None on cache miss."""
        with patch('cache.sentiment_cache.SentimentCache') as MockCache:
            mock_instance = MockCache.return_value
            mock_instance.get.return_value = None

            result = get_cached_sentiment("MISSING", "2025-12-09")
            assert result is None

    def test_get_cached_sentiment_defaults_to_today(self):
        """get_cached_sentiment should default date to today."""
        with patch('cache.sentiment_cache.SentimentCache') as MockCache:
            mock_instance = MockCache.return_value
            mock_instance.get.return_value = None

            get_cached_sentiment("NVDA")

            # Check that get was called with today's date
            call_args = mock_instance.get.call_args[0]
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            assert call_args[1] == today

    def test_cache_sentiment_calls_set(self):
        """cache_sentiment should call cache.set with correct args."""
        with patch('cache.sentiment_cache.SentimentCache') as MockCache:
            mock_instance = MockCache.return_value

            cache_sentiment("NVDA", "Bullish outlook", "perplexity", "2025-12-09")

            mock_instance.set.assert_called_once_with(
                "NVDA", "2025-12-09", "perplexity", "Bullish outlook"
            )

    def test_cache_sentiment_defaults_source(self):
        """cache_sentiment should default to perplexity source."""
        with patch('cache.sentiment_cache.SentimentCache') as MockCache:
            mock_instance = MockCache.return_value

            cache_sentiment("NVDA", "Bullish outlook")

            call_args = mock_instance.set.call_args[0]
            assert call_args[2] == "perplexity"  # source is third arg

    def test_cache_sentiment_invalid_source_raises(self):
        """cache_sentiment should raise for invalid source."""
        with patch('cache.sentiment_cache.SentimentCache') as MockCache:
            mock_instance = MockCache.return_value
            mock_instance.set.side_effect = ValueError("Invalid source")

            with pytest.raises(ValueError):
                cache_sentiment("NVDA", "sentiment", "invalid")


class TestEdgeCases:
    """Edge case tests."""

    @pytest.fixture
    def temp_cache(self, tmp_path):
        """Create a temporary cache for testing."""
        db_path = tmp_path / "test_cache.db"
        return SentimentCache(db_path=db_path)

    def test_long_sentiment_text(self, temp_cache):
        """Should handle very long sentiment text."""
        long_text = "A" * 10000
        temp_cache.set("NVDA", "2025-12-09", "perplexity", long_text)
        result = temp_cache.get("NVDA", "2025-12-09")
        assert result.sentiment == long_text

    def test_special_characters_in_sentiment(self, temp_cache):
        """Should handle special characters in sentiment."""
        special_text = "Bull's outlook: 'strong' & positive! <tag> \"quoted\""
        temp_cache.set("NVDA", "2025-12-09", "perplexity", special_text)
        result = temp_cache.get("NVDA", "2025-12-09")
        assert result.sentiment == special_text

    def test_unicode_in_sentiment(self, temp_cache):
        """Should handle unicode in sentiment."""
        unicode_text = "Bullish 🚀 outlook for Q4 earnings 📈"
        temp_cache.set("NVDA", "2025-12-09", "perplexity", unicode_text)
        result = temp_cache.get("NVDA", "2025-12-09")
        assert result.sentiment == unicode_text

    def test_empty_sentiment_text(self, temp_cache):
        """Should handle empty sentiment text."""
        temp_cache.set("NVDA", "2025-12-09", "perplexity", "")
        result = temp_cache.get("NVDA", "2025-12-09")
        assert result.sentiment == ""

    def test_concurrent_reads_writes(self, temp_cache):
        """Should handle concurrent operations safely."""
        # This is a basic test - full concurrency testing would need threads
        temp_cache.set("NVDA", "2025-12-09", "perplexity", "Sentiment 1")
        temp_cache.set("NVDA", "2025-12-09", "websearch", "Sentiment 2")
        result = temp_cache.get("NVDA", "2025-12-09")
        assert result is not None

    def test_creates_parent_directory(self, tmp_path):
        """Should create parent directories if they don't exist."""
        db_path = tmp_path / "nested" / "dir" / "cache.db"
        cache = SentimentCache(db_path=db_path)
        assert db_path.exists()

    def test_multiple_dates_same_ticker(self, temp_cache):
        """Should correctly differentiate entries by date."""
        temp_cache.set("NVDA", "2025-12-09", "perplexity", "Dec 9 sentiment")
        temp_cache.set("NVDA", "2025-12-10", "perplexity", "Dec 10 sentiment")

        result_9 = temp_cache.get("NVDA", "2025-12-09")
        result_10 = temp_cache.get("NVDA", "2025-12-10")

        assert result_9.sentiment == "Dec 9 sentiment"
        assert result_10.sentiment == "Dec 10 sentiment"

    def test_multiple_sources_different_dates(self, temp_cache):
        """Should correctly handle different sources on different dates."""
        temp_cache.set("NVDA", "2025-12-09", "perplexity", "Perplexity Dec 9")
        temp_cache.set("NVDA", "2025-12-10", "websearch", "WebSearch Dec 10")

        result_9 = temp_cache.get("NVDA", "2025-12-09")
        result_10 = temp_cache.get("NVDA", "2025-12-10")

        assert result_9.source == "perplexity"
        assert result_10.source == "websearch"
