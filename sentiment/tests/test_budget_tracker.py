"""
Unit tests for 4.0 budget_tracker module.

Tests the Perplexity API budget tracking system.
"""

import pytest
import sqlite3
from datetime import datetime, date, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "cache"))

from cache.budget_tracker import (
    BudgetStatus,
    BudgetInfo,
    BudgetTracker,
    check_budget,
    record_perplexity_call,
    get_budget_status,
    PRICING,
    MCP_COST_ESTIMATES,
)


class TestBudgetStatus:
    """Tests for BudgetStatus enum."""

    def test_status_values(self):
        """BudgetStatus should have correct values."""
        assert BudgetStatus.OK.value == "ok"
        assert BudgetStatus.WARNING.value == "warning"
        assert BudgetStatus.EXHAUSTED.value == "exhausted"

    def test_all_statuses_present(self):
        """Should have exactly 3 statuses."""
        assert len(BudgetStatus) == 3


class TestBudgetInfo:
    """Tests for BudgetInfo dataclass."""

    def test_usage_percent_empty(self):
        """usage_percent should be 0 when no calls made."""
        info = BudgetInfo(
            date="2025-12-09",
            calls_today=0,
            cost_today=0.0,
            calls_remaining=40,
            status=BudgetStatus.OK
        )
        assert info.usage_percent == 0.0

    def test_usage_percent_half(self):
        """usage_percent should be 50 when 20 of 40 calls made."""
        info = BudgetInfo(
            date="2025-12-09",
            calls_today=20,
            cost_today=0.12,
            calls_remaining=20,
            status=BudgetStatus.OK
        )
        assert info.usage_percent == 50.0

    def test_usage_percent_full(self):
        """usage_percent should be 100 when all calls exhausted."""
        info = BudgetInfo(
            date="2025-12-09",
            calls_today=40,
            cost_today=0.24,
            calls_remaining=0,
            status=BudgetStatus.EXHAUSTED
        )
        assert info.usage_percent == 100.0

    def test_usage_percent_warning_threshold(self):
        """usage_percent should be 80 at warning threshold."""
        info = BudgetInfo(
            date="2025-12-09",
            calls_today=32,
            cost_today=0.192,
            calls_remaining=8,
            status=BudgetStatus.WARNING
        )
        assert info.usage_percent == 80.0


class TestBudgetTracker:
    """Tests for BudgetTracker class."""

    @pytest.fixture
    def temp_tracker(self, tmp_path):
        """Create a temporary tracker for testing."""
        db_path = tmp_path / "test_budget.db"
        return BudgetTracker(db_path=db_path)

    def test_constants(self):
        """Verify budget constants are set correctly."""
        assert BudgetTracker.MONTHLY_BUDGET == 5.00
        assert BudgetTracker.MAX_DAILY_CALLS == 40
        assert BudgetTracker.WARN_THRESHOLD == 0.80
        assert BudgetTracker.COST_PER_CALL_ESTIMATE == 0.006

    def test_init_creates_database(self, tmp_path):
        """Tracker initialization should create database file."""
        db_path = tmp_path / "new_budget.db"
        tracker = BudgetTracker(db_path=db_path)
        assert db_path.exists()

    def test_init_creates_table(self, temp_tracker):
        """Tracker should create api_budget table."""
        with sqlite3.connect(temp_tracker.db_path) as conn:
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='api_budget'
            """)
            assert cursor.fetchone() is not None

    def test_get_info_initial_state(self, temp_tracker):
        """get_info should return OK status with no calls."""
        info = temp_tracker.get_info()
        assert info.calls_today == 0
        assert info.cost_today == 0.0
        assert info.calls_remaining == 40
        assert info.status == BudgetStatus.OK

    def test_can_call_when_ok(self, temp_tracker):
        """can_call should return True when under limit."""
        assert temp_tracker.can_call() is True

    def test_should_warn_when_ok(self, temp_tracker):
        """should_warn should return False when under warning threshold."""
        assert temp_tracker.should_warn() is False

    def test_record_call_increments_count(self, temp_tracker):
        """record_call should increment call count."""
        temp_tracker.record_call()
        info = temp_tracker.get_info()
        assert info.calls_today == 1

    def test_record_call_uses_default_cost(self, temp_tracker):
        """record_call should use default cost estimate."""
        temp_tracker.record_call()
        info = temp_tracker.get_info()
        assert info.cost_today == pytest.approx(0.006, rel=0.01)

    def test_record_call_with_custom_cost(self, temp_tracker):
        """record_call should use custom cost when provided."""
        temp_tracker.record_call(cost=0.01)
        info = temp_tracker.get_info()
        assert info.cost_today == 0.01

    def test_record_call_accumulates(self, temp_tracker):
        """Multiple calls should accumulate correctly."""
        temp_tracker.record_call(cost=0.01)
        temp_tracker.record_call(cost=0.02)
        temp_tracker.record_call(cost=0.01)

        info = temp_tracker.get_info()
        assert info.calls_today == 3
        assert info.cost_today == pytest.approx(0.04, rel=0.01)

    def test_warning_status_at_80_percent(self, temp_tracker):
        """Should enter WARNING status at 80% usage (32 calls)."""
        # Record 32 calls
        for _ in range(32):
            temp_tracker.record_call(cost=0.006)

        info = temp_tracker.get_info()
        assert info.status == BudgetStatus.WARNING
        assert temp_tracker.should_warn() is True
        assert temp_tracker.can_call() is True

    def test_warning_status_at_79_percent(self, temp_tracker):
        """Should remain OK at 79% usage (31 calls)."""
        for _ in range(31):
            temp_tracker.record_call(cost=0.006)

        info = temp_tracker.get_info()
        assert info.status == BudgetStatus.OK

    def test_exhausted_status_at_100_percent(self, temp_tracker):
        """Should enter EXHAUSTED status at 100% usage (40 calls)."""
        for _ in range(40):
            temp_tracker.record_call(cost=0.006)

        info = temp_tracker.get_info()
        assert info.status == BudgetStatus.EXHAUSTED
        assert temp_tracker.can_call() is False

    def test_can_call_false_when_exhausted(self, temp_tracker):
        """can_call should return False when exhausted."""
        for _ in range(40):
            temp_tracker.record_call(cost=0.006)

        assert temp_tracker.can_call() is False

    def test_calls_remaining_calculation(self, temp_tracker):
        """calls_remaining should be correctly calculated."""
        for _ in range(15):
            temp_tracker.record_call(cost=0.006)

        info = temp_tracker.get_info()
        assert info.calls_remaining == 25

    def test_calls_remaining_zero_when_exhausted(self, temp_tracker):
        """calls_remaining should be 0 when exhausted."""
        for _ in range(45):  # Over limit
            temp_tracker.record_call(cost=0.006)

        info = temp_tracker.get_info()
        assert info.calls_remaining == 0

    def test_reset_today_clears_counts(self, temp_tracker):
        """reset_today should clear today's counts."""
        temp_tracker.record_call(cost=0.01)
        temp_tracker.record_call(cost=0.01)
        temp_tracker.reset_today()

        info = temp_tracker.get_info()
        assert info.calls_today == 0
        assert info.cost_today == 0.0

    def test_get_monthly_summary_empty(self, temp_tracker):
        """get_monthly_summary should handle empty database."""
        summary = temp_tracker.get_monthly_summary()
        assert summary['total_calls'] == 0
        assert summary['total_cost'] == 0.0
        assert summary['budget_remaining'] == 5.00

    def test_get_monthly_summary_with_data(self, temp_tracker):
        """get_monthly_summary should return correct totals."""
        # Record some calls
        temp_tracker.record_call(cost=0.01)
        temp_tracker.record_call(cost=0.02)
        temp_tracker.record_call(cost=0.01)

        summary = temp_tracker.get_monthly_summary()
        assert summary['total_calls'] == 3
        assert summary['total_cost'] == pytest.approx(0.04, rel=0.01)
        assert summary['budget_remaining'] == pytest.approx(4.96, rel=0.01)

    def test_get_monthly_summary_month_format(self, temp_tracker):
        """get_monthly_summary should return current month in correct format."""
        summary = temp_tracker.get_monthly_summary()
        expected_month = date.today().strftime("%Y-%m")
        assert summary['month'] == expected_month

    def test_get_today_returns_iso_format(self, temp_tracker):
        """_get_today should return date in ISO format."""
        today = temp_tracker._get_today()
        assert today == date.today().isoformat()

    def test_date_rollover_creates_new_row(self, temp_tracker):
        """New day should start with fresh counts."""
        # Record calls "today"
        temp_tracker.record_call(cost=0.01)

        # Simulate next day by patching _get_today
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        with patch.object(temp_tracker, '_get_today', return_value=tomorrow):
            info = temp_tracker.get_info()
            assert info.calls_today == 0
            assert info.date == tomorrow


class TestConvenienceFunctions:
    """Tests for module-level convenience functions."""

    def test_check_budget_ok(self):
        """check_budget should return True and OK message when under limit."""
        with patch('cache.budget_tracker.BudgetTracker') as MockTracker:
            mock_instance = MockTracker.return_value
            mock_instance.get_info.return_value = BudgetInfo(
                date="2025-12-09",
                calls_today=10,
                cost_today=0.06,
                calls_remaining=30,
                status=BudgetStatus.OK
            )
            mock_instance.MAX_DAILY_CALLS = 40

            can_call, message = check_budget()
            assert can_call is True
            assert "Budget OK" in message
            assert "30 calls remaining" in message

    def test_check_budget_warning(self):
        """check_budget should return True with warning when at threshold."""
        with patch('cache.budget_tracker.BudgetTracker') as MockTracker:
            mock_instance = MockTracker.return_value
            mock_info = MagicMock(spec=BudgetInfo)
            mock_info.calls_today = 35
            mock_info.usage_percent = 87.5
            mock_info.status = BudgetStatus.WARNING
            mock_instance.get_info.return_value = mock_info
            mock_instance.MAX_DAILY_CALLS = 40

            can_call, message = check_budget()
            assert can_call is True
            assert "Warning" in message

    def test_check_budget_exhausted(self):
        """check_budget should return False when exhausted."""
        with patch('cache.budget_tracker.BudgetTracker') as MockTracker:
            mock_instance = MockTracker.return_value
            mock_instance.get_info.return_value = BudgetInfo(
                date="2025-12-09",
                calls_today=40,
                cost_today=0.24,
                calls_remaining=0,
                status=BudgetStatus.EXHAUSTED
            )
            mock_instance.MAX_DAILY_CALLS = 40

            can_call, message = check_budget()
            assert can_call is False
            assert "exhausted" in message.lower()
            assert "WebSearch fallback" in message

    def test_record_perplexity_call_default_cost(self):
        """record_perplexity_call should use default cost matching COST_PER_CALL_ESTIMATE."""
        with patch('cache.budget_tracker.BudgetTracker') as MockTracker:
            mock_instance = MockTracker.return_value

            record_perplexity_call()

            # Default matches COST_PER_CALL_ESTIMATE ($0.006 for sonar model)
            mock_instance.record_call.assert_called_once_with(0.006)

    def test_record_perplexity_call_custom_cost(self):
        """record_perplexity_call should accept custom cost."""
        with patch('cache.budget_tracker.BudgetTracker') as MockTracker:
            mock_instance = MockTracker.return_value

            record_perplexity_call(cost=0.02)

            # Function passes cost as positional arg
            mock_instance.record_call.assert_called_once_with(0.02)

    def test_get_budget_status_format(self):
        """get_budget_status should return formatted string."""
        with patch('cache.budget_tracker.BudgetTracker') as MockTracker:
            mock_instance = MockTracker.return_value
            mock_instance.MAX_DAILY_CALLS = 40
            mock_instance.get_info.return_value = BudgetInfo(
                date="2025-12-09",
                calls_today=15,
                cost_today=0.09,
                calls_remaining=25,
                status=BudgetStatus.OK
            )
            mock_instance.get_monthly_summary.return_value = {
                'total_calls': 100,
                'total_cost': 0.60,
                'total_output_tokens': 0,
                'total_reasoning_tokens': 0,
                'total_search_requests': 0
            }

            status = get_budget_status()

            assert "Budget Status" in status
            assert "Today:" in status
            assert "Month:" in status
            assert "OK" in status.upper()


class TestEdgeCases:
    """Edge case tests."""

    @pytest.fixture
    def temp_tracker(self, tmp_path):
        """Create a temporary tracker for testing."""
        db_path = tmp_path / "test_budget.db"
        return BudgetTracker(db_path=db_path)

    def test_record_call_rejects_negative_cost(self, temp_tracker):
        """record_call should reject negative costs."""
        with pytest.raises(ValueError, match="negative"):
            temp_tracker.record_call(cost=-0.01)

    def test_record_call_rejects_nan_cost(self, temp_tracker):
        """record_call should reject NaN costs."""
        import math
        with pytest.raises(ValueError, match="finite"):
            temp_tracker.record_call(cost=math.nan)

    def test_record_call_rejects_inf_cost(self, temp_tracker):
        """record_call should reject infinite costs."""
        import math
        with pytest.raises(ValueError, match="finite"):
            temp_tracker.record_call(cost=math.inf)

    def test_very_high_cost(self, temp_tracker):
        """Should handle unusually high costs."""
        temp_tracker.record_call(cost=1.0)
        info = temp_tracker.get_info()
        assert info.cost_today == 1.0

    def test_zero_cost(self, temp_tracker):
        """Should handle zero cost calls."""
        temp_tracker.record_call(cost=0.0)
        info = temp_tracker.get_info()
        assert info.calls_today == 1
        assert info.cost_today == 0.0

    def test_many_calls(self, temp_tracker):
        """Should handle many calls correctly."""
        for _ in range(100):
            temp_tracker.record_call(cost=0.006)

        info = temp_tracker.get_info()
        assert info.calls_today == 100
        assert info.status == BudgetStatus.EXHAUSTED

    def test_creates_parent_directory(self, tmp_path):
        """Should create parent directories if they don't exist."""
        db_path = tmp_path / "nested" / "dir" / "budget.db"
        tracker = BudgetTracker(db_path=db_path)
        assert db_path.exists()

    def test_concurrent_record_calls(self, temp_tracker):
        """Should handle rapid successive calls."""
        # Basic test - full concurrency testing would need threads
        for i in range(10):
            temp_tracker.record_call(cost=0.01)

        info = temp_tracker.get_info()
        assert info.calls_today == 10
        assert info.cost_today == pytest.approx(0.10, rel=0.01)

    def test_info_date_matches_today(self, temp_tracker):
        """BudgetInfo date should match today's date."""
        info = temp_tracker.get_info()
        assert info.date == date.today().isoformat()

    def test_multiple_trackers_same_db(self, tmp_path):
        """Multiple tracker instances should share same database."""
        db_path = tmp_path / "shared.db"

        tracker1 = BudgetTracker(db_path=db_path)
        tracker1.record_call(cost=0.01)

        tracker2 = BudgetTracker(db_path=db_path)
        info = tracker2.get_info()

        assert info.calls_today == 1

    def test_historical_data_preserved(self, temp_tracker):
        """Historical data should be preserved across days."""
        # Record today
        temp_tracker.record_call(cost=0.01)

        # Simulate "yesterday" by inserting directly
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        with sqlite3.connect(temp_tracker.db_path) as conn:
            conn.execute("""
                INSERT INTO api_budget (date, calls, cost, last_updated)
                VALUES (?, 20, 0.12, ?)
            """, (yesterday, datetime.now(timezone.utc).isoformat()))
            conn.commit()

        # Today's count should still be 1
        info = temp_tracker.get_info()
        assert info.calls_today == 1

        # Monthly summary should include both days
        summary = temp_tracker.get_monthly_summary()
        assert summary['total_calls'] == 21


class TestWarningThreshold:
    """Tests for warning threshold behavior."""

    @pytest.fixture
    def temp_tracker(self, tmp_path):
        """Create a temporary tracker for testing."""
        db_path = tmp_path / "test_budget.db"
        return BudgetTracker(db_path=db_path)

    def test_exactly_32_calls_is_warning(self, temp_tracker):
        """32 calls (80%) should trigger WARNING."""
        for _ in range(32):
            temp_tracker.record_call(cost=0.006)

        assert temp_tracker.get_info().status == BudgetStatus.WARNING

    def test_33_calls_still_warning(self, temp_tracker):
        """33 calls should still be WARNING."""
        for _ in range(33):
            temp_tracker.record_call(cost=0.006)

        assert temp_tracker.get_info().status == BudgetStatus.WARNING

    def test_39_calls_still_warning(self, temp_tracker):
        """39 calls should still be WARNING."""
        for _ in range(39):
            temp_tracker.record_call(cost=0.006)

        info = temp_tracker.get_info()
        assert info.status == BudgetStatus.WARNING
        assert info.calls_remaining == 1

    def test_40_calls_is_exhausted(self, temp_tracker):
        """40 calls should be EXHAUSTED."""
        for _ in range(40):
            temp_tracker.record_call(cost=0.006)

        assert temp_tracker.get_info().status == BudgetStatus.EXHAUSTED

    def test_warning_threshold_constant(self):
        """WARN_THRESHOLD should be 0.80 (80%)."""
        assert BudgetTracker.WARN_THRESHOLD == 0.80
        # 80% of 40 = 32
        assert int(BudgetTracker.MAX_DAILY_CALLS * BudgetTracker.WARN_THRESHOLD) == 32


class TestTokenTracking:
    """Tests for token-based cost tracking."""

    @pytest.fixture
    def temp_tracker(self, tmp_path):
        """Create a temporary tracker for testing."""
        db_path = tmp_path / "test_budget.db"
        return BudgetTracker(db_path=db_path)

    def test_pricing_constants(self):
        """Verify pricing constants match invoice rates."""
        assert PRICING["sonar_output"] == 0.000001
        assert PRICING["sonar_pro_output"] == 0.000015
        assert PRICING["reasoning_pro"] == 0.000003
        assert PRICING["search_request"] == 0.005

    def test_mcp_cost_estimates(self):
        """Verify MCP cost estimates are reasonable."""
        assert MCP_COST_ESTIMATES["perplexity_ask"] == 0.001
        assert MCP_COST_ESTIMATES["perplexity_search"] == 0.005
        assert MCP_COST_ESTIMATES["perplexity_research"] == 0.008
        assert MCP_COST_ESTIMATES["perplexity_reason"] == 0.012

    def test_record_call_with_tokens(self, temp_tracker):
        """record_call should track token counts."""
        temp_tracker.record_call(
            cost=0.01,
            output_tokens=1000,
            reasoning_tokens=500,
            search_requests=1
        )
        info = temp_tracker.get_info()
        assert info.calls_today == 1
        assert info.output_tokens == 1000
        assert info.reasoning_tokens == 500
        assert info.search_requests == 1

    def test_record_tokens_sonar(self, temp_tracker):
        """record_tokens should calculate sonar cost correctly."""
        # 1000 output tokens at $0.000001/token = $0.001
        cost = temp_tracker.record_tokens(output_tokens=1000, model="sonar")
        assert cost == pytest.approx(0.001, rel=0.01)

        info = temp_tracker.get_info()
        assert info.output_tokens == 1000
        assert info.cost_today == pytest.approx(0.001, rel=0.01)

    def test_record_tokens_sonar_pro(self, temp_tracker):
        """record_tokens should calculate sonar-pro cost correctly."""
        # 1000 output tokens at $0.000015/token = $0.015
        cost = temp_tracker.record_tokens(output_tokens=1000, model="sonar-pro")
        assert cost == pytest.approx(0.015, rel=0.01)

    def test_record_tokens_reasoning(self, temp_tracker):
        """record_tokens should calculate reasoning token cost correctly."""
        # 1000 reasoning tokens at $0.000003/token = $0.003
        cost = temp_tracker.record_tokens(reasoning_tokens=1000)
        assert cost == pytest.approx(0.003, rel=0.01)

        info = temp_tracker.get_info()
        assert info.reasoning_tokens == 1000

    def test_record_tokens_search(self, temp_tracker):
        """record_tokens should calculate search request cost correctly."""
        # 1 search request at $0.005 = $0.005
        cost = temp_tracker.record_tokens(search_requests=1)
        assert cost == pytest.approx(0.005, rel=0.01)

        info = temp_tracker.get_info()
        assert info.search_requests == 1

    def test_record_tokens_combined(self, temp_tracker):
        """record_tokens should handle combined costs correctly."""
        # 1000 output + 2000 reasoning + 2 searches
        # = $0.001 + $0.006 + $0.010 = $0.017
        cost = temp_tracker.record_tokens(
            output_tokens=1000,
            reasoning_tokens=2000,
            search_requests=2,
            model="sonar"
        )
        assert cost == pytest.approx(0.017, rel=0.01)

    def test_record_mcp_operation_ask(self, temp_tracker):
        """record_mcp_operation should use estimated cost for ask."""
        cost = temp_tracker.record_mcp_operation("perplexity_ask")
        assert cost == 0.001

        info = temp_tracker.get_info()
        assert info.calls_today == 1
        assert info.cost_today == 0.001

    def test_record_mcp_operation_search(self, temp_tracker):
        """record_mcp_operation should use estimated cost for search."""
        cost = temp_tracker.record_mcp_operation("perplexity_search")
        assert cost == 0.005

    def test_record_mcp_operation_research(self, temp_tracker):
        """record_mcp_operation should use estimated cost for research."""
        cost = temp_tracker.record_mcp_operation("perplexity_research")
        assert cost == 0.008

    def test_record_mcp_operation_reason(self, temp_tracker):
        """record_mcp_operation should use estimated cost for reason."""
        cost = temp_tracker.record_mcp_operation("perplexity_reason")
        assert cost == 0.012

    def test_record_mcp_operation_unknown(self, temp_tracker):
        """record_mcp_operation should use default cost for unknown operations."""
        cost = temp_tracker.record_mcp_operation("unknown_operation")
        assert cost == BudgetTracker.COST_PER_CALL_ESTIMATE

    def test_tokens_accumulate(self, temp_tracker):
        """Token counts should accumulate across calls."""
        temp_tracker.record_tokens(output_tokens=500)
        temp_tracker.record_tokens(output_tokens=300, reasoning_tokens=1000)

        info = temp_tracker.get_info()
        assert info.output_tokens == 800
        assert info.reasoning_tokens == 1000

    def test_monthly_summary_includes_tokens(self, temp_tracker):
        """Monthly summary should include token breakdown."""
        temp_tracker.record_tokens(output_tokens=1000, reasoning_tokens=500, search_requests=2)

        summary = temp_tracker.get_monthly_summary()
        assert summary['total_output_tokens'] == 1000
        assert summary['total_reasoning_tokens'] == 500
        assert summary['total_search_requests'] == 2

    def test_reset_today_clears_tokens(self, temp_tracker):
        """reset_today should clear token counts."""
        temp_tracker.record_tokens(output_tokens=1000, reasoning_tokens=500, search_requests=2)
        temp_tracker.reset_today()

        info = temp_tracker.get_info()
        assert info.output_tokens == 0
        assert info.reasoning_tokens == 0
        assert info.search_requests == 0

    def test_budget_info_token_fields_default(self, temp_tracker):
        """BudgetInfo should have token fields defaulting to 0."""
        info = temp_tracker.get_info()
        assert info.output_tokens == 0
        assert info.reasoning_tokens == 0
        assert info.search_requests == 0

    def test_record_call_rejects_negative_tokens(self, temp_tracker):
        """record_call should reject negative token counts."""
        with pytest.raises(ValueError, match="negative"):
            temp_tracker.record_call(cost=0.01, output_tokens=-100)

    def test_record_call_rejects_excessive_tokens(self, temp_tracker):
        """record_call should reject excessive token counts."""
        from cache.budget_tracker import MAX_TOKENS_PER_CALL
        with pytest.raises(ValueError, match="exceeds maximum"):
            temp_tracker.record_call(cost=0.01, output_tokens=MAX_TOKENS_PER_CALL + 1)

    def test_record_call_rejects_non_integer_tokens(self, temp_tracker):
        """record_call should reject non-integer token counts."""
        with pytest.raises(ValueError, match="must be an integer"):
            temp_tracker.record_call(cost=0.01, output_tokens=100.5)

    def test_schema_migration(self, tmp_path):
        """Should add token columns to existing databases."""
        db_path = tmp_path / "legacy.db"

        # Create legacy schema without token columns
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE api_budget (
                    date TEXT PRIMARY KEY,
                    calls INTEGER DEFAULT 0,
                    cost REAL DEFAULT 0.0,
                    last_updated TEXT
                )
            """)
            conn.execute("""
                INSERT INTO api_budget (date, calls, cost, last_updated)
                VALUES ('2025-01-15', 5, 0.03, '2025-01-15T12:00:00')
            """)
            conn.commit()

        # Create tracker - should add columns
        tracker = BudgetTracker(db_path=db_path)

        # Verify columns exist and can be queried
        with sqlite3.connect(db_path) as conn:
            cursor = conn.execute("""
                SELECT output_tokens, reasoning_tokens, search_requests
                FROM api_budget WHERE date = '2025-01-15'
            """)
            row = cursor.fetchone()
            # Legacy data should have 0 for token columns
            assert row[0] == 0 or row[0] is None
            assert row[1] == 0 or row[1] is None
            assert row[2] == 0 or row[2] is None

        # New calls should work with token tracking
        tracker.record_tokens(output_tokens=100)
        info = tracker.get_info()
        assert info.output_tokens == 100
