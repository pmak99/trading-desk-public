"""
Concurrent budget write tests for 4.0 budget_tracker.

Tests thread safety of the BudgetTracker under concurrent access,
validating that the threading.Lock() protection works correctly.
"""

import pytest
import threading
import sqlite3
from datetime import date
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "cache"))

from cache.budget_tracker import BudgetTracker, BudgetStatus


class TestConcurrentRecordCalls:
    """Tests for concurrent record_call() operations."""

    @pytest.fixture
    def temp_tracker(self, tmp_path):
        """Create a temporary tracker for testing."""
        db_path = tmp_path / "test_concurrent.db"
        return BudgetTracker(db_path=db_path)

    def test_concurrent_writes_no_lost_updates(self, temp_tracker):
        """10 concurrent threads each recording 1 call should produce exactly 10 calls."""
        errors = []
        n_threads = 10

        def record_one_call():
            try:
                temp_tracker.record_call(cost=0.006)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record_one_call) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent writes: {errors}"
        info = temp_tracker.get_info()
        assert info.calls_today == n_threads

    def test_concurrent_writes_cost_accumulation(self, temp_tracker):
        """Concurrent writes should accumulate costs correctly."""
        errors = []
        n_threads = 20
        cost_per_call = 0.01

        def record_with_cost():
            try:
                temp_tracker.record_call(cost=cost_per_call)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record_with_cost) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        info = temp_tracker.get_info()
        assert info.calls_today == n_threads
        assert info.cost_today == pytest.approx(n_threads * cost_per_call, rel=0.01)

    def test_concurrent_token_recording(self, temp_tracker):
        """Concurrent record_tokens() calls should accumulate correctly."""
        errors = []
        n_threads = 10
        tokens_per_call = 100

        def record_tokens():
            try:
                temp_tracker.record_tokens(
                    output_tokens=tokens_per_call,
                    reasoning_tokens=50,
                    search_requests=1,
                    model="sonar",
                )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record_tokens) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        info = temp_tracker.get_info()
        assert info.calls_today == n_threads
        assert info.output_tokens == n_threads * tokens_per_call
        assert info.reasoning_tokens == n_threads * 50
        assert info.search_requests == n_threads * 1

    def test_concurrent_budget_check_and_write(self, temp_tracker):
        """Concurrent can_call() + record_call() should not crash."""
        errors = []
        n_threads = 15

        def check_and_record():
            try:
                if temp_tracker.can_call():
                    temp_tracker.record_call(cost=0.006)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=check_and_record) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        info = temp_tracker.get_info()
        # All 15 should succeed since limit is 40
        assert info.calls_today == n_threads

    def test_concurrent_race_near_budget_limit(self, temp_tracker):
        """Near budget limit, concurrent writes should not cause data corruption."""
        # Pre-fill to 38 calls (2 remaining)
        for _ in range(38):
            temp_tracker.record_call(cost=0.006)

        errors = []
        n_threads = 5  # More threads than remaining budget

        def try_record():
            try:
                temp_tracker.record_call(cost=0.006)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=try_record) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        info = temp_tracker.get_info()
        # All 5 writes should succeed (budget tracking doesn't prevent writes)
        assert info.calls_today == 43
        assert info.status == BudgetStatus.EXHAUSTED

    def test_concurrent_mcp_operations(self, temp_tracker):
        """Concurrent record_mcp_operation() calls should not lose data."""
        errors = []
        n_threads = 10
        operations = ["perplexity_ask", "perplexity_search", "perplexity_research"]

        def record_mcp(op):
            try:
                temp_tracker.record_mcp_operation(op)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=record_mcp, args=(operations[i % len(operations)],))
            for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        info = temp_tracker.get_info()
        assert info.calls_today == n_threads

    def test_concurrent_get_info(self, temp_tracker):
        """Concurrent get_info() reads should not interfere with writes."""
        errors = []

        def writer():
            try:
                for _ in range(5):
                    temp_tracker.record_call(cost=0.006)
            except Exception as e:
                errors.append(("writer", e))

        def reader():
            try:
                for _ in range(5):
                    info = temp_tracker.get_info()
                    assert info.calls_today >= 0
                    assert info.cost_today >= 0
            except Exception as e:
                errors.append(("reader", e))

        writer_threads = [threading.Thread(target=writer) for _ in range(3)]
        reader_threads = [threading.Thread(target=reader) for _ in range(3)]

        all_threads = writer_threads + reader_threads
        for t in all_threads:
            t.start()
        for t in all_threads:
            t.join()

        assert not errors
        info = temp_tracker.get_info()
        assert info.calls_today == 15  # 3 writers × 5 calls

    def test_concurrent_reset_and_write(self, temp_tracker):
        """reset_today() during concurrent writes should not crash."""
        errors = []

        def writer():
            try:
                for _ in range(10):
                    temp_tracker.record_call(cost=0.006)
            except Exception as e:
                errors.append(e)

        def resetter():
            try:
                temp_tracker.reset_today()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=writer),
            threading.Thread(target=resetter),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No crashes
        assert not errors
        # State should be consistent (exact count depends on timing)
        info = temp_tracker.get_info()
        assert info.calls_today >= 0

    def test_multiple_tracker_instances_same_db(self, tmp_path):
        """Multiple tracker instances on same DB should not lose writes."""
        db_path = tmp_path / "shared_concurrent.db"
        errors = []
        n_trackers = 5
        calls_per_tracker = 4

        def write_from_tracker():
            try:
                tracker = BudgetTracker(db_path=db_path)
                for _ in range(calls_per_tracker):
                    tracker.record_call(cost=0.006)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_from_tracker) for _ in range(n_trackers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        final_tracker = BudgetTracker(db_path=db_path)
        info = final_tracker.get_info()
        assert info.calls_today == n_trackers * calls_per_tracker


class TestDatabaseIntegrity:
    """Tests for database integrity under concurrent access."""

    @pytest.fixture
    def temp_tracker(self, tmp_path):
        db_path = tmp_path / "test_integrity.db"
        return BudgetTracker(db_path=db_path)

    def test_no_duplicate_date_rows(self, temp_tracker):
        """Concurrent _ensure_today_row calls should not create duplicate rows."""
        errors = []

        def ensure_and_record():
            try:
                temp_tracker.record_call(cost=0.006)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=ensure_and_record) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors

        # Verify exactly one row for today
        with sqlite3.connect(temp_tracker.db_path) as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM api_budget WHERE date = ?",
                (date.today().isoformat(),),
            )
            count = cursor.fetchone()[0]
            assert count == 1, f"Expected 1 row for today, got {count}"

    def test_consistent_call_count_and_cost(self, temp_tracker):
        """Call count and cost should be consistent after concurrent writes."""
        n_threads = 10
        cost = 0.01
        errors = []

        def record():
            try:
                temp_tracker.record_call(cost=cost)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        info = temp_tracker.get_info()

        # Cost should be calls × per-call cost
        expected_cost = info.calls_today * cost
        assert info.cost_today == pytest.approx(expected_cost, rel=0.01)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
