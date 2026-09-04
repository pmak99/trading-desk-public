"""
Migration manager for database schema versioning.

Provides a formal, repeatable system for applying database schema changes
with version tracking, rollback support, and transaction safety.

Overview:
    The migration system tracks schema changes in a `schema_migrations` table,
    ensuring that each migration is applied exactly once and in the correct order.

    Migrations are defined as Migration objects with:
    - version: Sequential integer (1, 2, 3, ...)
    - name: Descriptive name (e.g., "add_cache_expiration_column")
    - sql_up: SQL to apply the migration
    - sql_down: SQL to rollback (optional)

Benefits:
    - Repeatable deployments: Same migrations work across all environments
    - Audit trail: schema_migrations table shows what changed and when
    - Safe rollbacks: Undo changes with sql_down if needed
    - Transaction safety: All-or-nothing application prevents partial migrations
    - Version control: Schema changes tracked alongside code

Usage:
    # Automatic (via Container):
    container = Container(config, run_migrations=True)  # Applies pending migrations

    # Manual (via CLI):
    python scripts/migrate.py status   # Check current version
    python scripts/migrate.py migrate  # Apply all pending
    python scripts/migrate.py rollback 1  # Rollback to version 1

    # Programmatic:
    manager = MigrationManager(db_path)
    manager.migrate()  # Apply all pending migrations

Architecture Decision:
    See docs/adr/005-database-migration-system.md for detailed rationale.
"""

import sqlite3
import logging
from pathlib import Path
from typing import List, Tuple
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class Migration:
    """
    Represents a single database migration.

    Each migration is a versioned schema change that can be applied
    and optionally rolled back.

    Attributes:
        version: Sequential version number (1, 2, 3, ...). Must be unique.
        name: Descriptive name (e.g., "add_user_table", "add_index_on_ticker")
        sql_up: SQL statements to apply the migration (forward)
        sql_down: SQL statements to rollback the migration (reverse, optional)

    Example:
        Migration(
            version=3,
            name="add_greeks_columns",
            sql_up=\"\"\"
                ALTER TABLE strategies ADD COLUMN theta REAL;
                ALTER TABLE strategies ADD COLUMN vega REAL;
            \"\"\",
            sql_down=\"\"\"
                ALTER TABLE strategies DROP COLUMN theta;
                ALTER TABLE strategies DROP COLUMN vega;
            \"\"\"
        )

    Notes:
        - Version numbers must be sequential and unique
        - sql_up is required, sql_down is optional
        - Multi-statement SQL is supported (separated by semicolons)
        - Migrations are applied in version order
    """
    version: int
    name: str
    sql_up: str
    sql_down: str | None = None  # Optional rollback SQL


class MigrationManager:
    """
    Manages database schema migrations.

    Features:
    - Version tracking in schema_migrations table
    - Sequential migration application
    - Idempotent (safe to run multiple times)
    - Rollback support (optional)
    - Transaction-based (all-or-nothing)

    Usage:
        manager = MigrationManager(db_path)
        manager.migrate()  # Apply all pending migrations
    """

    def __init__(self, db_path: Path | str):
        """
        Initialize migration manager.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrations: List[Migration] = []
        self._load_migrations()

    def _load_migrations(self):
        """Load all migration definitions."""
        # Migrations are defined in code for now
        # Future: Could load from SQL files in migrations/ directory

        # Migration 001: Create schema_migrations table
        self.migrations.append(Migration(
            version=1,
            name="create_schema_migrations_table",
            sql_up="""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL,
                    checksum TEXT
                )
            """,
            sql_down="DROP TABLE IF EXISTS schema_migrations"
        ))

        # Migration 002: Add expiration column to cache table
        self.migrations.append(Migration(
            version=2,
            name="add_cache_expiration_column",
            sql_up="""
                -- Check if cache table exists and add expiration if needed
                CREATE TABLE IF NOT EXISTS cache_temp AS SELECT * FROM cache LIMIT 0;
                DROP TABLE cache_temp;
            """,  # Actual migration happens in _apply_migration_002
            sql_down=None  # No safe rollback without data loss
        ))

        # Migration 003: Add last_validated_at to earnings_calendar
        # Tracks when conflict validation was last performed to skip re-validation
        # if validated within 48 hours
        self.migrations.append(Migration(
            version=3,
            name="add_last_validated_at_column",
            sql_up="""
                -- Handled in _apply_migration_003
            """,  # Actual migration happens in _apply_migration_003
            sql_down=None  # SQLite doesn't support DROP COLUMN easily
        ))

        # Migration 004: Recreate analysis_log with correct 19-column schema
        # DB has old 11-column schema (analyzed_at, edge_score), code expects 19 columns.
        # Table is empty (0 rows) - safe to drop and recreate.
        self.migrations.append(Migration(
            version=4,
            name="recreate_analysis_log_table",
            sql_up="""
                -- Handled in _apply_migration_004
            """,
            sql_down="DROP TABLE IF EXISTS analysis_log"
        ))

        # Migration 005: Add missing index + drop redundant index
        # ADD idx_strategies_parent on strategies(parent_strategy_id) - needed for FK joins
        # DROP idx_earnings_date - redundant, covered by idx_earnings_date_ticker(earnings_date, ticker)
        self.migrations.append(Migration(
            version=5,
            name="add_parent_index_drop_redundant",
            sql_up="""
                -- Handled in _apply_migration_005
            """,
            sql_down=None
        ))

        # Migration 006: Add missing indices on strategies and trade_journal
        # These tables are heavily queried but have zero secondary indices.
        self.migrations.append(Migration(
            version=6,
            name="add_strategies_and_journal_indices",
            sql_up="""
                -- Handled in _apply_migration_006
            """,
            sql_down=None
        ))

        # Migration 007: Add account_type to trade_journal and strategies
        # Distinguishes IRA vs TAXABLE trades. Existing rows default to TAXABLE.
        self.migrations.append(Migration(
            version=7,
            name="add_account_type_column",
            sql_up="""
                -- Handled in _apply_migration_007
            """,
            sql_down=None
        ))

        # Migration 008: Update trade_journal UNIQUE key to include account_type.
        # Without this, identical option positions in IRA and TAXABLE accounts
        # collide on INSERT OR IGNORE and one row is silently dropped.
        self.migrations.append(Migration(
            version=8,
            name="add_account_type_to_unique_key",
            sql_up="""
                -- Handled in _apply_migration_008
            """,
            sql_down=None
        ))

        # Migration 009: Add pre_earnings_straddle_pct to historical_moves.
        # Stores the ATM straddle price (as % of stock price) at the time of
        # analysis before earnings. Backfilled from ORATS ernStraPct1-12;
        # populated going forward via analysis_log writes.
        self.migrations.append(Migration(
            version=9,
            name="add_pre_earnings_straddle_pct",
            sql_up="""
                -- Handled in _apply_migration_009
            """,
            sql_down=None
        ))

        # Migration 010: Add ORATS-derived analytics columns.
        # historical_moves.ern_iv_effect: IV multiplier earnings added (ernEffct1-12).
        # position_limits: iv_rank_1y, iv_pct_1y (IV rank/percentile), iv30d, hv20d,
        #   abs_avg_ern_mv (ORATS average absolute earnings move), orats_implied_ern_mv.
        # Backfilled via backfill_orats_ivrank.py + backfill_orats_cores_extra.py.
        self.migrations.append(Migration(
            version=10,
            name="add_orats_analytics_columns",
            sql_up="""
                -- Handled in _apply_migration_010
            """,
            sql_down=None
        ))

        # Migration 011: Add fcst_ern_iv_effect to position_limits.
        # ORATS fcstErnEffct = model-based forecast of ern_iv_effect for the NEXT earnings quarter.
        # Populated by scripts/refresh_orats_snapshots.py (run weekly or before scans).
        self.migrations.append(Migration(
            version=11,
            name="add_fcst_ern_iv_effect",
            sql_up="""
                -- Handled in _apply_migration_011
            """,
            sql_down=None
        ))

        # Migration 012: Add ieeEarnEffect and rSlp30 columns to position_limits.
        # iee_earn_effect: ORATS live market implied earnings IV multiplier (capped at 4.0).
        # r_slp_30: ORATS 30-day put/call skew slope (used for skew signal fusion).
        # Both populated by fetch_orats_ticker.py (per /analyze) and
        # refresh_orats_snapshots.py (weekly batch via /datav2/summaries).
        self.migrations.append(Migration(
            version=12,
            name="add_iee_r_slp_columns",
            sql_up="""
                -- Handled in _apply_migration_012
            """,
            sql_down=None
        ))

        # Migration 013: Add compound risk tracking to bias_predictions.
        # compound_risk_active: 1 when ≥2 of [TRR HIGH, sizing alarm, BEARISH/STRONG_BEARISH fused skew] fired.
        # r_slp_30: ORATS value at prediction time (Tradier-only directional_bias stored separately).
        # fused_bias: the fused directional signal (ORATS-weighted) used in actual analysis.
        # trr_level: tail risk classification at prediction time.
        # Enables measuring fused skew accuracy specifically in compound risk zones.
        self.migrations.append(Migration(
            version=13,
            name="add_compound_risk_tracking_to_bias_predictions",
            sql_up="""
                -- Handled in _apply_migration_013
            """,
            sql_down=None
        ))

        # Migration 014: Add sizing_alarm to bias_predictions.
        # Stores whether the ORATS sizing alarm fired (Rule A: fcst≥2.0 OR Rule B: iee/fcst≥1.5).
        # Enables distinguishing compound risk sub-types:
        #   FULL (bearish skew among signals): 44% historical crush rate → SKIP
        #   PARTIAL (TRR+SIZING, no bearish skew): 67% historical crush rate → trade at reduced size
        # Without this column, the only way to infer sizing_alarm is
        # compound_risk_active=1 AND fused_bias NOT IN ('bearish','strong_bearish').
        self.migrations.append(Migration(
            version=14,
            name="add_sizing_alarm_to_bias_predictions",
            sql_up="""
                -- Handled in _apply_migration_014
            """,
            sql_down=None
        ))

        # Migration 015 (migrate_sentiment_tables_from_4.0) was applied
        # out-of-band in May 2026 and recorded directly in schema_migrations.

        # Migration 016: Add gap-inclusive VRP columns to analysis_log.
        # vrp_close_ratio = implied move / mean |close_move_pct| over the same
        # historical window the production (intraday-baseline) VRP uses.
        # intraday_move_pct excludes the overnight gap, so intraday VRP can be
        # inflated for gap-dominant tickers (ORCL Jun 2026). Logged on every
        # /analyze and /scan as a live A/B before any convention migration.
        self.migrations.append(Migration(
            version=16,
            name="add_vrp_close_ratio_to_analysis_log",
            sql_up="""
                -- Handled in _apply_migration_016
            """,
            sql_down=None
        ))

        # Migration 017: Add IV term-structure slope to analysis_log.
        # term_slope_ratio = front-expiry ATM IV / ~30d-out ATM IV at analysis
        # time (>1 = backwardation, the event-vol multiple). Measures how much
        # of the front IV is event-specific and will crush. Logged on every
        # /analyze for forward validation before it becomes a gate.
        self.migrations.append(Migration(
            version=17,
            name="add_term_slope_ratio_to_analysis_log",
            sql_up="""
                -- Handled in _apply_migration_017
            """,
            sql_down=None
        ))

        # Migration 018: TACO index-options skill tables.
        # taco_log = every /taco run (forward validation, like analysis_log).
        # taco_positions = open/closed TACO positions with FROZEN entry
        # reference levels (spec 2026-07-14: refs never update while open).
        self.migrations.append(Migration(
            version=18,
            name="add_taco_tables",
            sql_up="""
                -- Handled in _apply_migration_018
            """,
            sql_down="""
                DROP TABLE IF EXISTS taco_log;
                DROP TABLE IF EXISTS taco_positions;
            """
        ))

        # Migration 019: cross-asset confirmation columns on taco_log
        # (spec 2026-07-22). count/available = confirmation count over the
        # assets that had data; detail = per-asset JSON (move, threshold,
        # confirmed, source) for forward validation.
        self.migrations.append(Migration(
            version=19,
            name="add_cross_asset_to_taco_log",
            sql_up="""
                -- Handled in _apply_migration_019
            """,
            sql_down=None
        ))

        # Migration 020: Recent-move streak columns on analysis_log.
        # recent_move_up_count / recent_move_qtrs = up-quarters out of the last
        # (up to 4) prior quarters' close_move_pct. Observational only — a Sep
        # 2026 OOS backtest found no predictive power (47-50% out of sample);
        # not a sizing/strategy-selection input. Raw counts, not a boolean
        # flag, so a future re-test isn't locked into today's threshold.
        self.migrations.append(Migration(
            version=20,
            name="add_recent_move_streak_to_analysis_log",
            sql_up="""
                -- Handled in _apply_migration_020
            """,
            sql_down=None
        ))

        # Sort migrations by version (safety check)
        self.migrations.sort(key=lambda m: m.version)

    def _ensure_migrations_table(self, conn: sqlite3.Connection):
        """Ensure schema_migrations table exists."""
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL,
                checksum TEXT
            )
        """)
        conn.commit()

    def get_current_version(self) -> int:
        """
        Get current schema version.

        Returns:
            Current version number, or 0 if no migrations applied
        """
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            self._ensure_migrations_table(conn)
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(version) FROM schema_migrations")
            result = cursor.fetchone()
            return result[0] if result[0] is not None else 0

    def get_pending_migrations(self) -> List[Migration]:
        """
        Get list of pending migrations.

        Returns:
            List of migrations not yet applied
        """
        current_version = self.get_current_version()
        return [m for m in self.migrations if m.version > current_version]

    def get_applied_migrations(self) -> List[Tuple[int, str, str]]:
        """
        Get list of applied migrations.

        Returns:
            List of tuples: (version, name, applied_at)
        """
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            self._ensure_migrations_table(conn)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
            )
            return cursor.fetchall()

    def migrate(self, target_version: int | None = None) -> int:
        """
        Apply all pending migrations up to target version.

        Args:
            target_version: Version to migrate to (None = latest)

        Returns:
            Number of migrations applied

        Raises:
            RuntimeError: If migration fails
        """
        pending = self.get_pending_migrations()

        if target_version is not None:
            pending = [m for m in pending if m.version <= target_version]

        if not pending:
            logger.info(f"Database at version {self.get_current_version()}, no migrations needed")
            return 0

        logger.info(f"Applying {len(pending)} migrations to {self.db_path.name}...")

        applied_count = 0
        for migration in pending:
            try:
                self._apply_migration(migration)
                applied_count += 1
                logger.info(
                    f"✓ Applied migration {migration.version}: {migration.name}"
                )
            except Exception as e:
                logger.error(
                    f"✗ Failed to apply migration {migration.version}: {migration.name}"
                )
                logger.error(f"Error: {e}")
                raise RuntimeError(
                    f"Migration {migration.version} failed: {e}"
                ) from e

        logger.info(f"Successfully applied {applied_count} migrations")
        return applied_count

    def _apply_migration(self, migration: Migration):
        """
        Apply a single migration within a transaction.

        Args:
            migration: Migration to apply

        Raises:
            Exception: If migration fails (transaction rolled back)
        """
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("BEGIN TRANSACTION")
            try:
                cursor = conn.cursor()

                # Special handling for specific migrations
                if migration.version == 2:
                    self._apply_migration_002(cursor)
                elif migration.version == 3:
                    self._apply_migration_003(cursor)
                elif migration.version == 4:
                    self._apply_migration_004(cursor)
                elif migration.version == 5:
                    self._apply_migration_005(cursor)
                elif migration.version == 6:
                    self._apply_migration_006(cursor)
                elif migration.version == 7:
                    self._apply_migration_007(cursor)
                elif migration.version == 8:
                    self._apply_migration_008(cursor)
                elif migration.version == 9:
                    self._apply_migration_009(cursor)
                elif migration.version == 10:
                    self._apply_migration_010(cursor)
                elif migration.version == 11:
                    self._apply_migration_011(cursor)
                elif migration.version == 12:
                    self._apply_migration_012(cursor)
                elif migration.version == 13:
                    self._apply_migration_013(cursor)
                elif migration.version == 14:
                    self._apply_migration_014(cursor)
                elif migration.version == 16:
                    self._apply_migration_016(cursor)
                elif migration.version == 17:
                    self._apply_migration_017(cursor)
                elif migration.version == 18:
                    self._apply_migration_018(cursor)
                elif migration.version == 19:
                    self._apply_migration_019(cursor)
                elif migration.version == 20:
                    self._apply_migration_020(cursor)
                else:
                    # Execute statements individually (safer than executescript)
                    for statement in migration.sql_up.split(';'):
                        statement = statement.strip()
                        if statement and not statement.startswith('--'):
                            cursor.execute(statement)

                # Record migration in schema_migrations
                cursor.execute(
                    """
                    INSERT INTO schema_migrations (version, name, applied_at)
                    VALUES (?, ?, ?)
                    """,
                    (migration.version, migration.name, datetime.now().isoformat())
                )

                conn.commit()
            except Exception as e:
                conn.rollback()
                raise

    def _apply_migration_002(self, cursor: sqlite3.Cursor):
        """
        Apply migration 002: Add expiration column to cache table.

        Handles the case where cache table might not exist or already has the column.
        """
        # Check if cache table exists
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='cache'
        """)
        if not cursor.fetchone():
            # Table doesn't exist, skip migration
            logger.debug("Cache table doesn't exist, skipping expiration column migration")
            return

        # Check if expiration column already exists
        cursor.execute("PRAGMA table_info(cache)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'expiration' not in columns:
            logger.info("Adding expiration column to cache table")
            cursor.execute("ALTER TABLE cache ADD COLUMN expiration TEXT")
        else:
            logger.debug("Cache table already has expiration column")

    def _apply_migration_003(self, cursor: sqlite3.Cursor):
        """
        Apply migration 003: Add last_validated_at column to earnings_calendar.

        Tracks when conflict validation was last performed to enable
        skipping re-validation for tickers validated within 48 hours.
        """
        # Check if earnings_calendar table exists
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='earnings_calendar'
        """)
        if not cursor.fetchone():
            # Table doesn't exist, skip migration
            logger.debug("earnings_calendar table doesn't exist, skipping migration")
            return

        # Check if last_validated_at column already exists
        cursor.execute("PRAGMA table_info(earnings_calendar)")
        columns = [row[1] for row in cursor.fetchall()]

        if 'last_validated_at' not in columns:
            logger.info("Adding last_validated_at column to earnings_calendar table")
            cursor.execute("ALTER TABLE earnings_calendar ADD COLUMN last_validated_at DATETIME")
        else:
            logger.debug("earnings_calendar table already has last_validated_at column")

    def _apply_migration_004(self, cursor: sqlite3.Cursor):
        """
        Apply migration 004: Recreate analysis_log with correct 19-column schema.

        The existing table has old 11-column schema (analyzed_at, edge_score).
        Code expects 19 columns (timestamp, vix_level, strategy_type, etc.).
        Table is empty (0 rows) so safe to drop and recreate.
        """
        # Verify table is empty before dropping
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='analysis_log'
        """)
        if cursor.fetchone():
            row_count = cursor.execute("SELECT COUNT(*) FROM analysis_log").fetchone()[0]
            if row_count > 0:
                logger.warning(
                    f"analysis_log has {row_count} rows - skipping recreation to preserve data"
                )
                return

            # Drop old table and its indexes
            cursor.execute("DROP TABLE analysis_log")
            logger.info("Dropped old analysis_log table (0 rows)")

        # Recreate with correct 19-column schema (matches init_schema.py)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS analysis_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                ticker TEXT NOT NULL,
                earnings_date DATE NOT NULL,
                expiration DATE NOT NULL,
                implied_move_pct REAL NOT NULL,
                historical_mean_pct REAL NOT NULL,
                vrp_ratio REAL NOT NULL,
                recommendation TEXT NOT NULL,
                confidence REAL,
                consistency_score REAL,
                vix_level REAL,
                vix_regime TEXT,
                strategy_type TEXT,
                strategy_score REAL,
                strategy_pop REAL,
                strategy_rr REAL,
                contracts INTEGER,
                raw_analysis TEXT
            )
        """)

        # Create indexes (matches init_schema.py)
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_analysis_ticker ON analysis_log(ticker)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_analysis_timestamp ON analysis_log(timestamp)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_analysis_recommendation ON analysis_log(recommendation)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_analysis_regime ON analysis_log(vix_regime)'
        )
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_analysis_strategy ON analysis_log(strategy_type)'
        )
        logger.info("Recreated analysis_log with 19-column schema + 5 indexes")

    def _apply_migration_005(self, cursor: sqlite3.Cursor):
        """
        Apply migration 005: Add missing index + drop redundant index.

        - ADD idx_strategies_parent on strategies(parent_strategy_id) for FK joins
        - DROP idx_earnings_date (redundant, covered by idx_earnings_date_ticker)
        """
        # Add parent strategy index if strategies table exists
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='strategies'
        """)
        if cursor.fetchone():
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_strategies_parent
                ON strategies(parent_strategy_id)
            """)
            logger.info("Created idx_strategies_parent index")

        # Drop redundant idx_earnings_date (covered by idx_earnings_date_ticker)
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='index' AND name='idx_earnings_date'
        """)
        if cursor.fetchone():
            cursor.execute("DROP INDEX idx_earnings_date")
            logger.info("Dropped redundant idx_earnings_date index")
        else:
            logger.debug("idx_earnings_date already absent, skipping drop")

    def _apply_migration_006(self, cursor: sqlite3.Cursor):
        """
        Apply migration 006: Add missing indices on strategies and trade_journal.

        These tables are heavily queried (GROUP BY, WHERE, JOIN) but had no
        secondary indices, causing full table scans on every query.
        """
        # strategies table indices
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='strategies'
        """)
        if cursor.fetchone():
            index_defs = [
                ("idx_strategies_type", "strategies(strategy_type)"),
                ("idx_strategies_symbol", "strategies(symbol)"),
                ("idx_strategies_earnings", "strategies(earnings_date)"),
            ]
            for idx_name, idx_def in index_defs:
                cursor.execute(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_def}"
                )
                logger.info(f"Created index {idx_name}")

            # Partial indices for sparse columns
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_strategies_campaign
                ON strategies(campaign_id) WHERE campaign_id IS NOT NULL
            """)
            logger.info("Created partial index idx_strategies_campaign")

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_strategies_trr
                ON strategies(trr_at_entry) WHERE trr_at_entry IS NOT NULL
            """)
            logger.info("Created partial index idx_strategies_trr")

        # trade_journal table indices
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='trade_journal'
        """)
        if cursor.fetchone():
            index_defs = [
                ("idx_trade_journal_strategy", "trade_journal(strategy_id)"),
                ("idx_trade_journal_symbol", "trade_journal(symbol)"),
                ("idx_trade_journal_sale_date", "trade_journal(sale_date)"),
                ("idx_trade_journal_earnings", "trade_journal(earnings_date)"),
            ]
            for idx_name, idx_def in index_defs:
                cursor.execute(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON {idx_def}"
                )
                logger.info(f"Created index {idx_name}")

        # Update query planner statistics
        cursor.execute("ANALYZE")
        logger.info("Updated query planner statistics (ANALYZE)")

    def _apply_migration_007(self, cursor: sqlite3.Cursor):
        """
        Apply migration 007: Add account_type column to trade_journal and strategies.

        Existing rows default to TAXABLE. IRA trades imported after this migration
        are tagged at insert time via --account-type IRA.
        """
        _ALLOWED_TABLES = frozenset({'trade_journal', 'strategies'})
        for table in _ALLOWED_TABLES:
            cursor.execute(f"PRAGMA table_info([{table}])")
            columns = [row[1] for row in cursor.fetchall()]
            if not columns:
                logger.debug(f"{table} does not exist, skipping account_type migration")
                continue
            if 'account_type' not in columns:
                cursor.execute(
                    f"ALTER TABLE [{table}] ADD COLUMN account_type TEXT NOT NULL DEFAULT 'TAXABLE'"
                )
                logger.info(f"Added account_type column to {table}")
            else:
                logger.debug(f"{table} already has account_type column")

    def _apply_migration_008(self, cursor: sqlite3.Cursor):
        """
        Apply migration 008: Recreate trade_journal with account_type in UNIQUE key.

        SQLite cannot ALTER a UNIQUE constraint, so this recreates the table.
        Without account_type in the key, identical option positions in IRA and
        TAXABLE accounts collide on INSERT OR IGNORE and one row is silently lost.
        """
        # Idempotency / guard: check if trade_journal exists and whether account_type
        # is already part of the UNIQUE constraint.
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='trade_journal'")
        row = cursor.fetchone()
        if not row:
            logger.debug("trade_journal does not exist, skipping migration 008")
            return
        if row:
            ddl = row[0] or ''
            unique_clause = ddl.split('UNIQUE')[-1] if 'UNIQUE' in ddl else ''
            if 'account_type' in unique_clause:
                logger.debug("trade_journal UNIQUE constraint already includes account_type")
                return

        cursor.execute("""
            CREATE TABLE trade_journal_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                acquired_date DATE,
                sale_date DATE NOT NULL,
                days_held INTEGER,
                option_type TEXT,
                strike REAL,
                expiration DATE,
                quantity INTEGER,
                cost_basis REAL NOT NULL,
                proceeds REAL NOT NULL,
                gain_loss REAL NOT NULL,
                is_winner BOOLEAN NOT NULL,
                term TEXT,
                wash_sale_amount REAL DEFAULT 0,
                earnings_date DATE,
                actual_move REAL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                strategy_id INTEGER REFERENCES strategies(id),
                account_type TEXT NOT NULL DEFAULT 'TAXABLE',
                UNIQUE(symbol, acquired_date, sale_date, option_type, strike,
                       cost_basis, account_type)
            )
        """)

        cursor.execute("""
            INSERT INTO trade_journal_new
            SELECT id, symbol, acquired_date, sale_date, days_held, option_type,
                   strike, expiration, quantity, cost_basis, proceeds, gain_loss,
                   is_winner, term, wash_sale_amount, earnings_date, actual_move,
                   created_at, strategy_id, COALESCE(account_type, 'TAXABLE')
            FROM trade_journal
        """)

        cursor.execute("DROP TABLE trade_journal")
        cursor.execute("ALTER TABLE trade_journal_new RENAME TO trade_journal")

        for idx_name, idx_col in [
            ("idx_journal_symbol",          "trade_journal(symbol)"),
            ("idx_journal_sale_date",       "trade_journal(sale_date)"),
            ("idx_journal_earnings",        "trade_journal(earnings_date)"),
            ("idx_trade_journal_strategy",  "trade_journal(strategy_id)"),
            ("idx_trade_journal_symbol",    "trade_journal(symbol)"),
            ("idx_trade_journal_sale_date", "trade_journal(sale_date)"),
            ("idx_trade_journal_earnings",  "trade_journal(earnings_date)"),
        ]:
            cursor.execute(f"CREATE INDEX IF NOT EXISTS [{idx_name}] ON {idx_col}")

        cursor.execute("ANALYZE")
        logger.info("Recreated trade_journal with account_type in UNIQUE constraint")

    def _apply_migration_009(self, cursor: sqlite3.Cursor):
        """
        Apply migration 009: Add pre_earnings_straddle_pct to historical_moves.

        Nullable REAL column — NULL for rows created before this migration.
        Backfilled from ORATS ernStraPct1-12 via scripts/backfill_orats_straddle.py;
        populated going forward from analysis_log implied_move_pct writes.
        """
        cursor.execute("PRAGMA table_info([historical_moves])")
        columns = [row[1] for row in cursor.fetchall()]
        if not columns:
            logger.debug("historical_moves does not exist yet, skipping migration 009")
            return
        if 'pre_earnings_straddle_pct' not in columns:
            cursor.execute(
                "ALTER TABLE [historical_moves] ADD COLUMN pre_earnings_straddle_pct REAL"
            )
            logger.info("Added pre_earnings_straddle_pct column to historical_moves")
        else:
            logger.debug("historical_moves already has pre_earnings_straddle_pct column")

    def _apply_migration_010(self, cursor: sqlite3.Cursor):
        """
        Apply migration 010: Add ORATS analytics columns.

        historical_moves: ern_iv_effect (IV multiplier from earnings effect, ernEffct1-12).
        position_limits: iv_rank_1y, iv_pct_1y, iv30d, hv20d, abs_avg_ern_mv,
                         orats_implied_ern_mv (current snapshot + aggregate fields).
        All nullable — backfilled via backfill_orats_ivrank.py and
        backfill_orats_cores_extra.py.
        """
        cursor.execute("PRAGMA table_info([historical_moves])")
        hm_cols = [row[1] for row in cursor.fetchall()]
        if not hm_cols:
            logger.debug("historical_moves does not exist yet, skipping ern_iv_effect column")
        elif 'ern_iv_effect' not in hm_cols:
            cursor.execute(
                "ALTER TABLE [historical_moves] ADD COLUMN ern_iv_effect REAL"
            )
            logger.info("Added ern_iv_effect column to historical_moves")

        cursor.execute("PRAGMA table_info([position_limits])")
        pl_cols = [row[1] for row in cursor.fetchall()]
        if not pl_cols:
            logger.debug("position_limits does not exist yet, skipping migration 010 (position_limits)")
            return
        new_pl_cols = {
            'iv_rank_1y': 'REAL',
            'iv_pct_1y': 'REAL',
            'iv30d': 'REAL',
            'hv20d': 'REAL',
            'abs_avg_ern_mv': 'REAL',
            'orats_implied_ern_mv': 'REAL',
        }
        for col, col_type in new_pl_cols.items():
            if col not in pl_cols:
                cursor.execute(
                    f"ALTER TABLE [position_limits] ADD COLUMN {col} {col_type}"
                )
                logger.info(f"Added {col} column to position_limits")

    def _apply_migration_011(self, cursor: sqlite3.Cursor):
        """
        Apply migration 011: Add fcst_ern_iv_effect to position_limits.

        ORATS fcstErnEffct = model-based forecast of ern_iv_effect for the NEXT quarter.
        No offline substitute — requires live ORATS /datav2/cores call.
        Populated by scripts/refresh_orats_snapshots.py.
        """
        cursor.execute("PRAGMA table_info([position_limits])")
        pl_cols = [row[1] for row in cursor.fetchall()]
        if not pl_cols:
            logger.debug("position_limits does not exist yet, skipping migration 011")
            return
        if 'fcst_ern_iv_effect' not in pl_cols:
            cursor.execute(
                "ALTER TABLE [position_limits] ADD COLUMN fcst_ern_iv_effect REAL"
            )
            logger.info("Added fcst_ern_iv_effect column to position_limits")

    def _apply_migration_012(self, cursor: sqlite3.Cursor):
        """Apply migration 012: Add iee_earn_effect and r_slp_30 to position_limits."""
        cursor.execute("PRAGMA table_info([position_limits])")
        pl_cols = [row[1] for row in cursor.fetchall()]
        if not pl_cols:
            return
        for col, sql in [
            ('iee_earn_effect', 'ALTER TABLE [position_limits] ADD COLUMN iee_earn_effect REAL'),
            ('r_slp_30',        'ALTER TABLE [position_limits] ADD COLUMN r_slp_30 REAL'),
        ]:
            if col not in pl_cols:
                cursor.execute(sql)
                logger.info(f"Added {col} column to position_limits")

    def _apply_migration_013(self, cursor: sqlite3.Cursor):
        """Apply migration 013: Add compound risk tracking to bias_predictions."""
        cursor.execute("PRAGMA table_info([bias_predictions])")
        bp_cols = [row[1] for row in cursor.fetchall()]
        if not bp_cols:
            return
        for col, sql in [
            ('compound_risk_active', 'ALTER TABLE [bias_predictions] ADD COLUMN compound_risk_active INTEGER'),
            ('r_slp_30',            'ALTER TABLE [bias_predictions] ADD COLUMN r_slp_30 REAL'),
            ('fused_bias',          'ALTER TABLE [bias_predictions] ADD COLUMN fused_bias TEXT'),
            ('trr_level',           'ALTER TABLE [bias_predictions] ADD COLUMN trr_level TEXT'),
        ]:
            if col not in bp_cols:
                cursor.execute(sql)
                logger.info(f"Added {col} column to bias_predictions")

    def _apply_migration_014(self, cursor: sqlite3.Cursor):
        """Apply migration 014: Add sizing_alarm to bias_predictions.

        Stores whether the ORATS sizing alarm fired (Rule A: fcst>=2.0 OR Rule B: iee/fcst>=1.5).
        Enables clean separation of compound risk sub-types without re-deriving from position_limits:
          FULL  (bearish skew among signals): 44% historical crush → SKIP
          PARTIAL (TRR+SIZING, no bearish skew): 67% historical crush → trade at reduced size
        """
        cursor.execute("PRAGMA table_info([bias_predictions])")
        bp_cols = [row[1] for row in cursor.fetchall()]
        if not bp_cols:
            return
        if 'sizing_alarm' not in bp_cols:
            cursor.execute('ALTER TABLE [bias_predictions] ADD COLUMN sizing_alarm INTEGER')
            logger.info("Added sizing_alarm column to bias_predictions")

    def _apply_migration_016(self, cursor: sqlite3.Cursor):
        """Apply migration 016: Add gap-inclusive VRP columns to analysis_log."""
        cursor.execute("PRAGMA table_info([analysis_log])")
        al_cols = [row[1] for row in cursor.fetchall()]
        if not al_cols:
            return
        for col, sql in [
            ('historical_close_mean_pct', 'ALTER TABLE [analysis_log] ADD COLUMN historical_close_mean_pct REAL'),
            ('vrp_close_ratio',           'ALTER TABLE [analysis_log] ADD COLUMN vrp_close_ratio REAL'),
        ]:
            if col not in al_cols:
                cursor.execute(sql)
                logger.info(f"Added {col} column to analysis_log")

    def _apply_migration_017(self, cursor: sqlite3.Cursor):
        """Apply migration 017: Add IV term-structure slope to analysis_log."""
        cursor.execute("PRAGMA table_info([analysis_log])")
        al_cols = [row[1] for row in cursor.fetchall()]
        if not al_cols:
            return
        if 'term_slope_ratio' not in al_cols:
            cursor.execute('ALTER TABLE [analysis_log] ADD COLUMN term_slope_ratio REAL')
            logger.info("Added term_slope_ratio column to analysis_log")

    def _apply_migration_018(self, cursor: sqlite3.Cursor):
        """Apply migration 018: TACO skill tables (taco_log, taco_positions)."""
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS taco_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                mode TEXT NOT NULL,
                direction TEXT,
                spot REAL,
                drawdown_pct REAL,
                runup_z REAL,
                vix REAL,
                vix_spike REAL,
                vix_term_ratio REAL,
                event_date TEXT,
                event_type TEXT,
                event_rationale TEXT,
                score REAL,
                tier TEXT,
                recommendation TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS taco_positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                direction TEXT NOT NULL,        -- 'CALL' | 'PUT'
                symbol TEXT NOT NULL,           -- 'SPX' | 'SPY'
                contracts REAL NOT NULL,
                strike REAL NOT NULL,
                expiration TEXT NOT NULL,
                premium_paid REAL NOT NULL,     -- total dollars
                entry_date TEXT NOT NULL,
                event_date TEXT NOT NULL,
                pre_event_high REAL,            -- frozen at entry
                panic_low REAL,                 -- calls, frozen
                euphoria_high REAL,             -- puts, frozen
                rip_base REAL,                  -- puts: 20d mean close at event_date
                pre_event_vix REAL,             -- frozen
                status TEXT NOT NULL DEFAULT 'OPEN',  -- 'OPEN' | 'CLOSED'
                exit_date TEXT,
                proceeds REAL,
                outcome_pnl REAL,
                rule_compliant INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        logger.info("Created taco_log and taco_positions tables")

    def _apply_migration_019(self, cursor: sqlite3.Cursor):
        """Apply migration 019: cross-asset confirmation columns on taco_log."""
        cursor.execute("PRAGMA table_info([taco_log])")
        cols = [row[1] for row in cursor.fetchall()]
        if not cols:
            return
        for col, typ in (("cross_asset_count", "INTEGER"),
                         ("cross_asset_available", "INTEGER"),
                         ("cross_asset_detail", "TEXT")):
            if col not in cols:
                cursor.execute(
                    f"ALTER TABLE [taco_log] ADD COLUMN {col} {typ}")
                logger.info(f"Added {col} column to taco_log")

    def _apply_migration_020(self, cursor: sqlite3.Cursor):
        """Apply migration 020: recent-move streak columns on analysis_log."""
        cursor.execute("PRAGMA table_info([analysis_log])")
        al_cols = [row[1] for row in cursor.fetchall()]
        if not al_cols:
            return
        for col in ("recent_move_up_count", "recent_move_qtrs"):
            if col not in al_cols:
                cursor.execute(f"ALTER TABLE [analysis_log] ADD COLUMN {col} INTEGER")
                logger.info(f"Added {col} column to analysis_log")

    def rollback(self, target_version: int) -> int:
        """
        Rollback migrations to target version.

        Args:
            target_version: Version to rollback to

        Returns:
            Number of migrations rolled back

        Raises:
            RuntimeError: If rollback fails or migration has no rollback SQL
        """
        current_version = self.get_current_version()

        if target_version >= current_version:
            logger.info("No rollback needed")
            return 0

        # Get migrations to rollback (in reverse order)
        to_rollback = [
            m for m in reversed(self.migrations)
            if current_version >= m.version > target_version
        ]

        logger.warning(f"Rolling back {len(to_rollback)} migrations...")

        rolled_back_count = 0
        for migration in to_rollback:
            if migration.sql_down is None:
                raise RuntimeError(
                    f"Migration {migration.version} has no rollback SQL"
                )

            try:
                self._rollback_migration(migration)
                rolled_back_count += 1
                logger.info(
                    f"✓ Rolled back migration {migration.version}: {migration.name}"
                )
            except Exception as e:
                logger.error(
                    f"✗ Failed to rollback migration {migration.version}: {migration.name}"
                )
                raise RuntimeError(
                    f"Rollback of migration {migration.version} failed: {e}"
                ) from e

        logger.info(f"Successfully rolled back {rolled_back_count} migrations")
        return rolled_back_count

    def _rollback_migration(self, migration: Migration):
        """Rollback a single migration."""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.execute("BEGIN TRANSACTION")
            try:
                cursor = conn.cursor()

                # Execute rollback statements individually (safer than executescript)
                for statement in migration.sql_down.split(';'):
                    statement = statement.strip()
                    if statement and not statement.startswith('--'):
                        cursor.execute(statement)

                # Remove migration record
                cursor.execute(
                    "DELETE FROM schema_migrations WHERE version = ?",
                    (migration.version,)
                )

                conn.commit()
            except Exception as e:
                conn.rollback()
                raise

    def status(self) -> dict:
        """
        Get migration status.

        Returns:
            Dict with current_version, pending_count, applied migrations
        """
        current_version = self.get_current_version()
        pending = self.get_pending_migrations()
        applied = self.get_applied_migrations()

        return {
            'current_version': current_version,
            'latest_version': max(m.version for m in self.migrations),
            'pending_count': len(pending),
            'applied_migrations': applied,
            'pending_migrations': [(m.version, m.name) for m in pending]
        }
