"""
SQLite-based usage tracker with cost controls and budget management.
Replaces JSON file with fcntl locking for better concurrent performance.

Tracks API calls, token usage, and enforces budget limits.
Uses SQLite WAL mode for concurrent read/write access (no file lock bottleneck).
"""

import os
import yaml
import json
from datetime import datetime, date
from typing import Dict, Optional, Tuple
from pathlib import Path
import logging

from src.core.sqlite_base import SQLiteBase

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """Raised when budget is exceeded and no fallback available."""
    pass


class UsageTrackerSQLite(SQLiteBase):
    """
    Thread-safe AND process-safe usage tracker using SQLite with WAL mode.

    Advantages over JSON + fcntl:
    - Concurrent reads and writes (no serialization bottleneck)
    - ACID transactions (no race conditions)
    - Better performance for multiprocessing
    - Automatic indexing and querying
    """

    def __init__(self, config_path: str = "config/budget.yaml", db_path: str = "data/usage.db"):
        """
        Initialize usage tracker with SQLite backend.

        Args:
            config_path: Path to budget configuration file
            db_path: Path to SQLite database file
        """
        # Initialize base class (handles connection management)
        super().__init__(db_path)

        # Load configuration
        self.config_path = config_path
        self.config = self._load_config()

        # Initialize database schema
        self._init_database()

        # Migrate from JSON if exists
        self._migrate_from_json_if_needed()

    def _load_config(self) -> Dict:
        """Load budget configuration from YAML file."""
        try:
            with open(self.config_path, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            logger.error(f"Config file not found: {self.config_path}")
            raise
        except yaml.YAMLError as e:
            logger.error(f"Error parsing config: {e}")
            raise

    def _init_database(self):
        """Initialize SQLite database schema."""
        conn = self._get_connection()

        # Create tables
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS usage_summary (
                month TEXT PRIMARY KEY,
                total_cost REAL DEFAULT 0.0,
                total_calls INTEGER DEFAULT 0,
                perplexity_cost REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS daily_usage (
                date TEXT PRIMARY KEY,
                calls INTEGER DEFAULT 0,
                cost REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS model_usage (
                model TEXT PRIMARY KEY,
                calls INTEGER DEFAULT 0,
                tokens INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cost REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS provider_usage (
                provider TEXT PRIMARY KEY,
                calls INTEGER DEFAULT 0,
                cost REAL DEFAULT 0.0
            );

            CREATE TABLE IF NOT EXISTS daily_model_usage (
                date TEXT NOT NULL,
                model TEXT NOT NULL,
                calls INTEGER DEFAULT 0,
                PRIMARY KEY (date, model)
            );

            CREATE TABLE IF NOT EXISTS api_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model TEXT NOT NULL,
                tokens INTEGER NOT NULL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cost REAL NOT NULL,
                ticker TEXT,
                success INTEGER NOT NULL,
                date TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_calls_date ON api_calls(date);
            CREATE INDEX IF NOT EXISTS idx_calls_model ON api_calls(model);
            CREATE INDEX IF NOT EXISTS idx_calls_timestamp ON api_calls(timestamp);
        """)

        # Initialize current month if not exists
        current_month = self._current_month()
        conn.execute(
            "INSERT OR IGNORE INTO usage_summary (month) VALUES (?)",
            (current_month,)
        )
        conn.commit()

        # Migrate schema if needed (add new columns to existing tables)
        self._migrate_schema()

    def _migrate_schema(self):
        """Add new columns to existing tables if they don't exist."""
        conn = self._get_connection()

        try:
            # Check if input_tokens column exists in api_calls
            cursor = conn.execute("PRAGMA table_info(api_calls)")
            columns = [row['name'] for row in cursor.fetchall()]

            if 'input_tokens' not in columns:
                logger.info("Migrating api_calls schema: adding input_tokens and output_tokens columns")
                conn.execute("ALTER TABLE api_calls ADD COLUMN input_tokens INTEGER DEFAULT 0")
                conn.execute("ALTER TABLE api_calls ADD COLUMN output_tokens INTEGER DEFAULT 0")
                conn.commit()

            # Check if input_tokens column exists in model_usage
            cursor = conn.execute("PRAGMA table_info(model_usage)")
            columns = [row['name'] for row in cursor.fetchall()]

            if 'input_tokens' not in columns:
                logger.info("Migrating model_usage schema: adding input_tokens and output_tokens columns")
                conn.execute("ALTER TABLE model_usage ADD COLUMN input_tokens INTEGER DEFAULT 0")
                conn.execute("ALTER TABLE model_usage ADD COLUMN output_tokens INTEGER DEFAULT 0")
                conn.commit()

        except Exception as e:
            logger.error(f"Schema migration error: {e}")
            # Don't raise - schema might already be correct

    def _migrate_from_json_if_needed(self):
        """Migrate data from old JSON file if it exists and DB is empty."""
        old_json_path = Path(self.config.get('logging', {}).get('log_file', 'data/usage.json'))

        if not old_json_path.exists():
            return

        conn = self._get_connection()

        # Check if DB is empty
        cursor = conn.execute("SELECT COUNT(*) as count FROM api_calls")
        if cursor.fetchone()['count'] > 0:
            logger.debug("Database already has data, skipping JSON migration")
            return

        logger.info(f"Migrating data from {old_json_path} to SQLite...")

        try:
            with open(old_json_path, 'r') as f:
                json_data = json.load(f)

            # Migrate summary data
            if json_data.get('month') == self._current_month():
                conn.execute(
                    """UPDATE usage_summary
                       SET total_cost = ?, total_calls = ?, perplexity_cost = ?
                       WHERE month = ?""",
                    (
                        json_data.get('total_cost', 0.0),
                        json_data.get('total_calls', 0),
                        json_data.get('perplexity_cost', 0.0),
                        self._current_month()
                    )
                )

            # Migrate daily usage
            for date_str, data in json_data.get('daily_usage', {}).items():
                conn.execute(
                    "INSERT OR REPLACE INTO daily_usage (date, calls, cost) VALUES (?, ?, ?)",
                    (date_str, data.get('calls', 0), data.get('cost', 0.0))
                )

                # Migrate daily model usage
                for model, calls in data.get('model_calls', {}).items():
                    conn.execute(
                        "INSERT OR REPLACE INTO daily_model_usage (date, model, calls) VALUES (?, ?, ?)",
                        (date_str, model, calls)
                    )

            # Migrate model usage
            for model, data in json_data.get('model_usage', {}).items():
                conn.execute(
                    "INSERT OR REPLACE INTO model_usage (model, calls, tokens, cost) VALUES (?, ?, ?, ?)",
                    (model, data.get('calls', 0), data.get('tokens', 0), data.get('cost', 0.0))
                )

            # Migrate provider usage
            for provider, data in json_data.get('provider_usage', {}).items():
                conn.execute(
                    "INSERT OR REPLACE INTO provider_usage (provider, calls, cost) VALUES (?, ?, ?)",
                    (provider, data.get('calls', 0), data.get('cost', 0.0))
                )

            # Migrate call records (limit to recent calls to avoid huge DB)
            for call in json_data.get('calls', [])[-10000:]:  # Keep last 10k calls
                conn.execute(
                    """INSERT INTO api_calls (timestamp, model, tokens, cost, ticker, success, date)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        call.get('timestamp', datetime.now().isoformat()),
                        call.get('model', 'unknown'),
                        call.get('tokens', 0),
                        call.get('cost', 0.0),
                        call.get('ticker'),
                        1 if call.get('success', True) else 0,
                        call.get('timestamp', datetime.now().isoformat())[:10]
                    )
                )

            conn.commit()
            logger.info(f"✓ Successfully migrated {len(json_data.get('calls', []))} calls from JSON to SQLite")

            # Rename old file to .migrated
            old_json_path.rename(old_json_path.with_suffix('.json.migrated'))

        except Exception as e:
            logger.warning(f"Failed to migrate from JSON: {e}")
            conn.rollback()

    def _current_month(self) -> str:
        """Get current month as YYYY-MM string."""
        return datetime.now().strftime('%Y-%m')

    def _current_date(self) -> str:
        """Get current date as YYYY-MM-DD string."""
        return datetime.now().strftime('%Y-%m-%d')

    def _get_model_provider(self, model: str) -> str:
        """Get provider for a model."""
        model_configs = self.config.get('models', {})
        model_config = model_configs.get(model, {})
        return model_config.get('provider', 'perplexity')

    def _reset_if_new_month(self):
        """Reset usage data if it's a new month."""
        conn = self._get_connection()
        current_month = self._current_month()

        cursor = conn.execute("SELECT month FROM usage_summary WHERE month = ?", (current_month,))
        if cursor.fetchone() is None:
            logger.info(f"New month detected ({current_month}). Resetting usage data.")
            # Archive old month data (could be moved to archive table)
            conn.execute("DELETE FROM usage_summary WHERE month != ?", (current_month,))
            conn.execute("INSERT INTO usage_summary (month) VALUES (?)", (current_month,))
            conn.commit()

    def get_usage_summary(self) -> Dict:
        """Get current usage summary (for dashboard/reporting)."""
        self._reset_if_new_month()

        conn = self._get_connection()
        current_month = self._current_month()

        # Get summary
        cursor = conn.execute(
            "SELECT * FROM usage_summary WHERE month = ?",
            (current_month,)
        )
        summary = dict(cursor.fetchone())

        # Get daily usage
        cursor = conn.execute("SELECT * FROM daily_usage ORDER BY date DESC LIMIT 30")
        daily_usage = {row['date']: dict(row) for row in cursor}

        # Get model usage
        cursor = conn.execute("SELECT * FROM model_usage")
        model_usage = {row['model']: dict(row) for row in cursor}

        # Get provider usage
        cursor = conn.execute("SELECT * FROM provider_usage")
        provider_usage = {row['provider']: dict(row) for row in cursor}

        return {
            'month': current_month,
            'total_cost': summary.get('total_cost', 0.0),
            'total_calls': summary.get('total_calls', 0),
            'perplexity_cost': summary.get('perplexity_cost', 0.0),
            'daily_usage': daily_usage,
            'model_usage': model_usage,
            'provider_usage': provider_usage
        }

    def can_make_call(self, model: str, estimated_tokens: int = 1000, override_daily_limit: bool = False) -> Tuple[bool, str]:
        """
        Check if we can make an API call without exceeding budget.

        Args:
            model: Model name
            estimated_tokens: Estimated token count
            override_daily_limit: If True, bypass daily limits (but still check hard caps)

        Returns:
            Tuple of (can_call, reason)
        """
        self._reset_if_new_month()

        conn = self._get_connection()
        current_month = self._current_month()
        today = self._current_date()

        # Get current usage
        cursor = conn.execute(
            "SELECT total_cost, perplexity_cost FROM usage_summary WHERE month = ?",
            (current_month,)
        )
        summary = cursor.fetchone()
        total_cost = summary['total_cost'] if summary else 0.0
        perplexity_cost = summary['perplexity_cost'] if summary else 0.0

        # Calculate estimated cost
        model_config = self.config.get('models', {}).get(model, {})
        cost_per_1k = model_config.get('cost_per_1k_tokens', 0.0)
        estimated_cost = (estimated_tokens / 1000) * cost_per_1k

        # Check hard caps
        monthly_budget = self.config.get('monthly_budget', 5.0)
        if total_cost + estimated_cost > monthly_budget:
            return False, f"HARD_CAP: Monthly budget ${monthly_budget:.2f} would be exceeded"

        # Check Perplexity-specific limit
        provider = self._get_model_provider(model)
        if provider == 'perplexity':
            perplexity_limit = self.config.get('perplexity_monthly_limit', 4.98)
            if perplexity_cost + estimated_cost > perplexity_limit:
                return False, f"PERPLEXITY_LIMIT_EXCEEDED: ${perplexity_cost:.2f} / ${perplexity_limit:.2f}"

        # Check daily limits (skip if override)
        if not override_daily_limit:
            cursor = conn.execute("SELECT calls FROM daily_usage WHERE date = ?", (today,))
            daily_row = cursor.fetchone()
            daily_calls = daily_row['calls'] if daily_row else 0

            max_calls = self.config.get('daily_limits', {}).get('max_api_calls', 40)
            if daily_calls >= max_calls:
                return False, f"DAILY_LIMIT: Daily API call limit reached ({daily_calls} / {max_calls}). Resets at midnight ET."

            # Check per-model daily limits
            model_limit_key = f"{model}_calls"
            model_daily_limit = self.config.get('daily_limits', {}).get(model_limit_key, max_calls)

            cursor = conn.execute(
                "SELECT calls FROM daily_model_usage WHERE date = ? AND model = ?",
                (today, model)
            )
            model_row = cursor.fetchone()
            model_daily_calls = model_row['calls'] if model_row else 0

            if model_daily_calls >= model_daily_limit:
                return False, f"DAILY_LIMIT: Daily limit for {model} reached ({model_daily_calls} / {model_daily_limit})"
        else:
            logger.info(f"⚠️  Override mode: Bypassing daily limits for {model}")

        return True, "OK"

    def get_available_model(
        self,
        preferred_model: str,
        use_case: str = "sentiment",
        override_daily_limit: bool = False
    ) -> Tuple[str, str]:
        """
        Get best available model considering budget limits.

        Implements automatic fallback cascade:
        1. Try preferred model
        2. If budget/daily limit exceeded, try alternative models
        3. Raise BudgetExceededError if all exhausted

        Args:
            preferred_model: Preferred model name
            use_case: Use case (sentiment, strategy, etc.)
            override_daily_limit: If True, bypass daily limits

        Returns:
            Tuple of (model_name, provider)

        Raises:
            BudgetExceededError: If all models exhausted
        """
        # Try preferred model first
        can_use, reason = self.can_make_call(preferred_model, override_daily_limit=override_daily_limit)
        if can_use:
            provider = self._get_model_provider(preferred_model)
            return preferred_model, provider

        # Log why preferred model failed
        if "PERPLEXITY_LIMIT_EXCEEDED" in reason:
            logger.warning(f"⚠️  Perplexity limit reached ({reason})")
            logger.info("🔄 Attempting fallback to alternative models...")
        elif "DAILY_LIMIT" in reason:
            logger.warning(f"⚠️  Daily limit reached ({reason})")
            logger.info("🔄 Falling back to free Gemini model...")
        else:
            logger.warning(f"⚠️  Budget issue: {reason}")
            logger.info("🔄 Attempting fallback to alternative models...")

        # Try cascade of fallback models
        cascade_order = self.config.get('model_cascade', {}).get('order', ['perplexity', 'google'])

        for provider in cascade_order:
            # Find models for this provider
            for model_name, model_config in self.config.get('models', {}).items():
                if model_config.get('provider') == provider and model_name != preferred_model:
                    can_use, reason = self.can_make_call(model_name, override_daily_limit=override_daily_limit)
                    if can_use:
                        logger.info(f"✓ Using {model_name} ({provider}) as fallback")
                        return model_name, provider
                    else:
                        logger.debug(f"✗ {model_name} also unavailable: {reason}")

        # All models exhausted
        raise BudgetExceededError("All models exhausted - budget or daily limits exceeded for all providers")

    def log_api_call(
        self,
        model: str,
        tokens_used: int,
        cost: float,
        ticker: Optional[str] = None,
        success: bool = True,
        input_tokens: int = 0,
        output_tokens: int = 0
    ):
        """
        Log an API call (process-safe with SQLite ACID transactions).

        Args:
            model: Model name
            tokens_used: Total number of tokens used (for backward compatibility)
            cost: Cost of the call
            ticker: Ticker symbol (if applicable)
            success: Whether call was successful
            input_tokens: Number of input/prompt tokens (optional, for detailed tracking)
            output_tokens: Number of output/completion tokens (optional, for detailed tracking)
        """
        self._reset_if_new_month()

        conn = self._get_connection()
        today = self._current_date()
        current_month = self._current_month()
        provider = self._get_model_provider(model)

        try:
            # Begin transaction
            conn.execute("BEGIN IMMEDIATE")

            if success:
                # Update summary
                conn.execute(
                    """UPDATE usage_summary
                       SET total_cost = total_cost + ?,
                           total_calls = total_calls + 1
                       WHERE month = ?""",
                    (cost, current_month)
                )

                # Update Perplexity cost
                if provider == 'perplexity':
                    conn.execute(
                        """UPDATE usage_summary
                           SET perplexity_cost = perplexity_cost + ?
                           WHERE month = ?""",
                        (cost, current_month)
                    )

                # Update provider usage
                conn.execute(
                    """INSERT INTO provider_usage (provider, calls, cost)
                       VALUES (?, 1, ?)
                       ON CONFLICT(provider) DO UPDATE SET
                           calls = calls + 1,
                           cost = cost + ?""",
                    (provider, cost, cost)
                )

            # Update daily usage
            conn.execute(
                """INSERT INTO daily_usage (date, calls, cost)
                   VALUES (?, 1, ?)
                   ON CONFLICT(date) DO UPDATE SET
                       calls = calls + 1,
                       cost = cost + ?""",
                (today, cost, cost)
            )

            # Update daily model usage
            conn.execute(
                """INSERT INTO daily_model_usage (date, model, calls)
                   VALUES (?, ?, 1)
                   ON CONFLICT(date, model) DO UPDATE SET
                       calls = calls + 1""",
                (today, model)
            )

            # Update model usage
            conn.execute(
                """INSERT INTO model_usage (model, calls, tokens, input_tokens, output_tokens, cost)
                   VALUES (?, 1, ?, ?, ?, ?)
                   ON CONFLICT(model) DO UPDATE SET
                       calls = calls + 1,
                       tokens = tokens + ?,
                       input_tokens = input_tokens + ?,
                       output_tokens = output_tokens + ?,
                       cost = cost + ?""",
                (model, tokens_used, input_tokens, output_tokens, cost,
                 tokens_used, input_tokens, output_tokens, cost)
            )

            # Log call record
            conn.execute(
                """INSERT INTO api_calls (timestamp, model, tokens, input_tokens, output_tokens, cost, ticker, success, date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    datetime.now().isoformat(),
                    model,
                    tokens_used,
                    input_tokens,
                    output_tokens,
                    cost,
                    ticker,
                    1 if success else 0,
                    today
                )
            )

            # Commit transaction
            conn.commit()

            if success:
                if input_tokens > 0 or output_tokens > 0:
                    logger.info(
                        f"API call logged: {model} - {tokens_used} tokens "
                        f"(in:{input_tokens}, out:{output_tokens}) - ${cost:.4f} "
                        f"[{ticker or 'no ticker'}]"
                    )
                else:
                    logger.info(
                        f"API call logged: {model} - {tokens_used} tokens - ${cost:.4f} "
                        f"[{ticker or 'no ticker'}]"
                    )

        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to log API call: {e}")
            raise

    # Note: close(), __enter__(), __exit__() inherited from SQLiteBase
