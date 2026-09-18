"""MigrationManager's file loading and statement splitting.

No database: these are the two pure functions every migration passes through,
and the splitter had a silent-drop bug that nothing exercised until the first
real (commented) migration file existed.
"""

from src.infrastructure.database.migrations.migration_manager import (
    BASELINE_VERSION,
    MIGRATIONS_DIR,
    MigrationManager,
    _statements,
)


def test_a_statement_after_a_comment_line_is_kept():
    """The old splitter dropped any chunk that STARTED with --, i.e. every
    statement written under a comment."""
    sql = "-- why this index\nCREATE INDEX a ON t (x);\n-- and this one\nDROP INDEX b;"
    assert _statements(sql) == ["CREATE INDEX a ON t (x)", "DROP INDEX b"]


def test_a_semicolon_inside_a_comment_does_not_split_a_statement():
    sql = "ALTER TABLE t\n-- note; not a terminator\n  ADD COLUMN c int;"
    assert _statements(sql) == ["ALTER TABLE t\n  ADD COLUMN c int"]


def test_blank_and_comment_only_input_yields_nothing():
    assert _statements("\n-- only a comment;\n\n") == []


def _loaded():
    manager = MigrationManager.__new__(MigrationManager)
    manager.migrations = []
    manager._load_migrations()
    return manager.migrations


def test_migration_files_load_with_their_versions():
    migrations = _loaded()
    assert [m.version for m in migrations] == sorted({m.version for m in migrations})
    assert migrations[0].version == 2
    assert migrations[0].name == "schema_audit_2026_09"


def test_migration_2_splits_into_its_46_statements():
    """Pinned so a comment or a stray semicolon that changes how the file
    splits is caught before it reaches a database."""
    (m2,) = [m for m in _loaded() if m.version == 2]
    statements = _statements(m2.sql_up)
    assert len(statements) == 46
    assert statements[0] == "DROP INDEX idx_journal_earnings"
    assert statements[-1].startswith("UPDATE earnings_calendar SET created_at")


def test_migration_3_splits_into_its_2_statements():
    (m3,) = [m for m in _loaded() if m.version == 3]
    assert m3.name == "bias_predictions_dead_columns"
    guard, drop = _statements(m3.sql_up)
    assert guard.startswith("ALTER TABLE bias_predictions ADD CONSTRAINT migration3_dead_columns_empty")
    assert drop.startswith("ALTER TABLE bias_predictions\n    DROP COLUMN vrp_ratio,")


def test_migration_5_is_the_positions_view_as_the_baseline_writes_it():
    """One statement, and the same view text as baseline_schema.sql, so a fresh
    database and a migrated one read identical positions."""
    import re

    (m5,) = [m for m in _loaded() if m.version == 5]
    assert m5.name == "positions_view"
    (view,) = _statements(m5.sql_up)
    assert view.startswith('CREATE VIEW "positions" AS')
    baseline = (MIGRATIONS_DIR.parent / "baseline_schema.sql").read_text(encoding="utf-8")
    match = re.search(r'CREATE VIEW "positions" AS.*?;', baseline, re.S)
    assert match and match.group(0).rstrip(";") == view

def test_baseline_version_is_the_latest_migration():
    assert BASELINE_VERSION == max(m.version for m in _loaded())
    assert MIGRATIONS_DIR.name == "migrations"
