# 5.0/tests/test_budget.py
import pytest
import sqlite3
import tempfile
import os
from datetime import date
from src.core.budget import BudgetTracker

@pytest.fixture
def tracker():
    """Create tracker with temp database."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield BudgetTracker(db_path=db_path)
    os.unlink(db_path)

def test_record_api_call(tracker):
    """record_call increments daily count."""
    tracker.record_call("perplexity", cost=0.005)
    stats = tracker.get_daily_stats("perplexity")
    assert stats["calls"] == 1
    assert stats["cost"] == 0.005

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
