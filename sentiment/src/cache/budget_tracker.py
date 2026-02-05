"""
Budget Tracker for Perplexity API Usage

Tracks daily API calls, tokens, and cost to stay within $5/month budget.
Daily limits (40 calls) reset automatically at midnight based on date comparison.
Monthly cost tracking resets on the 1st of each month.

Token-Based Pricing (from actual Perplexity invoice January 2025):
- sonar output: $0.000001/token (1M tokens = $1)
- sonar-pro output: $0.000015/token (1M tokens = $15)
- reasoning-pro: $0.000003/token (1M tokens = $3)
- Search API: $0.005/request (flat fee)

MCP Tool Estimates (since MCP doesn't return token counts):
- perplexity_ask: ~200 output tokens (sonar) = $0.001
- perplexity_search: 1 search request = $0.005
- perplexity_research: ~500 output tokens (sonar-pro) = $0.008
- perplexity_reason: ~4000 reasoning tokens = $0.012

Limits:
- Monthly budget: $5.00
- Max: 40 calls/day
- Warn: At 80% (32 calls)
- Hard stop: At 100% (graceful degradation to WebSearch)
"""

import os
import sqlite3
import threading
import logging
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Optional, Tuple, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

_db_lock = threading.Lock()


# Perplexity token pricing (per token, from invoice)
PRICING = {
    "sonar_output": 0.000001,      # $1/1M tokens
    "sonar_pro_output": 0.000015,  # $15/1M tokens
    "reasoning_pro": 0.000003,     # $3/1M tokens
    "search_request": 0.005,       # $5/1000 requests (flat)
}

# MCP operation cost estimates (for operations without token counts)
# NOTE: These are empirical estimates based on typical response sizes observed
# in production (January-February 2025). MCP tools don't return token counts,
# so we estimate based on:
# - perplexity_ask: Simple Q&A responses averaging ~200 output tokens
# - perplexity_search: Fixed $0.005/request per Perplexity API pricing
# - perplexity_research: Detailed analysis averaging ~500 tokens (sonar-pro model)
# - perplexity_reason: Extended reasoning averaging ~4000 tokens
#
# ACCURACY: These estimates may vary ±50% from actual costs. Monitor monthly
# invoice against budget tracker totals to adjust if needed.
MCP_COST_ESTIMATES = {
    "perplexity_ask": 0.001,      # ~200 sonar output tokens @ $1/1M
    "perplexity_search": 0.005,   # 1 search request (fixed fee)
    "perplexity_research": 0.008, # ~500 sonar-pro output tokens @ $15/1M
    "perplexity_reason": 0.012,   # ~4000 reasoning tokens @ $3/1M
}

# Token count bounds (sanity check to catch bugs)
MAX_TOKENS_PER_CALL = 10_000_000  # 10M tokens max per call (very generous limit)


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
    # Token breakdown (optional, may be 0 for legacy data)
    output_tokens: int = 0
    reasoning_tokens: int = 0
    search_requests: int = 0

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
    MAX_DAILY_CALLS = 40   # ~40 calls/day with sonar model (~$0.006/call)
    WARN_THRESHOLD = 0.80  # 80% = 32 calls
    COST_PER_CALL_ESTIMATE = 0.006  # ~$0.006 per sonar call (includes $0.005 request fee)

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize tracker with optional custom database path.

        Path resolution order:
        1. Explicit db_path argument
        2. SENTIMENT_DB_PATH environment variable
        3. Default: <4.0>/data/sentiment_cache.db (relative to module location)
        """
        if db_path is None:
            env_path = os.environ.get("SENTIMENT_DB_PATH")
            if env_path:
                db_path = Path(env_path)
            else:
                db_path = Path(__file__).parent.parent.parent / "data" / "sentiment_cache.db"

        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database schema with token tracking columns."""
        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS api_budget (
                        date TEXT PRIMARY KEY,
                        calls INTEGER DEFAULT 0,
                        cost REAL DEFAULT 0.0,
                        last_updated TEXT,
                        output_tokens INTEGER DEFAULT 0,
                        reasoning_tokens INTEGER DEFAULT 0,
                        search_requests INTEGER DEFAULT 0
                    )
                """)
                # Add token columns if they don't exist (migration for existing DBs)
                # Use a whitelist of allowed column definitions to avoid SQL injection.
                # DDL statements (ALTER TABLE) cannot use parameterized queries for
                # column names/types, so we validate against an explicit whitelist.
                _ALLOWED_MIGRATIONS = {
                    "output_tokens": "ALTER TABLE api_budget ADD COLUMN output_tokens INTEGER DEFAULT 0",
                    "reasoning_tokens": "ALTER TABLE api_budget ADD COLUMN reasoning_tokens INTEGER DEFAULT 0",
                    "search_requests": "ALTER TABLE api_budget ADD COLUMN search_requests INTEGER DEFAULT 0",
                }
                for column, sql in _ALLOWED_MIGRATIONS.items():
                    try:
                        conn.execute(sql)
                    except sqlite3.OperationalError as e:
                        # Only ignore "duplicate column" errors, re-raise others
                        if "duplicate column" not in str(e).lower():
                            logger.error(f"Failed to add column {column}: {e}")
                            raise
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
        cost: float = None,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
        search_requests: int = 0
    ) -> None:
        """
        Record an API call with optional token breakdown.

        Args:
            cost: Actual cost if known, otherwise uses estimate
            output_tokens: Number of output tokens (sonar/sonar-pro)
            reasoning_tokens: Number of reasoning tokens (reasoning-pro)
            search_requests: Number of search API requests

        Raises:
            ValueError: If cost is negative or not a finite number
            ValueError: If token counts are negative or exceed bounds
        """
        if cost is None:
            cost = self.COST_PER_CALL_ESTIMATE
        else:
            # Validate cost is a reasonable positive number
            import math
            if not isinstance(cost, (int, float)) or math.isnan(cost) or math.isinf(cost):
                raise ValueError(f"Cost must be a finite number, got: {cost}")
            if cost < 0:
                raise ValueError(f"Cost cannot be negative, got: {cost}")

        # Validate token counts
        self._validate_token_counts(output_tokens, reasoning_tokens, search_requests)

        today = self._get_today()

        try:
            with _db_lock:
                with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                    self._ensure_today_row(conn)
                    conn.execute("""
                        UPDATE api_budget
                        SET calls = calls + 1,
                            cost = cost + ?,
                            output_tokens = output_tokens + ?,
                            reasoning_tokens = reasoning_tokens + ?,
                            search_requests = search_requests + ?,
                            last_updated = ?
                        WHERE date = ?
                    """, (cost, output_tokens, reasoning_tokens, search_requests,
                          datetime.now(timezone.utc).isoformat(), today))
                    conn.commit()
        except sqlite3.OperationalError as e:
            logger.error(f"Budget DB operational error in record_call: {e}")
        except sqlite3.Error as e:
            logger.error(f"Budget DB error in record_call: {e}")

    def record_tokens(
        self,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
        search_requests: int = 0,
        model: str = "sonar"
    ) -> float:
        """
        Record API usage by token count and calculate actual cost.

        This is the preferred method when token data is available (REST API calls).
        Cost is calculated from actual token counts using invoice-verified rates.

        Args:
            output_tokens: Number of output tokens
            reasoning_tokens: Number of reasoning tokens (from reasoning-pro model)
            search_requests: Number of search API requests
            model: Model used ("sonar", "sonar-pro", or "reasoning-pro")

        Returns:
            Calculated cost in dollars
        """
        # Validate token counts before calculation
        self._validate_token_counts(output_tokens, reasoning_tokens, search_requests)

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

        # Validate calculated cost is non-negative
        if cost < 0:
            raise ValueError(f"Calculated cost cannot be negative, got: {cost}")

        if cost > 10.0:  # $10 per call would be anomalous
            logger.warning(f"Unusually high cost calculated: ${cost:.4f} for {output_tokens} output tokens")

        # Record with token breakdown
        self.record_call(
            cost=cost,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            search_requests=search_requests
        )

        return cost

    def record_mcp_operation(self, operation: str) -> float:
        """
        Record an MCP tool operation using estimated costs.

        Use this when token data is not available (MCP tool calls).

        Args:
            operation: MCP operation name (e.g., "perplexity_ask", "perplexity_search")

        Returns:
            Estimated cost in dollars
        """
        cost = MCP_COST_ESTIMATES.get(operation, self.COST_PER_CALL_ESTIMATE)
        self.record_call(cost=cost)
        return cost

    def get_info(self) -> BudgetInfo:
        """Get current budget information including token breakdown."""
        today = self._get_today()

        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                conn.row_factory = sqlite3.Row
                self._ensure_today_row(conn)

                row = conn.execute("""
                    SELECT date, calls, cost, output_tokens, reasoning_tokens, search_requests
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
                    status=status,
                    output_tokens=row['output_tokens'] or 0,
                    reasoning_tokens=row['reasoning_tokens'] or 0,
                    search_requests=row['search_requests'] or 0
                )

    def get_monthly_summary(self) -> dict:
        """Get monthly usage summary including token breakdown."""
        today = date.today()
        month_start = today.replace(day=1).isoformat()

        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                conn.row_factory = sqlite3.Row

                row = conn.execute("""
                    SELECT
                        COUNT(*) as days_with_usage,
                        SUM(calls) as total_calls,
                        SUM(cost) as total_cost,
                        AVG(calls) as avg_calls_per_day,
                        MAX(calls) as max_calls_day,
                        SUM(output_tokens) as total_output_tokens,
                        SUM(reasoning_tokens) as total_reasoning_tokens,
                        SUM(search_requests) as total_search_requests
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
                    "budget_remaining": 5.00 - (row['total_cost'] or 0.0),
                    # Token breakdown
                    "total_output_tokens": row['total_output_tokens'] or 0,
                    "total_reasoning_tokens": row['total_reasoning_tokens'] or 0,
                    "total_search_requests": row['total_search_requests'] or 0
                }

    def reset_today(self) -> None:
        """Reset today's counts (for testing)."""
        today = self._get_today()

        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                conn.execute("""
                    UPDATE api_budget
                    SET calls = 0, cost = 0.0,
                        output_tokens = 0, reasoning_tokens = 0, search_requests = 0,
                        last_updated = ?
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


def record_perplexity_call(cost: float = 0.006) -> None:
    """Quick helper to record a Perplexity API call.

    Default cost matches COST_PER_CALL_ESTIMATE in BudgetTracker.
    """
    tracker = BudgetTracker()
    tracker.record_call(cost)


def get_budget_status() -> str:
    """Get formatted budget status for display."""
    tracker = BudgetTracker()
    info = tracker.get_info()
    monthly = tracker.get_monthly_summary()

    # Base status
    status = f"""Budget Status:
  Today: {info.calls_today}/{tracker.MAX_DAILY_CALLS} calls (${info.cost_today:.4f})
  Month: {monthly['total_calls']} calls (${monthly['total_cost']:.4f} of $5.00)
  Status: {info.status.value.upper()}"""

    # Add token breakdown if any tokens tracked
    total_tokens = (monthly['total_output_tokens'] +
                    monthly['total_reasoning_tokens'])
    if total_tokens > 0:
        status += f"""
  Tokens: {monthly['total_output_tokens']:,} output, {monthly['total_reasoning_tokens']:,} reasoning
  Searches: {monthly['total_search_requests']}"""

    return status
