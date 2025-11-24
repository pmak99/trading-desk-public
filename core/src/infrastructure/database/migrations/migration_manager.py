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
                else:
                    # Standard SQL execution
                    cursor.executescript(migration.sql_up)

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

                # Execute rollback SQL
                cursor.executescript(migration.sql_down)

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
