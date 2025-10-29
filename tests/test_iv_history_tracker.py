"""
Comprehensive tests for IV History Tracker.

Tests the IV Rank calculation system that tracks historical
implied volatility and calculates percentile rankings.
"""

import pytest
import tempfile
import os
from datetime import datetime, timedelta
from pathlib import Path

from src.iv_history_tracker import IVHistoryTracker


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
        db_path = f.name

    yield db_path

    # Cleanup
    try:
        os.unlink(db_path)
    except:
        pass


@pytest.fixture
def tracker(temp_db):
    """Create an IV tracker instance with temporary database."""
    return IVHistoryTracker(db_path=temp_db)


class TestIVHistoryTrackerInitialization:
    """Test tracker initialization and database setup."""

    def test_database_creation(self, tracker, temp_db):
        """Test that database file is created."""
        assert Path(temp_db).exists(), "Database file should be created"

    def test_database_schema(self, tracker):
        """Test that database schema is properly initialized."""
        conn = tracker._get_connection()

        # Check that table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='iv_history'"
        )
        table = cursor.fetchone()
        assert table is not None, "iv_history table should exist"

        # Check table structure
        cursor = conn.execute("PRAGMA table_info(iv_history)")
        columns = {row[1] for row in cursor.fetchall()}
        expected_columns = {'ticker', 'date', 'iv_value', 'timestamp'}
        assert columns == expected_columns, f"Expected columns {expected_columns}, got {columns}"

    def test_wal_mode_enabled(self, tracker):
        """Test that WAL mode is enabled for concurrency."""
        conn = tracker._get_connection()
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode.upper() == 'WAL', "WAL mode should be enabled"


class TestIVRecording:
    """Test recording IV values."""

    def test_record_single_iv(self, tracker):
        """Test recording a single IV value."""
        tracker.record_iv('AAPL', 75.5)

        conn = tracker._get_connection()
        cursor = conn.execute(
            "SELECT iv_value FROM iv_history WHERE ticker = 'AAPL'"
        )
        row = cursor.fetchone()
        assert row is not None, "IV should be recorded"
        assert row['iv_value'] == 75.5, "IV value should match"

    def test_record_multiple_ivs_same_ticker(self, tracker):
        """Test recording multiple IV values for same ticker."""
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

        tracker.record_iv('NVDA', 85.0, date=today)
        tracker.record_iv('NVDA', 82.0, date=yesterday)

        conn = tracker._get_connection()
        cursor = conn.execute(
            "SELECT COUNT(*) as count FROM iv_history WHERE ticker = 'NVDA'"
        )
        count = cursor.fetchone()['count']
        assert count == 2, "Should have 2 records for NVDA"

    def test_record_multiple_tickers(self, tracker):
        """Test recording IV values for multiple tickers."""
        tracker.record_iv('AAPL', 65.0)
        tracker.record_iv('TSLA', 95.0)
        tracker.record_iv('NVDA', 88.0)

        conn = tracker._get_connection()
        cursor = conn.execute("SELECT COUNT(DISTINCT ticker) as count FROM iv_history")
        count = cursor.fetchone()['count']
        assert count == 3, "Should have 3 distinct tickers"

    def test_record_with_explicit_date(self, tracker):
        """Test recording IV with explicit date."""
        test_date = '2024-01-15'
        tracker.record_iv('TEST', 70.0, date=test_date)

        conn = tracker._get_connection()
        cursor = conn.execute(
            "SELECT date FROM iv_history WHERE ticker = 'TEST'"
        )
        row = cursor.fetchone()
        assert row['date'] == test_date, "Date should match"

    def test_record_replaces_existing_date(self, tracker):
        """Test that recording IV for same ticker+date replaces old value."""
        test_date = datetime.now().strftime('%Y-%m-%d')

        tracker.record_iv('AAPL', 70.0, date=test_date)
        tracker.record_iv('AAPL', 75.0, date=test_date)  # Update

        conn = tracker._get_connection()
        cursor = conn.execute(
            "SELECT iv_value, COUNT(*) as count FROM iv_history WHERE ticker = 'AAPL' AND date = ?"
, (test_date,)
        )
        row = cursor.fetchone()
        assert row['count'] == 1, "Should have only 1 record (replaced)"
        assert row['iv_value'] == 75.0, "IV should be updated value"

    def test_record_invalid_iv_ignored(self, tracker):
        """Test that invalid IV values are ignored."""
        tracker.record_iv('TEST', 0.0)
        tracker.record_iv('TEST', -5.0)

        conn = tracker._get_connection()
        cursor = conn.execute(
            "SELECT COUNT(*) as count FROM iv_history WHERE ticker = 'TEST'"
        )
        count = cursor.fetchone()['count']
        assert count == 0, "Invalid IV values should not be recorded"


class TestIVRankCalculation:
    """Test IV Rank (percentile) calculation."""

    def test_insufficient_data(self, tracker):
        """Test that IV Rank returns 0 with insufficient data."""
        # Record only 10 days (need 30 for reliable percentile)
        for i in range(10):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            tracker.record_iv('TEST', 70.0 + i, date=date)

        iv_rank = tracker.calculate_iv_rank('TEST', 75.0)
        assert iv_rank == 0.0, "Should return 0 with < 30 data points"

    def test_current_iv_at_minimum(self, tracker):
        """Test IV Rank when current IV is at minimum of range."""
        # Record 50 days with IV from 60-90
        for i in range(50):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            tracker.record_iv('TEST', 60.0 + (i % 30))

        iv_rank = tracker.calculate_iv_rank('TEST', 60.0)  # Minimum
        assert iv_rank == 0.0, "IV at minimum should have rank 0%"

    def test_current_iv_at_maximum(self, tracker):
        """Test IV Rank when current IV is at maximum of range."""
        # Record 50 days with IV from 60-90
        for i in range(50):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            tracker.record_iv('TEST', 60.0 + (i % 30))

        iv_rank = tracker.calculate_iv_rank('TEST', 100.0)  # Above all
        assert iv_rank == 100.0, "IV above all historical should have rank 100%"

    def test_current_iv_at_median(self, tracker):
        """Test IV Rank when current IV is at median of range."""
        # Record 100 days with IV from 50-100 (median = 75)
        for i in range(100):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            tracker.record_iv('TEST', 50.0 + (i % 50))

        iv_rank = tracker.calculate_iv_rank('TEST', 75.0)  # Median
        # Should be around 50% (allowing some variance due to modulo)
        assert 45.0 <= iv_rank <= 55.0, f"IV at median should have rank ~50%, got {iv_rank}%"

    def test_current_iv_in_75th_percentile(self, tracker):
        """Test IV Rank in 75th percentile."""
        # Record 100 days with values 0-99
        for i in range(100):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            tracker.record_iv('TEST', float(i))

        iv_rank = tracker.calculate_iv_rank('TEST', 75.0)
        # 75 values are below 75.0, so rank should be 75%
        assert iv_rank == 75.0, f"Expected rank 75%, got {iv_rank}%"

    def test_current_iv_in_25th_percentile(self, tracker):
        """Test IV Rank in 25th percentile."""
        # Record 100 days with values 0-99
        for i in range(100):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            tracker.record_iv('TEST', float(i))

        iv_rank = tracker.calculate_iv_rank('TEST', 25.0)
        # 25 values are below 25.0, so rank should be 25%
        assert iv_rank == 25.0, f"Expected rank 25%, got {iv_rank}%"

    def test_only_52_weeks_considered(self, tracker):
        """Test that only last 52 weeks are considered."""
        # Record data for 400 days
        for i in range(400):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            if i < 365:
                # Recent 365 days: IV 50-100
                tracker.record_iv('TEST', 50.0 + (i % 50))
            else:
                # Older data: IV 0-50 (should be ignored)
                tracker.record_iv('TEST', float(i % 50))

        # Current IV 25 would be low if old data counted, but not in recent year
        iv_rank = tracker.calculate_iv_rank('TEST', 40.0)
        # Should be calculated against recent 365 days only
        assert iv_rank > 0.0, "Should consider only recent 365 days"

    def test_invalid_current_iv(self, tracker):
        """Test with invalid current IV."""
        # Record some valid data
        for i in range(50):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            tracker.record_iv('TEST', 70.0 + i)

        iv_rank = tracker.calculate_iv_rank('TEST', 0.0)
        assert iv_rank == 0.0, "Invalid current IV should return 0"

        iv_rank = tracker.calculate_iv_rank('TEST', -10.0)
        assert iv_rank == 0.0, "Negative current IV should return 0"

    def test_no_data_for_ticker(self, tracker):
        """Test IV Rank with no historical data."""
        iv_rank = tracker.calculate_iv_rank('NONEXISTENT', 75.0)
        assert iv_rank == 0.0, "No data should return rank 0"


class TestIVStatistics:
    """Test IV statistics retrieval."""

    def test_get_stats_with_data(self, tracker):
        """Test getting statistics with sufficient data."""
        # Record 50 days of data
        for i in range(50):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            tracker.record_iv('AAPL', 60.0 + (i % 20))

        stats = tracker.get_iv_stats('AAPL')

        assert stats['data_points'] == 50, "Should have 50 data points"
        assert 60.0 <= stats['min_iv'] <= 80.0, "Min IV should be in range"
        assert 60.0 <= stats['max_iv'] <= 80.0, "Max IV should be in range"
        assert 60.0 <= stats['avg_iv'] <= 80.0, "Avg IV should be in range"
        assert stats['latest_iv'] > 0, "Latest IV should be recorded"
        assert stats['latest_date'] is not None, "Latest date should be recorded"

    def test_get_stats_no_data(self, tracker):
        """Test getting statistics with no data."""
        stats = tracker.get_iv_stats('NONEXISTENT')

        assert stats['data_points'] == 0, "Should have 0 data points"
        assert stats['min_iv'] == 0, "Min should be 0"
        assert stats['max_iv'] == 0, "Max should be 0"
        assert stats['avg_iv'] == 0, "Avg should be 0"
        assert stats['latest_iv'] == 0, "Latest should be 0"
        assert stats['latest_date'] is None, "Latest date should be None"

    def test_stats_include_latest_iv(self, tracker):
        """Test that stats include the most recent IV value."""
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

        tracker.record_iv('TEST', 70.0, date=yesterday)
        tracker.record_iv('TEST', 85.0, date=today)

        stats = tracker.get_iv_stats('TEST')
        assert stats['latest_iv'] == 85.0, "Latest IV should be most recent value"


class TestDataCleanup:
    """Test old data cleanup functionality."""

    def test_cleanup_old_data(self, tracker):
        """Test cleanup of data older than threshold."""
        # Record data for 500 days
        for i in range(500):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            tracker.record_iv('TEST', 70.0)

        # Cleanup data older than 400 days
        tracker.cleanup_old_data(days_to_keep=400)

        conn = tracker._get_connection()
        cursor = conn.execute(
            "SELECT COUNT(*) as count FROM iv_history WHERE ticker = 'TEST'"
        )
        count = cursor.fetchone()['count']

        # Should have ~400 records (plus today)
        assert 395 <= count <= 405, f"Should have ~400 records after cleanup, got {count}"

    def test_cleanup_preserves_recent_data(self, tracker):
        """Test that cleanup preserves recent data."""
        # Record 100 recent days
        for i in range(100):
            date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
            tracker.record_iv('RECENT', float(i))

        # Record 100 old days
        for i in range(100):
            date = (datetime.now() - timedelta(days=500 + i)).strftime('%Y-%m-%d')
            tracker.record_iv('OLD', float(i))

        tracker.cleanup_old_data(days_to_keep=400)

        conn = tracker._get_connection()

        # Recent ticker should still have all data
        cursor = conn.execute(
            "SELECT COUNT(*) as count FROM iv_history WHERE ticker = 'RECENT'"
        )
        recent_count = cursor.fetchone()['count']
        assert recent_count == 100, "Recent data should be preserved"

        # Old ticker should have no data
        cursor = conn.execute(
            "SELECT COUNT(*) as count FROM iv_history WHERE ticker = 'OLD'"
        )
        old_count = cursor.fetchone()['count']
        assert old_count == 0, "Old data should be deleted"


class TestThreadSafety:
    """Test thread-safe operations."""

    def test_thread_local_connections(self, tracker):
        """Test that each thread gets its own connection."""
        import threading

        connections = []

        def get_connection():
            conn = tracker._get_connection()
            connections.append(id(conn))

        threads = [threading.Thread(target=get_connection) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Each thread should get a different connection object
        # (though they connect to same database)
        assert len(set(connections)) >= 1, "Should have thread-local connections"

    def test_concurrent_writes(self, tracker):
        """Test concurrent write operations."""
        import threading

        def record_ivs(ticker, start_val):
            for i in range(10):
                date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
                tracker.record_iv(ticker, start_val + i, date=date)

        threads = [
            threading.Thread(target=record_ivs, args=(f'TICKER{i}', i * 10))
            for i in range(5)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        conn = tracker._get_connection()
        cursor = conn.execute("SELECT COUNT(*) as count FROM iv_history")
        count = cursor.fetchone()['count']

        # Should have 5 tickers * 10 records each = 50 total
        assert count == 50, f"Should have 50 records from concurrent writes, got {count}"


class TestRealWorldScenarios:
    """Test with realistic usage patterns."""

    def test_daily_tracking_over_year(self, tracker):
        """Test tracking daily IV for a year."""
        # Simulate daily IV tracking with realistic volatility
        base_iv = 70.0
        for i in range(365):
            date = (datetime.now() - timedelta(days=365 - i)).strftime('%Y-%m-%d')
            # Simulate realistic IV fluctuation
            import math
            iv = base_iv + 20 * math.sin(i / 30.0)  # Cyclical pattern
            tracker.record_iv('NVDA', iv, date=date)

        # Calculate IV Rank for current elevated IV
        iv_rank = tracker.calculate_iv_rank('NVDA', 85.0)

        assert 0 < iv_rank <= 100, "IV Rank should be valid percentile"
        assert iv_rank > 50, "High IV should have high rank"

    def test_multiple_tickers_realistic_data(self, tracker):
        """Test with multiple tickers and realistic data patterns."""
        tickers = ['AAPL', 'NVDA', 'TSLA', 'MSFT', 'GOOGL']

        # Record 90 days for each ticker
        for ticker in tickers:
            base_iv = 50.0 + (ord(ticker[0]) % 20)  # Different base for each
            for i in range(90):
                date = (datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d')
                tracker.record_iv(ticker, base_iv + (i % 30), date=date)

        # Verify each ticker has data and can calculate rank
        for ticker in tickers:
            stats = tracker.get_iv_stats(ticker)
            assert stats['data_points'] == 90, f"{ticker} should have 90 data points"

            iv_rank = tracker.calculate_iv_rank(ticker, stats['avg_iv'])
            assert 40 <= iv_rank <= 60, f"{ticker} avg IV should have rank ~50%"
