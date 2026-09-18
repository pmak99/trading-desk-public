"""MigrationManager.migrate() against real Postgres.

Until 2026-09-14 no test called `.migrate()` on a database, and three defects
sat in it unseen: the `checksum` column was never written, so an edited
already-applied migration was undetectable; nothing serialised two migrators, so
the Monday maintenance run and a bot restart could both apply the same pending
migration; and `rollback()` could only ever raise, because no down SQL was ever
loaded.

Every test runs in a throwaway schema with its own migrations directory, so the
versions here (NEXT and NEXT + 1, above whatever the baseline embodies) are
fixtures, not the repo's migrations.
"""

import hashlib
import threading

import pytest

from common.db import connect
from src.infrastructure.database.migrations import migration_manager as mm
from src.infrastructure.database.migrations.migration_manager import (
    BASELINE_VERSION,
    MigrationManager,
)

pytestmark = pytest.mark.pg

# The fixtures' first version: a database built from the baseline is seeded at
# BASELINE_VERSION, so anything at or below it would never be pending.
NEXT = BASELINE_VERSION + 1


@pytest.fixture
def migrations_dir(pg_schema, tmp_path, monkeypatch):
    """An empty migrations directory the manager reads instead of the repo's."""
    monkeypatch.setattr(mm, "MIGRATIONS_DIR", tmp_path)
    return tmp_path


def _write(directory, version, name, sql):
    path = directory / f"{version:03d}_{name}.sql"
    path.write_text(sql, encoding="utf-8")
    return path


def _rows():
    with connect() as conn:
        return conn.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        ).fetchall()


def _migrate():
    with connect() as conn:
        return MigrationManager(conn).migrate()


def test_applying_twice_is_a_no_op(migrations_dir):
    _write(migrations_dir, NEXT, "widgets", "CREATE TABLE widget (id int);")
    _write(migrations_dir, NEXT + 1, "gadgets", "CREATE TABLE gadget (id int);")

    assert _migrate() == 2
    first = _rows()
    # CREATE TABLE is not idempotent: a replay would raise, not return 0.
    assert _migrate() == 0
    assert _rows() == first
    assert [r.version for r in first] == [BASELINE_VERSION, NEXT, NEXT + 1]


def test_applying_records_the_files_sha256(migrations_dir):
    path = _write(migrations_dir, NEXT, "widgets", "-- a widget\nCREATE TABLE widget (id int);\n")

    _migrate()

    stored = {r.version: r.checksum for r in _rows()}
    assert stored[NEXT] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_an_edited_applied_migration_fails_loudly_and_applies_nothing(migrations_dir):
    path = _write(migrations_dir, NEXT, "widgets", "CREATE TABLE widget (id int);")
    _migrate()

    path.write_text("CREATE TABLE widget (id bigint);", encoding="utf-8")
    _write(migrations_dir, NEXT + 1, "gadgets", "CREATE TABLE gadget (id int);")

    with pytest.raises(RuntimeError, match=rf"migration {NEXT} \(widgets\).*changed"):
        _migrate()
    # The check runs before anything pending is applied.
    assert [r.version for r in _rows()] == [BASELINE_VERSION, NEXT]


def test_a_null_checksum_is_recorded_not_rejected(migrations_dir):
    """Every row applied before checksums were kept -- production's migration 2
    among them -- has NULL. The first migrate() records the file's checksum
    rather than treating the gap as a mismatch, and does not re-apply it."""
    path = _write(migrations_dir, NEXT, "widgets", "CREATE TABLE widget (id int);")
    with connect() as conn:
        MigrationManager(conn).get_current_version()      # creates + seeds the table
        conn.execute("CREATE TABLE widget (id int)")
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at)"
            " VALUES (%s, 'widgets', now())", (NEXT,)
        )
        conn.commit()

    assert _migrate() == 0

    stored = {r.version: r.checksum for r in _rows()}
    assert stored[NEXT] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert stored[BASELINE_VERSION] is None     # a seeded baseline has no file


def test_analyze_runs_after_a_migration_is_applied(migrations_dir):
    _write(migrations_dir, NEXT, "widgets",
           "CREATE TABLE widget (id int); INSERT INTO widget SELECT generate_series(1, 50);")

    _migrate()

    with connect() as conn:
        (analyzed,) = conn.execute(
            "SELECT last_analyze FROM pg_stat_user_tables"
            " WHERE schemaname = current_schema() AND relname = 'widget'"
        ).fetchone()
    assert analyzed is not None


def test_two_concurrent_migrators_apply_a_migration_once(migrations_dir, monkeypatch):
    """The Monday maintenance run and a bot restart, both finding the same
    migration pending. Each computes its pending list, then they race to apply it.

    Without the advisory lock both run the DDL at once; without the re-check
    after the lock, the second replays CREATE TABLE and raises. Either way a
    thread fails. pg_sleep widens the window the second one waits in."""
    _write(migrations_dir, NEXT, "widgets",
           "SELECT pg_sleep(0.5); CREATE TABLE widget (id int);")
    with connect() as conn:
        MigrationManager(conn).get_current_version()      # seed before racing
        conn.commit()

    both_see_it_pending = threading.Barrier(2, timeout=20)
    original = MigrationManager.get_pending_migrations

    def pending_then_wait(self):
        pending = original(self)
        both_see_it_pending.wait()
        return pending

    monkeypatch.setattr(MigrationManager, "get_pending_migrations", pending_then_wait)

    results, errors = [], []

    def migrator():
        try:
            results.append(_migrate())
        except Exception as exc:                        # reported below
            errors.append(exc)

    threads = [threading.Thread(target=migrator) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == []
    assert sorted(results) == [0, 1]
    assert [r.version for r in _rows()] == [BASELINE_VERSION, NEXT]
