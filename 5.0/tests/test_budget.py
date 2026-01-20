# 5.0/tests/test_budget.py
import pytest
import sqlite3
import tempfile
import os
from datetime import date
from src.core.budget import BudgetTracker, PRICING

@pytest.fixture
def tracker():
    """Create tracker with temp database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield BudgetTracker(db_path=db_path)
    os.unlink(db_path)

def test_record_api_call(tracker):
    """record_call increments daily count."""
    tracker.record_call("perplexity", cost=0.006)
    stats = tracker.get_daily_stats("perplexity")
    assert stats["calls"] == 1
    assert stats["cost"] == 0.006

def test_daily_limit_check(tracker):
    """can_call returns False when daily limit exceeded."""
    # Record 40 calls (daily limit)
    for _ in range(40):
        tracker.record_call("perplexity", cost=0.005)

    assert tracker.can_call("perplexity") is False

def test_daily_limit_resets(tracker):
    """Daily limit resets on new day."""
    # Record calls for yesterday
    yesterday = "2025-12-11"
    for _ in range(40):
        tracker.record_call("perplexity", cost=0.005, date_str=yesterday)

    # Today should be allowed
    assert tracker.can_call("perplexity") is True

def test_monthly_budget_check(tracker):
    """can_call returns False when monthly budget exceeded."""
    # Record $5 worth of calls (monthly budget)
    for _ in range(100):
        tracker.record_call("perplexity", cost=0.05)  # $5 total

    assert tracker.can_call("perplexity") is False

def test_get_budget_summary(tracker):
    """get_summary returns calls and budget remaining."""
    tracker.record_call("perplexity", cost=0.10)
    tracker.record_call("perplexity", cost=0.15)

    summary = tracker.get_summary()
    assert summary["today_calls"] == 2
    assert summary["today_cost"] == 0.25
    assert summary["month_cost"] == 0.25
    assert summary["budget_remaining"] == 4.75  # $5 - $0.25


def test_record_call_rejects_negative_cost(tracker):
    """record_call should reject negative costs."""
    import pytest
    with pytest.raises(ValueError, match="negative"):
        tracker.record_call("perplexity", cost=-0.01)


def test_record_call_rejects_nan_cost(tracker):
    """record_call should reject NaN costs."""
    import pytest
    import math
    with pytest.raises(ValueError, match="finite"):
        tracker.record_call("perplexity", cost=math.nan)


def test_record_call_rejects_inf_cost(tracker):
    """record_call should reject infinite costs."""
    import pytest
    import math
    with pytest.raises(ValueError, match="finite"):
        tracker.record_call("perplexity", cost=math.inf)


class TestTryAcquireCall:
    """Tests for atomic try_acquire_call methods."""

    def test_try_acquire_call_success(self, tracker):
        """try_acquire_call should succeed when under limits."""
        result = tracker.try_acquire_call("perplexity", cost=0.006)
        assert result is True

        stats = tracker.get_daily_stats("perplexity")
        assert stats["calls"] == 1

    def test_try_acquire_call_daily_limit(self, tracker):
        """try_acquire_call should fail when daily limit exceeded."""
        # Record 40 calls to hit limit
        for _ in range(40):
            tracker.record_call("perplexity", cost=0.005)

        # Next acquire should fail
        result = tracker.try_acquire_call("perplexity", cost=0.006)
        assert result is False

        # Count should still be 40
        stats = tracker.get_daily_stats("perplexity")
        assert stats["calls"] == 40

    def test_try_acquire_call_monthly_budget(self, tracker):
        """try_acquire_call should fail when monthly budget exceeded."""
        # Record $5 worth of calls
        tracker.record_call("perplexity", cost=5.00)

        # Next acquire should fail
        result = tracker.try_acquire_call("perplexity", cost=0.006)
        assert result is False

    def test_try_acquire_call_with_tokens(self, tracker):
        """try_acquire_call should track tokens."""
        result = tracker.try_acquire_call(
            "perplexity",
            cost=0.01,
            output_tokens=1000,
            reasoning_tokens=500,
            search_requests=1
        )
        assert result is True

        stats = tracker.get_daily_stats("perplexity")
        assert stats["output_tokens"] == 1000
        assert stats["reasoning_tokens"] == 500
        assert stats["search_requests"] == 1


class TestTryAcquireCallAsync:
    """Tests for async try_acquire_call_async method."""

    @pytest.mark.asyncio
    async def test_try_acquire_call_async_success(self, tracker):
        """try_acquire_call_async should succeed when under limits."""
        result = await tracker.try_acquire_call_async("perplexity", cost=0.006)
        assert result is True

        stats = tracker.get_daily_stats("perplexity")
        assert stats["calls"] == 1

    @pytest.mark.asyncio
    async def test_try_acquire_call_async_daily_limit(self, tracker):
        """try_acquire_call_async should fail when daily limit exceeded."""
        # Record 40 calls to hit limit
        for _ in range(40):
            tracker.record_call("perplexity", cost=0.005)

        # Next acquire should fail
        result = await tracker.try_acquire_call_async("perplexity", cost=0.006)
        assert result is False

    @pytest.mark.asyncio
    async def test_try_acquire_call_async_monthly_budget(self, tracker):
        """try_acquire_call_async should fail when monthly budget exceeded."""
        # Record $5 worth of calls
        tracker.record_call("perplexity", cost=5.00)

        # Next acquire should fail
        result = await tracker.try_acquire_call_async("perplexity", cost=0.006)
        assert result is False

    @pytest.mark.asyncio
    async def test_try_acquire_call_async_with_tokens(self, tracker):
        """try_acquire_call_async should track tokens."""
        result = await tracker.try_acquire_call_async(
            "perplexity",
            cost=0.01,
            output_tokens=2000,
            reasoning_tokens=1000,
            search_requests=2
        )
        assert result is True

        stats = tracker.get_daily_stats("perplexity")
        assert stats["output_tokens"] == 2000
        assert stats["reasoning_tokens"] == 1000
        assert stats["search_requests"] == 2


class TestTokenTracking:
    """Tests for token-based cost tracking."""

    def test_pricing_constants(self):
        """Verify pricing constants match invoice rates."""
        assert PRICING["sonar_output"] == 0.000001
        assert PRICING["sonar_pro_output"] == 0.000015
        assert PRICING["reasoning_pro"] == 0.000003
        assert PRICING["search_request"] == 0.005

    def test_record_call_with_tokens(self, tracker):
        """record_call should track token counts."""
        tracker.record_call(
            "perplexity",
            cost=0.01,
            output_tokens=1000,
            reasoning_tokens=500,
            search_requests=1
        )
        stats = tracker.get_daily_stats("perplexity")
        assert stats["calls"] == 1
        assert stats["output_tokens"] == 1000
        assert stats["reasoning_tokens"] == 500
        assert stats["search_requests"] == 1

    def test_record_tokens_sonar(self, tracker):
        """record_tokens should calculate sonar cost correctly."""
        # 1000 output tokens at $0.000001/token = $0.001
        cost = tracker.record_tokens(output_tokens=1000, model="sonar")
        assert cost == pytest.approx(0.001, rel=0.01)

        stats = tracker.get_daily_stats("perplexity")
        assert stats["output_tokens"] == 1000

    def test_record_tokens_sonar_pro(self, tracker):
        """record_tokens should calculate sonar-pro cost correctly."""
        # 1000 output tokens at $0.000015/token = $0.015
        cost = tracker.record_tokens(output_tokens=1000, model="sonar-pro")
        assert cost == pytest.approx(0.015, rel=0.01)

    def test_record_tokens_reasoning(self, tracker):
        """record_tokens should calculate reasoning token cost correctly."""
        # 1000 reasoning tokens at $0.000003/token = $0.003
        cost = tracker.record_tokens(reasoning_tokens=1000)
        assert cost == pytest.approx(0.003, rel=0.01)

        stats = tracker.get_daily_stats("perplexity")
        assert stats["reasoning_tokens"] == 1000

    def test_record_tokens_search(self, tracker):
        """record_tokens should calculate search request cost correctly."""
        # 1 search request at $0.005 = $0.005
        cost = tracker.record_tokens(search_requests=1)
        assert cost == pytest.approx(0.005, rel=0.01)

        stats = tracker.get_daily_stats("perplexity")
        assert stats["search_requests"] == 1

    def test_tokens_accumulate(self, tracker):
        """Token counts should accumulate across calls."""
        tracker.record_tokens(output_tokens=500)
        tracker.record_tokens(output_tokens=300, reasoning_tokens=1000)

        stats = tracker.get_daily_stats("perplexity")
        assert stats["output_tokens"] == 800
        assert stats["reasoning_tokens"] == 1000

    def test_summary_includes_tokens(self, tracker):
        """Summary should include token breakdown."""
        tracker.record_tokens(output_tokens=1000, reasoning_tokens=500, search_requests=2)

        summary = tracker.get_summary()
        assert summary["today_output_tokens"] == 1000
        assert summary["today_reasoning_tokens"] == 500
        assert summary["today_search_requests"] == 2
        assert summary["month_output_tokens"] == 1000
        assert summary["month_reasoning_tokens"] == 500
        assert summary["month_search_requests"] == 2

    def test_get_monthly_tokens(self, tracker):
        """get_monthly_tokens should return token totals."""
        tracker.record_tokens(output_tokens=1000, reasoning_tokens=500, search_requests=2)

        tokens = tracker.get_monthly_tokens("perplexity")
        assert tokens["output_tokens"] == 1000
        assert tokens["reasoning_tokens"] == 500
        assert tokens["search_requests"] == 2

    def test_schema_migration(self, tmp_path):
        """Should add token columns to existing databases."""
        db_path = str(tmp_path / "legacy.db")

        # Create legacy schema without token columns
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE api_budget (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                service TEXT NOT NULL,
                calls INTEGER DEFAULT 0,
                cost REAL DEFAULT 0.0,
                updated_at TEXT NOT NULL,
                UNIQUE(date, service)
            )
        """)
        conn.execute("""
            INSERT INTO api_budget (date, service, calls, cost, updated_at)
            VALUES ('2025-01-15', 'perplexity', 5, 0.03, '2025-01-15T12:00:00')
        """)
        conn.commit()
        conn.close()

        # Create tracker - should add columns
        tracker = BudgetTracker(db_path=db_path)

        # Verify columns exist and can be queried
        conn = sqlite3.connect(db_path)
        cursor = conn.execute("""
            SELECT output_tokens, reasoning_tokens, search_requests
            FROM api_budget WHERE date = '2025-01-15'
        """)
        row = cursor.fetchone()
        # Legacy data should have 0 for token columns
        assert row[0] == 0 or row[0] is None
        assert row[1] == 0 or row[1] is None
        assert row[2] == 0 or row[2] is None
        conn.close()

        # New calls should work with token tracking
        tracker.record_tokens(output_tokens=100)
        stats = tracker.get_daily_stats("perplexity")
        assert stats["output_tokens"] == 100


class TestTokenValidation:
    """Tests for token count validation."""

    def test_record_call_rejects_negative_tokens(self, tracker):
        """record_call should reject negative token counts."""
        with pytest.raises(ValueError, match="negative"):
            tracker.record_call("perplexity", cost=0.01, output_tokens=-100)

    def test_record_call_rejects_excessive_tokens(self, tracker):
        """record_call should reject token counts exceeding bounds."""
        from src.core.budget import MAX_TOKENS_PER_CALL
        with pytest.raises(ValueError, match="exceeds maximum"):
            tracker.record_call("perplexity", cost=0.01, output_tokens=MAX_TOKENS_PER_CALL + 1)

    def test_record_call_rejects_non_integer_tokens(self, tracker):
        """record_call should reject non-integer token counts."""
        with pytest.raises(ValueError, match="must be an integer"):
            tracker.record_call("perplexity", cost=0.01, output_tokens=100.5)

    def test_try_acquire_call_validates_tokens(self, tracker):
        """try_acquire_call should validate token counts."""
        with pytest.raises(ValueError, match="negative"):
            tracker.try_acquire_call("perplexity", cost=0.01, reasoning_tokens=-500)

    @pytest.mark.asyncio
    async def test_try_acquire_call_async_validates_tokens(self, tracker):
        """try_acquire_call_async should validate token counts."""
        with pytest.raises(ValueError, match="negative"):
            await tracker.try_acquire_call_async("perplexity", cost=0.01, search_requests=-1)
