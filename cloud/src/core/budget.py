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


class BudgetExhausted(Exception):
    """Raised when API budget (daily calls or monthly spend) is exhausted."""

    def __init__(self, service: str, reason: str):
        self.service = service
        self.reason = reason
        super().__init__(f"Budget exhausted for {service}: {reason}")


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
                    updated_at TEXT NOT NULL,
                    UNIQUE(date, service)
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
    ) -> bool:
        """
        Record an API call.

        Args:
            service: Service name (e.g., "perplexity")
            cost: Cost of the call in dollars
            date_str: Date to record for (default: today)

        Returns:
            True if recorded successfully
        """
        if date_str is None:
            date_str = today_et()

        timestamp = now_et().isoformat()

        conn = sqlite3.connect(self.db_path)
        try:
            # Use UPSERT for atomic insert-or-update
            conn.execute("""
                INSERT INTO api_budget (date, service, calls, cost, updated_at)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(date, service) DO UPDATE SET
                    calls = calls + 1,
                    cost = cost + excluded.cost,
                    updated_at = excluded.updated_at
            """, (date_str, service, cost, timestamp))

            conn.commit()
            log("debug", "API call recorded", service=service, cost=cost)
            return True
        except sqlite3.Error as e:
            log("error", "Failed to record API call", error=str(e))
            return False
        finally:
            conn.close()

    def try_acquire_call(
        self,
        service: str = "perplexity",
        cost: float = 0.005
    ) -> bool:
        """
        Atomic check-and-increment: check limits and record call in one transaction.

        This prevents race conditions where multiple requests check limits
        simultaneously and all proceed.

        Args:
            service: Service name
            cost: Estimated cost of the call

        Returns:
            True if call was acquired (within limits), False if limits exceeded
        """
        date_str = today_et()
        month_prefix = date_str[:7]  # YYYY-MM
        timestamp = now_et().isoformat()

        # Use DEFERRED (default) with IMMEDIATE upgrade for write - better for GCS multi-instance
        max_retries = 3
        for attempt in range(max_retries):
            conn = sqlite3.connect(self.db_path, timeout=30)
            try:
                # Begin immediate transaction (write lock)
                conn.execute("BEGIN IMMEDIATE")

                # Get current stats in same transaction
                cursor = conn.execute(
                    "SELECT calls, cost FROM api_budget WHERE date = ? AND service = ?",
                    (date_str, service)
                )
                row = cursor.fetchone()
                daily_calls = row[0] if row else 0

                # Check daily limit BEFORE incrementing
                if daily_calls >= settings.PERPLEXITY_DAILY_LIMIT:
                    log("warn", "Daily API limit reached", service=service, calls=daily_calls)
                    conn.rollback()
                    return False

                # Get monthly cost
                cursor = conn.execute(
                    "SELECT SUM(cost) FROM api_budget WHERE date LIKE ? AND service = ?",
                    (f"{month_prefix}%", service)
                )
                row = cursor.fetchone()
                monthly_cost = row[0] if row and row[0] else 0.0

                # Check monthly budget BEFORE incrementing
                if monthly_cost >= settings.PERPLEXITY_MONTHLY_BUDGET:
                    log("warn", "Monthly API budget exceeded", service=service, cost=monthly_cost)
                    conn.rollback()
                    return False

                # All checks passed - atomically increment
                conn.execute("""
                    INSERT INTO api_budget (date, service, calls, cost, updated_at)
                    VALUES (?, ?, 1, ?, ?)
                    ON CONFLICT(date, service) DO UPDATE SET
                        calls = calls + 1,
                        cost = cost + excluded.cost,
                        updated_at = excluded.updated_at
                """, (date_str, service, cost, timestamp))

                conn.commit()
                log("debug", "API call acquired", service=service, daily_calls=daily_calls + 1)
                return True

            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < max_retries - 1:
                    log("warn", "Database locked, retrying", attempt=attempt + 1)
                    conn.rollback()
                    # Note: This is a sync function, so we use time.sleep
                    # For async callers, consider using try_acquire_call_async instead
                    import time
                    time.sleep(0.5 * (attempt + 1))  # Exponential backoff
                    continue
                log("error", "Failed to acquire API call", error=str(e))
                conn.rollback()
                return False
            except sqlite3.Error as e:
                log("error", "Failed to acquire API call", error=str(e))
                conn.rollback()
                return False
            finally:
                conn.close()
        return False

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

    async def try_acquire_call_async(
        self,
        service: str = "perplexity",
        cost: float = 0.005
    ) -> bool:
        """
        Async version of try_acquire_call.

        Use this from async code to avoid blocking the event loop.
        Uses asyncio.sleep for backoff instead of time.sleep.

        Args:
            service: Service name
            cost: Estimated cost of the call

        Returns:
            True if call was acquired (within limits), False if limits exceeded
        """
        import asyncio

        date_str = today_et()
        month_prefix = date_str[:7]  # YYYY-MM
        timestamp = now_et().isoformat()

        max_retries = 3
        for attempt in range(max_retries):
            conn = sqlite3.connect(self.db_path, timeout=30)
            try:
                conn.execute("BEGIN IMMEDIATE")

                cursor = conn.execute(
                    "SELECT calls, cost FROM api_budget WHERE date = ? AND service = ?",
                    (date_str, service)
                )
                row = cursor.fetchone()
                daily_calls = row[0] if row else 0

                if daily_calls >= settings.PERPLEXITY_DAILY_LIMIT:
                    log("warn", "Daily API limit reached", service=service, calls=daily_calls)
                    conn.rollback()
                    return False

                cursor = conn.execute(
                    "SELECT SUM(cost) FROM api_budget WHERE date LIKE ? AND service = ?",
                    (f"{month_prefix}%", service)
                )
                row = cursor.fetchone()
                monthly_cost = row[0] if row and row[0] else 0.0

                if monthly_cost >= settings.PERPLEXITY_MONTHLY_BUDGET:
                    log("warn", "Monthly API budget exceeded", service=service, cost=monthly_cost)
                    conn.rollback()
                    return False

                conn.execute("""
                    INSERT INTO api_budget (date, service, calls, cost, updated_at)
                    VALUES (?, ?, 1, ?, ?)
                    ON CONFLICT(date, service) DO UPDATE SET
                        calls = calls + 1,
                        cost = cost + excluded.cost,
                        updated_at = excluded.updated_at
                """, (date_str, service, cost, timestamp))

                conn.commit()
                log("debug", "API call acquired", service=service, daily_calls=daily_calls + 1)
                return True

            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < max_retries - 1:
                    log("warn", "Database locked, retrying async", attempt=attempt + 1)
                    conn.rollback()
                    await asyncio.sleep(0.5 * (attempt + 1))  # Non-blocking backoff
                    continue
                log("error", "Failed to acquire API call", error=str(e))
                conn.rollback()
                return False
            except sqlite3.Error as e:
                log("error", "Failed to acquire API call", error=str(e))
                conn.rollback()
                return False
            finally:
                conn.close()
        return False

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
