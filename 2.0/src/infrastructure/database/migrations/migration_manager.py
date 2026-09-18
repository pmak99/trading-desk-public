"""
Database migration system.

Versioned, repeatable schema changes tracked in `schema_migrations`.

**The 21 SQLite migration definitions that used to live here are gone.** They
described the incremental path from an empty 2025 database to schema v21, and
`2.0/sql/baseline_schema.sql` — generated from the v21 database itself — is
where that path ended. Replaying them against Postgres would be both
impossible (SQLite DDL) and pointless (the destination already exists). The
baseline was recorded as version 1 of the new lineage on 2026-09-12; migration
2 onward is for changes made *after* the cutover. Migration SQL lives in
`2.0/sql/migrations/NNN_name.sql`, one file per version.

**Who migrates.** `apply_migrations()` below, called once by each entry point
that writes through the engine (the Telegram bot at start, morning_digest,
record_moves --execute, scan.py, analyze.py, store_bias_prediction.py), and
weekly-maintenance.sh's own step right after its `git pull`. Until 2026-09-14
`Container.__init__` migrated, so building one anywhere -- a test, the Finnhub
MCP server, `trade.sh health` -- applied pending migrations to whatever
PGDATABASE named and wrote `schema_migrations`; constructing a Container now
touches no database (tests/unit/test_container_startup.py).

⚠️ **`BASELINE_VERSION` is the version `baseline_schema.sql` embodies, not
"1".** The baseline file is kept equal to the CURRENT schema, because every
test fixture is built from it. A database built from it is therefore already at
the latest version and must be seeded as such: seeding it at 1 would make this
manager replay migration 2 onto a schema that already has it, and fail. The
same rule keeps `migrate()` working against an EMPTY database (CI): the table
is seeded at the latest version and nothing is pending. Production, seeded at 1
on 2026-09-12, is the database that applies migrations 2 to 6.

When you add migration N: write `2.0/sql/migrations/NNN_name.sql`, make the same
change to `baseline_schema.sql`, and set BASELINE_VERSION = N — in one commit.

⚠️ **A migration file is immutable once applied.** `migrate()` compares the
sha256 of every file with the checksum stored when it was applied, and a
difference raises before anything else runs — which, in `apply_migrations()`,
stops the bot starting (it Telegrams each distinct error once) and fails a job before its work (run_job Telegrams it). The fix is to restore the file and write a new
migration, never to edit history. Rows applied before checksums were kept
(2026-09-14: production's migration 2) have NULL, and the first `migrate()`
after that date records the file's current checksum instead of raising
(`_verify_checksums`). A seeded baseline row has no file of its own and is
never compared.

⚠️ **Two processes can migrate at once** — the Monday maintenance run and a bot
restart, or two `./trade.sh` runs. Each migration is applied in a transaction
that first takes `pg_advisory_xact_lock(MIGRATION_LOCK_KEY)` and then re-checks
that the version is still unapplied, so the loser waits, finds the work done,
and skips it. Until 2026-09-14 nothing serialised them.

Further consequences worth knowing:

- `MigrationManager` takes a **connection**, not a path. The SQLite version
  opened `sqlite3.connect(self.db_path)` in four separate methods, each its own
  transaction. A caller still passing a Path now fails on `.execute` instead of
  quietly creating a database file — the failure mode CLAUDE.md calls out as
  SQLite-specific and one of the quieter wins of this migration. The connection
  must not be in autocommit: the lock and the migration share a transaction.
- There is no explicit `BEGIN TRANSACTION`. Postgres opens one implicitly on
  the first statement; every statement of one migration, and its
  schema_migrations row, commit together or not at all.
- **There is no rollback.** Down migrations were never loaded from the files,
  so `rollback()` could only raise; it was removed on 2026-09-14. A bad
  migration is undone by a new migration, rehearsed on a restored copy.

Usage:
    apply_migrations()                    # an entry point, before its work

    with session() as conn:               # weekly-maintenance.sh
        MigrationManager(conn).migrate()
"""

import hashlib
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

from psycopg import sql as pgsql

logger = logging.getLogger(__name__)

# The version 2.0/sql/baseline_schema.sql embodies -- see the module docstring.
# Version 1 was the cutover baseline (the whole SQLite lineage in one row);
# the file has since absorbed migrations 2 to 6.
BASELINE_VERSION = 7
BASELINE_NAME = "baseline_postgres_2026_09"

MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "sql" / "migrations"

# The advisory-lock key every migrator takes. Fixed, because two processes must
# agree on it without talking: the ASCII bytes of "ivcrush" plus a zero byte,
# read as a bigint. Advisory locks are per database, and nothing else in the
# repo takes one; a future user must pick a different key.
MIGRATION_LOCK_KEY = 0x6976637275736800

# statement_timeout for the migration's own transaction, overriding the role's
# (the tracked role grants). The lock wait counts against it -- a second
# migrator waits for the first one's whole migration -- and DDL can queue behind
# an idle-in-transaction session for up to that role's 15-minute
# idle_in_transaction_session_timeout, after which the session is killed and the
# DDL proceeds. So: past 15 minutes, but still a bound, not a hang.
MIGRATION_STATEMENT_TIMEOUT = "30min"


@dataclass
class Migration:
    """
    Represents a single database migration.

    Attributes:
        version: Sequential version number, taken from the file's NNN prefix.
        name: Descriptive name, the rest of the file name.
        sql_up: SQL statements to apply the migration.
        checksum: sha256 of the file's bytes, stored when it is applied.

    Notes:
        - Version numbers must be unique
        - Statements are split on semicolons: none may appear inside a
          statement (full-line `--` comments are removed before splitting)
        - Migrations are applied in version order
    """
    version: int
    name: str
    sql_up: str
    checksum: str


class MigrationManager:
    """
    Manages database schema migrations.

    Features:
    - Version tracking in schema_migrations table
    - Sequential migration application, one transaction per migration
    - Idempotent (safe to run multiple times, and from two processes at once)
    - Applied files are checksummed; an edited one fails loudly

    Usage:
        with get_pool().connection() as conn:
            MigrationManager(conn).migrate()
    """

    def __init__(self, conn):
        """
        Initialize migration manager.

        Args:
            conn: An open psycopg connection, not in autocommit.
        """
        if getattr(conn, "autocommit", False):
            raise ValueError(
                "MigrationManager needs a transactional connection: in autocommit "
                "the advisory lock would be released before the migration runs"
            )
        self.conn = conn
        self.migrations: List[Migration] = []
        self._load_migrations()

    def _load_migrations(self):
        """Load migration definitions from 2.0/sql/migrations/NNN_name.sql.

        A duplicate version number is a packaging error and raises here,
        before anything touches the database.
        """
        for path in sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql")):
            raw = path.read_bytes()
            self.migrations.append(Migration(
                version=int(path.name[:3]),
                name=path.stem[4:],
                sql_up=raw.decode("utf-8"),
                checksum=hashlib.sha256(raw).hexdigest(),
            ))

        self.migrations.sort(key=lambda m: m.version)
        versions = [m.version for m in self.migrations]
        if len(versions) != len(set(versions)):
            raise RuntimeError(f"duplicate migration versions: {versions}")

    def _begin_locked(self):
        """Start this transaction's work holding the migration lock.

        The timeout override comes first because the lock wait is itself
        subject to statement_timeout. Under READ COMMITTED (the pool's and
        common.db's level) every statement after the lock sees what the
        previous holder committed, which is what the callers' re-checks rely on.
        """
        self.conn.execute(
            "SELECT set_config('statement_timeout', %s, true)",
            (MIGRATION_STATEMENT_TIMEOUT,),
        )
        self.conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_KEY,))

    def _ensure_migrations_table(self):
        """Ensure schema_migrations exists and records the baseline.

        The baseline row is seeded only when the table is empty, at the version
        baseline_schema.sql embodies. The one-time loader deliberately skipped
        `schema_migrations` — carrying the 21 SQLite rows across would have made
        this manager believe v1-21 of a lineage that no longer exists had been
        applied.

        Under the lock, because two processes starting against an empty
        database would otherwise race CREATE TABLE IF NOT EXISTS (it is not
        concurrency-safe) and the seed insert.
        """
        try:
            self._begin_locked()
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version bigint PRIMARY KEY,
                    name text NOT NULL,
                    applied_at timestamptz NOT NULL,
                    checksum text
                )
            """)
            # now(), not now()::text: applied_at is timestamptz since migration 2,
            # and a text expression is not assignable to it. now() is assignable
            # to either type, so this also works on a pre-migration-2 table.
            self.conn.execute(
                """
                INSERT INTO schema_migrations (version, name, applied_at)
                SELECT %s, %s, now()
                WHERE NOT EXISTS (SELECT 1 FROM schema_migrations)
                """,
                (BASELINE_VERSION, BASELINE_NAME),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _verify_checksums(self):
        """Compare every applied migration with its file; fail on a difference.

        ⚠️ **This writes.** A row with a NULL checksum -- every migration applied
        before 2026-09-14 -- gets the file's current checksum, once, logged at
        INFO. In production that is migration 2, written by whichever process
        first calls `migrate()` after the deploy: the weekly maintenance step,
        a bot restart, or any entry point's `apply_migrations()`. Recording the file as it is then is
        the best available evidence; it was not edited between 2026-09-13 and
        this change (git log of 002_schema_audit_2026_09.sql).

        Skipped: rows with no file (version 1, the cutover baseline) and a
        seeded baseline row, which records that the schema was BUILT from
        baseline_schema.sql, not that the file of that version was applied.
        """
        files = {m.version: m for m in self.migrations}
        try:
            self._begin_locked()
            rows = self.conn.execute(
                "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
            ).fetchall()
            changed = []
            for version, name, checksum in rows:
                migration = files.get(version)
                if migration is None or name == BASELINE_NAME:
                    continue
                if name != migration.name:
                    changed.append(
                        f"migration {version} ({name}) changed after it was applied: "
                        f"the file is now named {migration.name!r}"
                    )
                elif checksum is None:
                    self.conn.execute(
                        "UPDATE schema_migrations SET checksum = %s"
                        " WHERE version = %s AND checksum IS NULL",
                        (migration.checksum, version),
                    )
                    logger.info(
                        f"Recorded checksum for migration {version} ({name}), "
                        f"applied before checksums were kept: {migration.checksum}"
                    )
                elif checksum != migration.checksum:
                    changed.append(
                        f"migration {version} ({name}) changed after it was applied: "
                        f"stored sha256 {checksum}, file {migration.checksum}"
                    )
            if changed:
                raise RuntimeError(
                    "; ".join(changed)
                    + ". An applied migration must never be edited: restore the file "
                    "(git log -p 2.0/sql/migrations/) and put the change in a new migration."
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def get_current_version(self) -> int:
        """
        Get current schema version.

        Returns:
            Current version number, or 0 if no migrations applied
        """
        self._ensure_migrations_table()
        result = self.conn.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()
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
        self._ensure_migrations_table()
        return self.conn.execute(
            "SELECT version, name, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()

    def migrate(self, target_version: int | None = None) -> int:
        """
        Verify applied migrations, then apply all pending ones up to target.

        Args:
            target_version: Version to migrate to (None = latest)

        Returns:
            Number of migrations THIS call applied. One another process applied
            while this one waited for the lock is skipped and not counted.

        Raises:
            RuntimeError: If an applied migration's file changed, or a
                migration fails
        """
        self._ensure_migrations_table()
        self._verify_checksums()
        pending = self.get_pending_migrations()

        if target_version is not None:
            pending = [m for m in pending if m.version <= target_version]

        if not pending:
            logger.debug(
                f"Database at version {self.get_current_version()}, "
                f"no migrations needed"
            )
            return 0

        logger.info(f"Applying {len(pending)} migration(s)...")

        applied_count = 0
        for migration in pending:
            try:
                applied = self._apply_migration(migration)
            except Exception as e:
                logger.error(
                    f"✗ Failed to apply migration {migration.version}: {migration.name}"
                )
                logger.error(f"Error: {e}")
                raise RuntimeError(
                    f"Migration {migration.version} failed: {e}"
                ) from e
            if applied:
                applied_count += 1
                logger.info(
                    f"✓ Applied migration {migration.version}: {migration.name}"
                )
            else:
                logger.info(
                    f"○ Migration {migration.version} ({migration.name}) was applied "
                    f"by another process while this one waited"
                )

        if applied_count:
            self._analyze()
        logger.info(f"Successfully applied {applied_count} migrations")
        return applied_count

    def _apply_migration(self, migration: Migration) -> bool:
        """
        Apply a single migration within a transaction, holding the lock.

        Returns:
            True if applied here; False if it was already applied when the
            lock was acquired.

        Raises:
            Exception: If migration fails (transaction rolled back)
        """
        try:
            # End the getters' read transaction so the lock opens this one.
            self.conn.commit()
            self._begin_locked()
            if self.conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = %s",
                (migration.version,),
            ).fetchone():
                self.conn.rollback()
                return False

            for statement in _statements(migration.sql_up):
                self.conn.execute(statement)

            self.conn.execute(
                """
                INSERT INTO schema_migrations (version, name, applied_at, checksum)
                VALUES (%s, %s, now(), %s)
                """,
                (migration.version, migration.name, migration.checksum),
            )
            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            raise

    def _analyze(self):
        """Refresh planner statistics after a migration changed the schema.

        ANALYZE, not VACUUM (ANALYZE). An ALTER COLUMN TYPE rewrite discards the
        column's statistics and a new index or table starts with none, so the
        first queries after a migration can plan badly -- that is the immediate
        cost, and ANALYZE alone fixes it. Dead tuples a migration's UPDATEs leave
        behind are autovacuum's job, on its schedule. VACUUM would also need
        autocommit, which means flipping it on a connection the caller may hand
        back to the shared pool.

        Only tables in the current schema: `public` in production, and the
        throwaway schema in a test, where a bare ANALYZE would walk every other
        test's leftovers in the database. A failure here is logged, not raised:
        the migrations are already committed and correct, and autovacuum
        analyzes on its own.
        """
        try:
            self.conn.execute(
                "SELECT set_config('statement_timeout', %s, true)",
                (MIGRATION_STATEMENT_TIMEOUT,),
            )
            tables = self.conn.execute(
                "SELECT schemaname, tablename FROM pg_tables"
                " WHERE schemaname = current_schema() ORDER BY tablename"
            ).fetchall()
            for schema, table in tables:
                self.conn.execute(
                    pgsql.SQL("ANALYZE {}").format(pgsql.Identifier(schema, table))
                )
            self.conn.commit()
            logger.info(f"Analyzed {len(tables)} tables after migrating")
        except Exception as e:
            self.conn.rollback()
            logger.warning(f"ANALYZE after migrating failed (autovacuum will catch up): {e}")

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
            'latest_version': max(
                (m.version for m in self.migrations), default=BASELINE_VERSION
            ),
            'pending_count': len(pending),
            'applied_migrations': applied,
            'pending_migrations': [(m.version, m.name) for m in pending]
        }


def apply_migrations() -> int:
    """Verify every applied migration, then apply pending ones, on the PG* database.

    The one migration call an entry point makes, before any other database work
    (see "Who migrates" above). Idempotent and cheap when nothing is pending --
    three short transactions under the advisory lock -- and it always checks
    the stored checksums, so an edited migration file stops the caller here.

    Returns the number of migrations this call applied.

    Raises:
        RuntimeError: an applied migration's file changed, a migration failed,
            or the database is unreachable. Logged at ERROR first: a process
            that dies here must say why in its own log (the bot's journal and
            one Telegram per distinct error, a job's log and its run_job Telegram).
    """
    from src.infrastructure.database.pg_pool import get_pool

    try:
        with get_pool().connection() as conn:
            manager = MigrationManager(conn)
            applied = manager.migrate()
            version = manager.get_current_version()
    except Exception as e:
        logger.error(f"Database migration check failed, refusing to start: {e}")
        raise RuntimeError(f"Failed to apply database migrations: {e}") from e

    if applied:
        logger.info(f"Database migrations complete: {applied} applied (version: {version})")
    else:
        logger.debug(f"Database up to date (version: {version})")
    return applied


def _statements(sql: str) -> List[str]:
    """Split multi-statement migration SQL into executable statements.

    ⚠️ Full-line `--` comments are removed BEFORE splitting. The previous
    version split first and then dropped any chunk that *started* with `--`,
    which silently discarded every statement preceded by a comment line -- and
    a comment containing a semicolon cut the statement after it in half. Nothing
    exercised it until the first real migration file, which is commented.
    """
    without_comments = "\n".join(
        line for line in sql.splitlines() if not re.match(r"^\s*--", line)
    )
    return [s.strip() for s in without_comments.split(";") if s.strip()]
