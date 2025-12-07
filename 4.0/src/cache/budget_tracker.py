"""
Budget Tracker for Perplexity API Usage

Tracks daily API calls and cost to stay within $5/month budget.
Budget resets on the 1st of each month.
Daily limits reset automatically based on date comparison (no cron needed).

Perplexity Pricing (sonar model - configured via PERPLEXITY_ASK_MODEL=sonar):
- Input: $1/1M tokens
- Output: $1/1M tokens
- Typical query (~150 input, ~400 output): ~$0.00055
- Estimated cost per call: ~$0.001 (conservative, includes overhead)

Limits:
- Monthly budget: $5.00
- Max: 200 calls/day (~$0.20/day, ~$4.40/month at 22 trading days)
- Warn: At 80% (160 calls)
- Hard stop: At 100% (graceful degradation to WebSearch)

Note: Using sonar (basic) instead of sonar-pro for 10x cost savings.
Quality is sufficient for sentiment synthesis queries.
"""

import sqlite3
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class BudgetStatus(Enum):
    """Budget status levels."""
    OK = "ok"           # Under 80%
    WARNING = "warning"  # 80-99%
    EXHAUSTED = "exhausted"  # 100%+


@dataclass
class BudgetInfo:
    """Current budget information."""
    date: str
    calls_today: int
    cost_today: float
    calls_remaining: int
    status: BudgetStatus

    @property
    def usage_percent(self) -> float:
        """Percentage of daily budget used."""
        return (self.calls_today / BudgetTracker.MAX_DAILY_CALLS) * 100


class BudgetTracker:
    """
    SQLite-backed daily budget tracker for Perplexity API.

    Usage:
        tracker = BudgetTracker()

        # Before making API call
        if tracker.can_call():
            # Make Perplexity call...
            tracker.record_call(cost=0.01)
        else:
            # Fall back to WebSearch
            pass

        # Check status
        info = tracker.get_info()
        print(f"Calls today: {info.calls_today}/{tracker.MAX_DAILY_CALLS}")
    """

    # Budget constants
    MONTHLY_BUDGET = 5.00  # $5/month budget
    MAX_DAILY_CALLS = 200  # ~200 calls/day with sonar model (~$0.001/call)
    WARN_THRESHOLD = 0.80  # 80% = 160 calls
    COST_PER_CALL_ESTIMATE = 0.001  # ~$0.001 per sonar call (conservative)

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize tracker with optional custom database path."""
        if db_path is None:
            db_path = Path(__file__).parent.parent.parent / "data" / "sentiment_cache.db"

        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_budget (
                    date TEXT PRIMARY KEY,
                    calls INTEGER DEFAULT 0,
                    cost REAL DEFAULT 0.0,
                    last_updated TEXT
                )
            """)
            conn.commit()

    def _get_today(self) -> str:
        """Get today's date as string."""
        return date.today().isoformat()

    def _ensure_today_row(self, conn: sqlite3.Connection) -> None:
        """Ensure a row exists for today (handles date rollover)."""
        today = self._get_today()
        conn.execute("""
            INSERT OR IGNORE INTO api_budget (date, calls, cost, last_updated)
            VALUES (?, 0, 0.0, ?)
        """, (today, datetime.now(timezone.utc).isoformat()))

    def can_call(self) -> bool:
        """Check if we can make another API call today."""
        info = self.get_info()
        return info.status != BudgetStatus.EXHAUSTED

    def should_warn(self) -> bool:
        """Check if we should warn user about budget."""
        info = self.get_info()
        return info.status == BudgetStatus.WARNING

    def record_call(self, cost: float = None) -> None:
        """
        Record an API call.

        Args:
            cost: Actual cost if known, otherwise uses estimate
        """
        if cost is None:
            cost = self.COST_PER_CALL_ESTIMATE

        today = self._get_today()

        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            self._ensure_today_row(conn)
            conn.execute("""
                UPDATE api_budget
                SET calls = calls + 1,
                    cost = cost + ?,
                    last_updated = ?
                WHERE date = ?
            """, (cost, datetime.now(timezone.utc).isoformat(), today))
            conn.commit()

    def get_info(self) -> BudgetInfo:
        """Get current budget information."""
        today = self._get_today()

        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row
            self._ensure_today_row(conn)

            row = conn.execute("""
                SELECT date, calls, cost
                FROM api_budget
                WHERE date = ?
            """, (today,)).fetchone()

            calls = row['calls']
            cost = row['cost']
            remaining = max(0, self.MAX_DAILY_CALLS - calls)

            # Determine status
            if calls >= self.MAX_DAILY_CALLS:
                status = BudgetStatus.EXHAUSTED
            elif calls >= int(self.MAX_DAILY_CALLS * self.WARN_THRESHOLD):
                status = BudgetStatus.WARNING
            else:
                status = BudgetStatus.OK

            return BudgetInfo(
                date=today,
                calls_today=calls,
                cost_today=cost,
                calls_remaining=remaining,
                status=status
            )

    def get_monthly_summary(self) -> dict:
        """Get monthly usage summary."""
        today = date.today()
        month_start = today.replace(day=1).isoformat()

        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            conn.row_factory = sqlite3.Row

            row = conn.execute("""
                SELECT
                    COUNT(*) as days_with_usage,
                    SUM(calls) as total_calls,
                    SUM(cost) as total_cost,
                    AVG(calls) as avg_calls_per_day,
                    MAX(calls) as max_calls_day
                FROM api_budget
                WHERE date >= ?
            """, (month_start,)).fetchone()

            return {
                "month": today.strftime("%Y-%m"),
                "days_with_usage": row['days_with_usage'] or 0,
                "total_calls": row['total_calls'] or 0,
                "total_cost": row['total_cost'] or 0.0,
                "avg_calls_per_day": row['avg_calls_per_day'] or 0.0,
                "max_calls_day": row['max_calls_day'] or 0,
                "budget_remaining": 5.00 - (row['total_cost'] or 0.0)
            }

    def reset_today(self) -> None:
        """Reset today's counts (for testing)."""
        today = self._get_today()

        with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
            conn.execute("""
                UPDATE api_budget
                SET calls = 0, cost = 0.0, last_updated = ?
                WHERE date = ?
            """, (datetime.now(timezone.utc).isoformat(), today))
            conn.commit()


# Convenience functions for slash commands
def check_budget() -> Tuple[bool, str]:
    """
    Quick helper to check if Perplexity call is allowed.

    Returns:
        Tuple of (can_call: bool, message: str)
    """
    tracker = BudgetTracker()
    info = tracker.get_info()

    if info.status == BudgetStatus.EXHAUSTED:
        return False, f"Daily budget exhausted ({info.calls_today}/{tracker.MAX_DAILY_CALLS} calls). Using WebSearch fallback."

    if info.status == BudgetStatus.WARNING:
        return True, f"Warning: {info.usage_percent:.0f}% of daily budget used ({info.calls_today}/{tracker.MAX_DAILY_CALLS} calls)."

    return True, f"Budget OK: {info.calls_remaining} calls remaining today."


def record_perplexity_call(cost: float = 0.01) -> None:
    """Quick helper to record a Perplexity API call."""
    tracker = BudgetTracker()
    tracker.record_call(cost)


def get_budget_status() -> str:
    """Get formatted budget status for display."""
    tracker = BudgetTracker()
    info = tracker.get_info()
    monthly = tracker.get_monthly_summary()

    return f"""Budget Status:
  Today: {info.calls_today}/{tracker.MAX_DAILY_CALLS} calls (${info.cost_today:.2f})
  Month: {monthly['total_calls']} calls (${monthly['total_cost']:.2f} of $5.00)
  Status: {info.status.value.upper()}"""
