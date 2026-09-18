"""Migration 6 applied to production's shape at version 5, then compared with the baseline."""

import hashlib

import pytest

from common.db import connect
from common.pgtesting import baseline_ddl
from src.infrastructure.database.migrations import migration_manager as mm
from src.infrastructure.database.migrations.migration_manager import (
    MigrationManager,
    _statements,
)

pytestmark = pytest.mark.pg

V5_ANALYSIS_LOG_RENAMES = (("vrp_intraday_ratio", "vrp_ratio"),
                           ("historical_intraday_mean_pct", "historical_mean_pct"))


def _file(version):
    (path,) = mm.MIGRATIONS_DIR.glob(f"{version:03d}_*.sql")
    return path


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _shape(conn, table):
    cols = [(r.column_name, r.is_nullable, r.data_type) for r in conn.execute(
        "SELECT column_name, is_nullable, data_type FROM information_schema.columns"
        " WHERE table_schema = current_schema() AND table_name = %s ORDER BY ordinal_position",
        (table,))]
    checks = sorted(r.def_ for r in conn.execute(
        "SELECT pg_get_constraintdef(c.oid) AS def_ FROM pg_constraint c"
        " JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace"
        " WHERE n.nspname = current_schema() AND t.relname = %s AND c.contype = 'c'", (table,)))
    return cols, checks


def test_the_file_splits_into_its_12_statements():
    assert len(_statements(_file(6).read_text())) == 12


def test_production_at_version_5_migrates_to_the_baseline_shape(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("analysis_log", "sentiment_history"))
        target = {t: _shape(conn, t) for t in ("analysis_log", "sentiment_history")}
        # Undo migration 6 on the baseline-built tables: the version-5 shape.
        conn.execute("ALTER TABLE analysis_log DROP CONSTRAINT analysis_log_graded_ratio_present")
        conn.execute("ALTER TABLE analysis_log DROP CONSTRAINT analysis_log_vrp_convention_check")
        conn.execute("ALTER TABLE analysis_log DROP COLUMN vrp_convention")
        for new, old in V5_ANALYSIS_LOG_RENAMES:
            conn.execute(f"ALTER TABLE analysis_log RENAME COLUMN {new} TO {old}")
            conn.execute(f"ALTER TABLE analysis_log ALTER COLUMN {old} SET NOT NULL")
        conn.execute("ALTER TABLE sentiment_history DROP CONSTRAINT sentiment_history_vrp_convention_with_ratio")
        conn.execute("ALTER TABLE sentiment_history DROP CONSTRAINT sentiment_history_vrp_convention_check")
        conn.execute("ALTER TABLE sentiment_history DROP COLUMN vrp_convention")
        conn.execute("CREATE TABLE schema_migrations (version bigint PRIMARY KEY,"
                     " name text NOT NULL, applied_at timestamptz NOT NULL, checksum text)")
        conn.execute("INSERT INTO schema_migrations VALUES (1, %s, now(), NULL)", (mm.BASELINE_NAME,))
        for v in (2, 3, 4, 5):
            f = _file(v)
            conn.execute("INSERT INTO schema_migrations VALUES (%s, %s, now(), %s)",
                         (v, f.stem[4:], _sha256(f)))
        conn.execute("INSERT INTO analysis_log (timestamp, ticker, earnings_date, expiration,"
                     " implied_move_pct, historical_mean_pct, vrp_ratio, recommendation)"
                     " VALUES (now(), 'T', '2026-07-01', '2026-07-02', 6.0, 2.0, 3.0, 'excellent')")
        conn.execute("INSERT INTO sentiment_history (ticker, earnings_date, collected_at, source,"
                     " sentiment_text, vrp_ratio) VALUES ('T', '2026-07-01', now(), 'p', 'x', 3.0),"
                     " ('U', '2026-07-01', now(), 'p', 'x', NULL)")
        conn.commit()

        MigrationManager(conn).migrate(target_version=6)
        conn.commit()

        for table, (cols, checks) in target.items():
            got_cols, got_checks = _shape(conn, table)
            assert got_cols == cols, table
            assert got_checks == checks, table
        assert conn.execute("SELECT vrp_convention, vrp_intraday_ratio FROM analysis_log").fetchone() == ("intraday", 3.0)
        assert sorted(conn.execute("SELECT ticker, vrp_convention FROM sentiment_history").fetchall()) == [("T", "intraday"), ("U", None)]
