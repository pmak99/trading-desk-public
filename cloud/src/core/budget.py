"""
API budget tracker for rate-limited services.

Tracks daily calls and monthly spend against configured limits.
Prevents exceeding Perplexity's 40 calls/day, $5/month budget.
"""

import sqlite3
from datetime import datetime, date
from typing import Dict, Any, Optional

from .config import today_et, now_et, settings
from .logging import log


class BudgetTracker:
    """Track API usage against daily/monthly limits."""

    def __init__(self, db_path: str = "data/ivcrush.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize api_budget table."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_budget (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    service TEXT NOT NULL,
                    calls INTEGER DEFAULT 0,
                    cost REAL DEFAULT 0.0,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_api_budget_date_service
                ON api_budget(date, service)
            """)
            conn.commit()
        finally:
            conn.close()

    def record_call(
        self,
        service: str,
        cost: float = 0.0,
        date_str: Optional[str] = None
    ):
        """
        Record an API call.

        Args:
            service: Service name (e.g., "perplexity")
            cost: Cost of the call in dollars
            date_str: Date to record for (default: today)
        """
        if date_str is None:
            date_str = today_et()

        timestamp = now_et().isoformat()

        conn = sqlite3.connect(self.db_path)
        try:
            # Check if row exists for today
            cursor = conn.execute(
                "SELECT id, calls, cost FROM api_budget WHERE date = ? AND service = ?",
                (date_str, service)
            )
            row = cursor.fetchone()

            if row:
                # Update existing
                conn.execute("""
                    UPDATE api_budget
                    SET calls = calls + 1, cost = cost + ?, updated_at = ?
                    WHERE id = ?
                """, (cost, timestamp, row[0]))
            else:
                # Insert new
                conn.execute("""
                    INSERT INTO api_budget (date, service, calls, cost, updated_at)
                    VALUES (?, ?, 1, ?, ?)
                """, (date_str, service, cost, timestamp))

            conn.commit()
            log("debug", "API call recorded", service=service, cost=cost)
        finally:
            conn.close()

    def get_daily_stats(self, service: str, date_str: Optional[str] = None) -> Dict[str, Any]:
        """Get daily usage stats for a service."""
        if date_str is None:
            date_str = today_et()

        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT calls, cost FROM api_budget WHERE date = ? AND service = ?",
                (date_str, service)
            )
            row = cursor.fetchone()
            return {
                "calls": row[0] if row else 0,
                "cost": row[1] if row else 0.0,
                "date": date_str,
            }
        finally:
            conn.close()

    def get_monthly_cost(self, service: str) -> float:
        """Get total cost for current month."""
        month_prefix = today_et()[:7]  # YYYY-MM

        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT SUM(cost) FROM api_budget WHERE date LIKE ? AND service = ?",
                (f"{month_prefix}%", service)
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else 0.0
        finally:
            conn.close()

    def can_call(self, service: str = "perplexity") -> bool:
        """
        Check if we can make another API call.

        Returns False if:
        - Daily limit exceeded (40 calls)
        - Monthly budget exceeded ($5)
        """
        daily = self.get_daily_stats(service)
        monthly_cost = self.get_monthly_cost(service)

        # Check daily limit
        if daily["calls"] >= settings.PERPLEXITY_DAILY_LIMIT:
            log("warn", "Daily API limit reached", service=service, calls=daily["calls"])
            return False

        # Check monthly budget
        if monthly_cost >= settings.PERPLEXITY_MONTHLY_BUDGET:
            log("warn", "Monthly API budget exceeded", service=service, cost=monthly_cost)
            return False

        return True

    def get_summary(self, service: str = "perplexity") -> Dict[str, Any]:
        """Get budget summary for display."""
        daily = self.get_daily_stats(service)
        monthly_cost = self.get_monthly_cost(service)

        return {
            "today_calls": daily["calls"],
            "today_cost": round(daily["cost"], 2),
            "daily_limit": settings.PERPLEXITY_DAILY_LIMIT,
            "month_cost": round(monthly_cost, 2),
            "monthly_budget": settings.PERPLEXITY_MONTHLY_BUDGET,
            "budget_remaining": round(settings.PERPLEXITY_MONTHLY_BUDGET - monthly_cost, 2),
            "can_call": self.can_call(service),
        }
