"""
Comprehensive tests for SQLite-based usage tracker.

Tests the new SQLite backend that replaced JSON file locking,
eliminating the multiprocessing bottleneck.
"""

import pytest
import tempfile
import os
import yaml
from datetime import datetime
from pathlib import Path

from src.usage_tracker_sqlite import UsageTrackerSQLite, BudgetExceededError


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
def temp_config():
    """Create a temporary config file."""
    config_data = {
        'monthly_budget': 10.0,
        'perplexity_monthly_limit': 5.0,
        'api_costs': {
            'perplexity': {
                'sonar-pro': {'per_1k_tokens': 0.003},
                'sonar': {'per_1k_tokens': 0.001}
            },
            'openai': {
                'gpt-4': {'per_1k_tokens': 0.03},
                'gpt-3.5-turbo': {'per_1k_tokens': 0.001}
            }
        }
    }

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(config_data, f)
        config_path = f.name

    yield config_path

    # Cleanup
    try:
        os.unlink(config_path)
    except:
        pass


@pytest.fixture
def tracker(temp_config, temp_db):
    """Create a tracker instance with temporary config and database."""
    return UsageTrackerSQLite(config_path=temp_config, db_path=temp_db)


class TestInitialization:
    """Test tracker initialization."""

    def test_database_creation(self, tracker, temp_db):
        """Test that database is created."""
        assert Path(temp_db).exists(), "Database file should be created"

    def test_database_schema(self, tracker):
        """Test database schema creation."""
        conn = tracker._get_connection()

        # Check api_calls table
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='api_calls'"
        )
        assert cursor.fetchone() is not None, "api_calls table should exist"

        # Check monthly_summary table
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='monthly_summary'"
        )
        assert cursor.fetchone() is not None, "monthly_summary table should exist"

    def test_wal_mode_enabled(self, tracker):
        """Test that WAL mode is enabled."""
        conn = tracker._get_connection()
        cursor = conn.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode.upper() == 'WAL', "WAL mode should be enabled for concurrency"

    def test_config_loaded(self, tracker):
        """Test that config is properly loaded."""
        assert tracker.config['monthly_budget'] == 10.0
        assert tracker.config['perplexity_monthly_limit'] == 5.0
        assert 'api_costs' in tracker.config


class TestBudgetChecking:
    """Test budget checking functionality."""

    def test_can_make_call_under_budget(self, tracker):
        """Test that calls are allowed under budget."""
        can_call, remaining = tracker.can_make_call('perplexity', 'sonar-pro')
        assert can_call is True, "Should allow calls under budget"
        assert remaining == 10.0, "Should have full budget remaining"

    def test_cannot_make_call_over_budget(self, tracker):
        """Test that calls are blocked when over budget."""
        # Use up the entire budget
        tracker.log_api_call(
            model='perplexity:sonar-pro',
            tokens=3_333_000,  # 3.333M tokens * $0.003 = $10
            cost=10.0
        )

        can_call, remaining = tracker.can_make_call('perplexity', 'sonar-pro')
        assert can_call is False, "Should block calls over budget"
        assert remaining <= 0, "Should have no budget remaining"

    def test_perplexity_specific_limit(self, tracker):
        """Test Perplexity-specific limit checking."""
        # Use up Perplexity budget but not total budget
        tracker.log_api_call(
            model='perplexity:sonar-pro',
            tokens=1_666_000,  # $5.0
            cost=5.0
        )

        # Perplexity should be blocked
        can_call, remaining = tracker.can_make_call('perplexity', 'sonar-pro')
        assert can_call is False, "Should block Perplexity over its limit"

        # But other APIs should still work
        can_call, remaining = tracker.can_make_call('openai', 'gpt-3.5-turbo')
        assert can_call is True, "Should allow other APIs under total budget"

    def test_bypass_mode(self, tracker):
        """Test bypass mode allows calls over budget."""
        # Use up budget
        tracker.log_api_call(
            model='perplexity:sonar-pro',
            tokens=3_333_000,
            cost=10.0
        )

        # Should be blocked normally
        can_call, _ = tracker.can_make_call('perplexity', 'sonar-pro')
        assert can_call is False

        # But allowed in bypass mode
        can_call, _ = tracker.can_make_call('perplexity', 'sonar-pro', bypass=True)
        assert can_call is True, "Bypass mode should allow calls"


class TestAPICallLogging:
    """Test API call logging."""

    def test_log_single_call(self, tracker):
        """Test logging a single API call."""
        tracker.log_api_call(
            model='perplexity:sonar-pro',
            tokens=1000,
            cost=0.003
        )

        summary = tracker.get_usage_summary()
        assert summary['total_calls'] == 1, "Should have 1 call logged"
        assert summary['total_cost'] == 0.003, "Cost should match"

    def test_log_multiple_calls(self, tracker):
        """Test logging multiple API calls."""
        tracker.log_api_call(model='perplexity:sonar-pro', tokens=1000, cost=0.003)
        tracker.log_api_call(model='perplexity:sonar', tokens=2000, cost=0.002)
        tracker.log_api_call(model='openai:gpt-3.5-turbo', tokens=500, cost=0.0005)

        summary = tracker.get_usage_summary()
        assert summary['total_calls'] == 3, "Should have 3 calls"
        assert summary['total_cost'] == 0.0055, "Total cost should be sum"

    def test_model_specific_tracking(self, tracker):
        """Test that each model is tracked separately."""
        tracker.log_api_call(model='perplexity:sonar-pro', tokens=1000, cost=0.003)
        tracker.log_api_call(model='perplexity:sonar-pro', tokens=1000, cost=0.003)
        tracker.log_api_call(model='openai:gpt-4', tokens=1000, cost=0.03)

        summary = tracker.get_usage_summary()
        model_usage = summary['model_usage']

        assert 'perplexity:sonar-pro' in model_usage
        assert model_usage['perplexity:sonar-pro']['calls'] == 2
        assert model_usage['perplexity:sonar-pro']['tokens'] == 2000

        assert 'openai:gpt-4' in model_usage
        assert model_usage['openai:gpt-4']['calls'] == 1

    def test_daily_usage_tracking(self, tracker):
        """Test daily usage is tracked separately."""
        tracker.log_api_call(model='perplexity:sonar', tokens=1000, cost=0.001)
        tracker.log_api_call(model='perplexity:sonar', tokens=2000, cost=0.002)

        summary = tracker.get_usage_summary()
        today = tracker._current_date()

        assert today in summary['daily_usage']
        assert summary['daily_usage'][today]['calls'] == 2
        assert summary['daily_usage'][today]['cost'] == 0.003

    def test_provider_specific_cost(self, tracker):
        """Test provider-specific cost tracking."""
        tracker.log_api_call(model='perplexity:sonar-pro', tokens=1000, cost=0.003)
        tracker.log_api_call(model='perplexity:sonar', tokens=1000, cost=0.001)
        tracker.log_api_call(model='openai:gpt-4', tokens=1000, cost=0.03)

        summary = tracker.get_usage_summary()

        assert summary['perplexity_cost'] == 0.004  # 0.003 + 0.001
        assert summary['total_cost'] == 0.034  # All providers


class TestEnforceAPICall:
    """Test enforce_api_call functionality."""

    def test_enforce_blocks_over_budget(self, tracker):
        """Test that enforce blocks calls when over budget."""
        # Use up budget
        tracker.log_api_call(
            model='perplexity:sonar-pro',
            tokens=3_333_000,
            cost=10.0
        )

        # Should raise exception
        with pytest.raises(BudgetExceededError) as exc_info:
            tracker.enforce_api_call('perplexity', 'sonar-pro')

        assert 'Budget exceeded' in str(exc_info.value)

    def test_enforce_allows_under_budget(self, tracker):
        """Test that enforce allows calls under budget."""
        try:
            tracker.enforce_api_call('perplexity', 'sonar-pro')
        except BudgetExceededError:
            pytest.fail("Should not raise exception under budget")

    def test_enforce_respects_bypass(self, tracker):
        """Test that enforce respects bypass mode."""
        # Use up budget
        tracker.log_api_call(
            model='perplexity:sonar-pro',
            tokens=3_333_000,
            cost=10.0
        )

        # Should not raise in bypass mode
        try:
            tracker.enforce_api_call('perplexity', 'sonar-pro', bypass=True)
        except BudgetExceededError:
            pytest.fail("Should not raise exception in bypass mode")


class TestUsageSummary:
    """Test usage summary retrieval."""

    def test_empty_summary(self, tracker):
        """Test summary with no usage."""
        summary = tracker.get_usage_summary()

        assert summary['total_calls'] == 0
        assert summary['total_cost'] == 0.0
        assert summary['perplexity_cost'] == 0.0
        assert summary['model_usage'] == {}
        assert summary['daily_usage'] == {}

    def test_summary_structure(self, tracker):
        """Test that summary has expected structure."""
        tracker.log_api_call(model='perplexity:sonar-pro', tokens=1000, cost=0.003)

        summary = tracker.get_usage_summary()

        required_keys = {
            'month', 'total_calls', 'total_cost', 'perplexity_cost',
            'model_usage', 'daily_usage'
        }
        assert set(summary.keys()) == required_keys, "Summary should have all required keys"

    def test_summary_accuracy(self, tracker):
        """Test that summary calculations are accurate."""
        # Log various calls
        tracker.log_api_call(model='perplexity:sonar-pro', tokens=1000, cost=0.003)
        tracker.log_api_call(model='perplexity:sonar', tokens=2000, cost=0.002)
        tracker.log_api_call(model='openai:gpt-3.5-turbo', tokens=1000, cost=0.001)

        summary = tracker.get_usage_summary()

        assert summary['total_calls'] == 3
        assert summary['total_cost'] == 0.006
        assert summary['perplexity_cost'] == 0.005
        assert len(summary['model_usage']) == 3


class TestMonthlyRollover:
    """Test monthly data rollover."""

    def test_different_months_tracked_separately(self, tracker):
        """Test that different months are tracked in separate records."""
        # Log call for current month
        tracker.log_api_call(model='perplexity:sonar', tokens=1000, cost=0.001)

        # Verify current month has data
        current_month = datetime.now().strftime('%Y-%m')
        conn = tracker._get_connection()
        cursor = conn.execute(
            "SELECT total_cost FROM monthly_summary WHERE month = ?",
            (current_month,)
        )
        row = cursor.fetchone()
        assert row is not None, "Current month should have data"
        assert row['total_cost'] == 0.001


class TestThreadSafety:
    """Test thread-safe operations."""

    def test_concurrent_logging(self, tracker):
        """Test concurrent API call logging."""
        import threading

        def log_calls():
            for _ in range(10):
                tracker.log_api_call(
                    model='perplexity:sonar',
                    tokens=100,
                    cost=0.0001
                )

        threads = [threading.Thread(target=log_calls) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        summary = tracker.get_usage_summary()
        # 5 threads * 10 calls = 50 total
        assert summary['total_calls'] == 50, f"Should have 50 calls, got {summary['total_calls']}"
        # 50 * 0.0001 = 0.005
        expected_cost = 0.005
        assert abs(summary['total_cost'] - expected_cost) < 0.0001, \
            f"Cost should be ~{expected_cost}, got {summary['total_cost']}"

    def test_concurrent_budget_checks(self, tracker):
        """Test concurrent budget checking."""
        import threading

        results = []

        def check_budget():
            can_call, remaining = tracker.can_make_call('perplexity', 'sonar')
            results.append((can_call, remaining))

        threads = [threading.Thread(target=check_budget) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All checks should succeed (under budget)
        assert all(result[0] for result in results), "All budget checks should pass"


class TestPerformance:
    """Test performance characteristics."""

    def test_logging_performance(self, tracker):
        """Test that logging is fast enough for production use."""
        import time

        start = time.time()
        for i in range(100):
            tracker.log_api_call(
                model='perplexity:sonar',
                tokens=1000,
                cost=0.001
            )
        elapsed = time.time() - start

        # Should be able to log 100 calls in < 1 second
        assert elapsed < 1.0, f"Logging 100 calls should take < 1s, took {elapsed:.2f}s"

    def test_budget_check_performance(self, tracker):
        """Test that budget checking is fast."""
        import time

        # Log some calls first
        for i in range(50):
            tracker.log_api_call(model='perplexity:sonar', tokens=1000, cost=0.001)

        start = time.time()
        for i in range(100):
            tracker.can_make_call('perplexity', 'sonar')
        elapsed = time.time() - start

        # Should be able to check 100 times in < 0.5 seconds
        assert elapsed < 0.5, f"100 budget checks should take < 0.5s, took {elapsed:.2f}s"


class TestDataCleanup:
    """Test data cleanup functionality."""

    def test_cleanup_old_months(self, tracker):
        """Test cleanup of old monthly data."""
        # Insert old data directly
        conn = tracker._get_connection()
        old_month = '2023-01'

        conn.execute(
            """INSERT OR REPLACE INTO monthly_summary
               (month, total_calls, total_cost, perplexity_cost, data)
               VALUES (?, ?, ?, ?, ?)""",
            (old_month, 100, 5.0, 3.0, '{}')
        )
        conn.commit()

        # Cleanup
        tracker.cleanup_old_data(months_to_keep=6)

        # Old month should be deleted
        cursor = conn.execute(
            "SELECT COUNT(*) as count FROM monthly_summary WHERE month = ?",
            (old_month,)
        )
        count = cursor.fetchone()['count']
        assert count == 0, "Old month data should be deleted"

    def test_cleanup_preserves_recent_data(self, tracker):
        """Test that cleanup preserves recent months."""
        # Log current month data
        tracker.log_api_call(model='perplexity:sonar', tokens=1000, cost=0.001)

        # Cleanup
        tracker.cleanup_old_data(months_to_keep=6)

        # Current month should still have data
        summary = tracker.get_usage_summary()
        assert summary['total_cost'] == 0.001, "Recent data should be preserved"


class TestErrorHandling:
    """Test error handling."""

    def test_invalid_model_format(self, tracker):
        """Test logging with invalid model format."""
        # Should not crash, just log warning
        try:
            tracker.log_api_call(model='invalid', tokens=1000, cost=0.001)
        except Exception as e:
            pytest.fail(f"Should handle invalid model format gracefully: {e}")

    def test_negative_cost(self, tracker):
        """Test logging with negative cost."""
        # Should be handled gracefully
        tracker.log_api_call(model='perplexity:sonar', tokens=1000, cost=-0.001)

        summary = tracker.get_usage_summary()
        # Cost should not go negative
        assert summary['total_cost'] >= 0, "Total cost should not be negative"

    def test_zero_tokens(self, tracker):
        """Test logging with zero tokens."""
        tracker.log_api_call(model='perplexity:sonar', tokens=0, cost=0.0)

        summary = tracker.get_usage_summary()
        assert summary['total_calls'] == 1, "Should count call even with 0 tokens"


class TestRealWorldScenarios:
    """Test realistic usage patterns."""

    def test_typical_daily_usage(self, tracker):
        """Test a typical day of API usage."""
        # Simulate 50 API calls throughout the day
        models = ['perplexity:sonar-pro', 'perplexity:sonar', 'openai:gpt-3.5-turbo']

        for i in range(50):
            model = models[i % len(models)]
            tokens = 500 + (i * 10)
            cost = tokens * 0.000001  # Rough estimate

            tracker.log_api_call(model=model, tokens=tokens, cost=cost)

        summary = tracker.get_usage_summary()

        assert summary['total_calls'] == 50
        assert summary['total_cost'] > 0
        assert len(summary['model_usage']) == 3

    def test_approaching_budget_limit(self, tracker):
        """Test behavior as budget limit is approached."""
        # Use 90% of budget
        tracker.log_api_call(model='perplexity:sonar-pro', tokens=3_000_000, cost=9.0)

        can_call, remaining = tracker.can_make_call('perplexity', 'sonar-pro')

        assert can_call is True, "Should still allow calls at 90%"
        assert 0.9 <= remaining <= 1.1, f"Should have ~$1 remaining, got {remaining}"

        # Push over budget
        tracker.log_api_call(model='perplexity:sonar-pro', tokens=400_000, cost=1.2)

        can_call, remaining = tracker.can_make_call('perplexity', 'sonar-pro')
        assert can_call is False, "Should block calls over budget"
