"""
API budget tracker for rate-limited services.

Tracks daily calls, tokens, and monthly spend against configured limits.
Prevents exceeding Perplexity's 40 calls/day, $5/month budget.

Token-Based Pricing (from actual Perplexity invoice January 2025):
- sonar output: $0.000001/token (1M tokens = $1)
- sonar-pro output: $0.000015/token (1M tokens = $15)
- reasoning-pro: $0.000003/token (1M tokens = $3)
- Search API: $0.005/request (flat fee)
"""

import sqlite3
from datetime import datetime, date
from typing import Dict, Any, Optional

from .config import today_et, now_et, settings
from .logging import log


# Perplexity token pricing (per token, from invoice)
PRICING = {
    "sonar_output": 0.000001,      # $1/1M tokens
    "sonar_pro_output": 0.000015,  # $15/1M tokens
    "reasoning_pro": 0.000003,     # $3/1M tokens
    "search_request": 0.005,       # $5/1000 requests (flat)
}

# Default cost estimate when token data unavailable
DEFAULT_COST_ESTIMATE = 0.006

# Token count bounds (sanity check to catch bugs)
MAX_TOKENS_PER_CALL = 10_000_000  # 10M tokens max per call (very generous limit)


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
        """Initialize api_budget table with token tracking columns."""
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
                    output_tokens INTEGER DEFAULT 0,
                    reasoning_tokens INTEGER DEFAULT 0,
                    search_requests INTEGER DEFAULT 0,
                    UNIQUE(date, service)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_api_budget_date_service
                ON api_budget(date, service)
            """)
            # Add token columns if they don't exist (migration for existing DBs)
            for column in ['output_tokens', 'reasoning_tokens', 'search_requests']:
                try:
                    conn.execute(f"ALTER TABLE api_budget ADD COLUMN {column} INTEGER DEFAULT 0")
                except sqlite3.OperationalError:
                    pass  # Column already exists
            conn.commit()
        finally:
            conn.close()

    def _validate_token_counts(
        self,
        output_tokens: int,
        reasoning_tokens: int,
        search_requests: int
    ) -> None:
        """
        Validate token counts are within reasonable bounds.

        Args:
            output_tokens: Number of output tokens
            reasoning_tokens: Number of reasoning tokens
            search_requests: Number of search requests

        Raises:
            ValueError: If any count is negative or exceeds MAX_TOKENS_PER_CALL
        """
        for name, value in [
            ("output_tokens", output_tokens),
            ("reasoning_tokens", reasoning_tokens),
            ("search_requests", search_requests),
        ]:
            if not isinstance(value, int):
                raise ValueError(f"{name} must be an integer, got: {type(value).__name__}")
            if value < 0:
                raise ValueError(f"{name} cannot be negative, got: {value}")
            if value > MAX_TOKENS_PER_CALL:
                raise ValueError(f"{name} exceeds maximum ({MAX_TOKENS_PER_CALL}), got: {value}")

    def record_call(
        self,
        service: str,
        cost: float = 0.0,
        date_str: Optional[str] = None,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
        search_requests: int = 0
    ) -> bool:
        """
        Record an API call with optional token breakdown.

        Args:
            service: Service name (e.g., "perplexity")
            cost: Cost of the call in dollars
            date_str: Date to record for (default: today)
            output_tokens: Number of output tokens (sonar/sonar-pro)
            reasoning_tokens: Number of reasoning tokens (reasoning-pro)
            search_requests: Number of search API requests

        Returns:
            True if recorded successfully

        Raises:
            ValueError: If cost is negative or not a finite number
            ValueError: If token counts are negative or exceed bounds
        """
        # Validate cost is a reasonable positive number
        import math
        if not isinstance(cost, (int, float)) or math.isnan(cost) or math.isinf(cost):
            raise ValueError(f"Cost must be a finite number, got: {cost}")
        if cost < 0:
            raise ValueError(f"Cost cannot be negative, got: {cost}")

        # Validate token counts
        self._validate_token_counts(output_tokens, reasoning_tokens, search_requests)

        if date_str is None:
            date_str = today_et()

        timestamp = now_et().isoformat()

        conn = sqlite3.connect(self.db_path)
        try:
            # Use UPSERT for atomic insert-or-update
            conn.execute("""
                INSERT INTO api_budget (date, service, calls, cost, updated_at,
                                        output_tokens, reasoning_tokens, search_requests)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(date, service) DO UPDATE SET
                    calls = calls + 1,
                    cost = cost + excluded.cost,
                    output_tokens = output_tokens + excluded.output_tokens,
                    reasoning_tokens = reasoning_tokens + excluded.reasoning_tokens,
                    search_requests = search_requests + excluded.search_requests,
                    updated_at = excluded.updated_at
            """, (date_str, service, cost, timestamp, output_tokens, reasoning_tokens, search_requests))

            conn.commit()
            log("debug", "API call recorded", service=service, cost=cost,
                output_tokens=output_tokens, reasoning_tokens=reasoning_tokens)
            return True
        except sqlite3.Error as e:
            log("error", "Failed to record API call", error=str(e))
            return False
        finally:
            conn.close()

    def record_tokens(
        self,
        service: str = "perplexity",
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
        search_requests: int = 0,
        model: str = "sonar"
    ) -> float:
        """
        Record API usage by token count and calculate actual cost.

        This is the preferred method when token data is available.
        Cost is calculated from actual token counts using invoice-verified rates.

        Args:
            service: Service name
            output_tokens: Number of output tokens
            reasoning_tokens: Number of reasoning tokens (from reasoning-pro model)
            search_requests: Number of search API requests
            model: Model used ("sonar", "sonar-pro", or "reasoning-pro")

        Returns:
            Calculated cost in dollars
        """
        # Calculate cost from tokens
        cost = 0.0
        if output_tokens > 0:
            if model == "sonar-pro":
                cost += output_tokens * PRICING["sonar_pro_output"]
            else:
                cost += output_tokens * PRICING["sonar_output"]
        if reasoning_tokens > 0:
            cost += reasoning_tokens * PRICING["reasoning_pro"]
        if search_requests > 0:
            cost += search_requests * PRICING["search_request"]

        # Record with token breakdown
        self.record_call(
            service=service,
            cost=cost,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            search_requests=search_requests
        )

        log("debug", "Token usage recorded",
            service=service, model=model, cost=cost,
            output_tokens=output_tokens, reasoning_tokens=reasoning_tokens)

        return cost

    def try_acquire_call(
        self,
        service: str = "perplexity",
        cost: float = 0.006,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
        search_requests: int = 0
    ) -> bool:
        """
        Atomic check-and-increment: check limits and record call in one transaction.

        This prevents race conditions where multiple requests check limits
        simultaneously and all proceed.

        Args:
            service: Service name
            cost: Estimated cost of the call
            output_tokens: Number of output tokens (sonar/sonar-pro)
            reasoning_tokens: Number of reasoning tokens (reasoning-pro)
            search_requests: Number of search API requests

        Returns:
            True if call was acquired (within limits), False if limits exceeded

        Raises:
            ValueError: If token counts are negative or exceed bounds
        """
        # Validate token counts before any database operations
        self._validate_token_counts(output_tokens, reasoning_tokens, search_requests)

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
                    INSERT INTO api_budget (date, service, calls, cost, updated_at,
                                            output_tokens, reasoning_tokens, search_requests)
                    VALUES (?, ?, 1, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, service) DO UPDATE SET
                        calls = calls + 1,
                        cost = cost + excluded.cost,
                        output_tokens = output_tokens + excluded.output_tokens,
                        reasoning_tokens = reasoning_tokens + excluded.reasoning_tokens,
                        search_requests = search_requests + excluded.search_requests,
                        updated_at = excluded.updated_at
                """, (date_str, service, cost, timestamp, output_tokens, reasoning_tokens, search_requests))

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
        # All retries exhausted without success
        log("error", "API call acquire failed after all retries", service=service, retries=max_retries)
        return False

    def get_daily_stats(self, service: str, date_str: Optional[str] = None) -> Dict[str, Any]:
        """Get daily usage stats for a service including token breakdown."""
        if date_str is None:
            date_str = today_et()

        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """SELECT calls, cost, output_tokens, reasoning_tokens, search_requests
                   FROM api_budget WHERE date = ? AND service = ?""",
                (date_str, service)
            )
            row = cursor.fetchone()
            return {
                "calls": row[0] if row else 0,
                "cost": row[1] if row else 0.0,
                "date": date_str,
                "output_tokens": row[2] if row else 0,
                "reasoning_tokens": row[3] if row else 0,
                "search_requests": row[4] if row else 0,
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
        cost: float = 0.006,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
        search_requests: int = 0
    ) -> bool:
        """
        Async version of try_acquire_call.

        Use this from async code to avoid blocking the event loop.
        Uses asyncio.sleep for backoff instead of time.sleep.

        Args:
            service: Service name
            cost: Estimated cost of the call
            output_tokens: Number of output tokens (sonar/sonar-pro)
            reasoning_tokens: Number of reasoning tokens (reasoning-pro)
            search_requests: Number of search API requests

        Returns:
            True if call was acquired (within limits), False if limits exceeded

        Raises:
            ValueError: If token counts are negative or exceed bounds
        """
        import asyncio

        # Validate token counts before any database operations
        self._validate_token_counts(output_tokens, reasoning_tokens, search_requests)

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
                    INSERT INTO api_budget (date, service, calls, cost, updated_at,
                                            output_tokens, reasoning_tokens, search_requests)
                    VALUES (?, ?, 1, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, service) DO UPDATE SET
                        calls = calls + 1,
                        cost = cost + excluded.cost,
                        output_tokens = output_tokens + excluded.output_tokens,
                        reasoning_tokens = reasoning_tokens + excluded.reasoning_tokens,
                        search_requests = search_requests + excluded.search_requests,
                        updated_at = excluded.updated_at
                """, (date_str, service, cost, timestamp, output_tokens, reasoning_tokens, search_requests))

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
        # All retries exhausted without success
        log("error", "API call acquire failed after all retries (async)", service=service, retries=max_retries)
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
        """Get budget summary for display including token breakdown."""
        daily = self.get_daily_stats(service)
        monthly_cost = self.get_monthly_cost(service)
        monthly_tokens = self.get_monthly_tokens(service)

        return {
            "today_calls": daily["calls"],
            "today_cost": round(daily["cost"], 4),
            "daily_limit": settings.PERPLEXITY_DAILY_LIMIT,
            "month_cost": round(monthly_cost, 4),
            "monthly_budget": settings.PERPLEXITY_MONTHLY_BUDGET,
            "budget_remaining": round(settings.PERPLEXITY_MONTHLY_BUDGET - monthly_cost, 4),
            "can_call": self.can_call(service),
            # Token breakdown
            "today_output_tokens": daily.get("output_tokens", 0),
            "today_reasoning_tokens": daily.get("reasoning_tokens", 0),
            "today_search_requests": daily.get("search_requests", 0),
            "month_output_tokens": monthly_tokens.get("output_tokens", 0),
            "month_reasoning_tokens": monthly_tokens.get("reasoning_tokens", 0),
            "month_search_requests": monthly_tokens.get("search_requests", 0),
        }

    def get_monthly_tokens(self, service: str) -> Dict[str, int]:
        """Get total token counts for current month."""
        month_prefix = today_et()[:7]  # YYYY-MM

        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                """SELECT SUM(output_tokens), SUM(reasoning_tokens), SUM(search_requests)
                   FROM api_budget WHERE date LIKE ? AND service = ?""",
                (f"{month_prefix}%", service)
            )
            row = cursor.fetchone()
            return {
                "output_tokens": row[0] if row and row[0] else 0,
                "reasoning_tokens": row[1] if row and row[1] else 0,
                "search_requests": row[2] if row and row[2] else 0,
            }
        finally:
            conn.close()
