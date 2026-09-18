"""Migration 3 applied to production's shape: bias_predictions' three dead columns.

The repo's own migration file, run by MigrationManager on a schema standing where
production stood at version 2 -- today's baseline table plus the three columns --
with production's schema_migrations rows: the cutover baseline at 1, migration 2
with its checksum. That is what the first apply_migrations() after the deploy does
on the server, without the data (which was rehearsed on a restored copy).
"""

import hashlib

import pytest

from common.db import connect
from common.pgtesting import baseline_ddl
from src.infrastructure.database.migrations import migration_manager as mm
from src.infrastructure.database.migrations.migration_manager import MigrationManager

pytestmark = pytest.mark.pg

DEAD = ("vrp_ratio", "implied_move_pct", "historical_mean_pct")


def _file(version):
    (path,) = mm.MIGRATIONS_DIR.glob(f"{version:03d}_*.sql")
    return path


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def at_version_2(pg_schema):
    two = _file(2)
    with connect() as conn:
        conn.execute(baseline_ddl("bias_predictions"))
        conn.execute("ALTER TABLE bias_predictions ADD COLUMN vrp_ratio double precision,"
                     " ADD COLUMN implied_move_pct double precision,"
                     " ADD COLUMN historical_mean_pct double precision")
        conn.execute("CREATE TABLE schema_migrations (version bigint PRIMARY KEY,"
                     " name text NOT NULL, applied_at timestamptz NOT NULL, checksum text)")
        conn.execute("INSERT INTO schema_migrations VALUES (1, %s, now(), NULL), (2, %s, now(), %s)",
                     (mm.BASELINE_NAME, two.stem[4:], _sha256(two)))
        conn.execute(
            "INSERT INTO bias_predictions (ticker, earnings_date, expiration, stock_price,"
            " predicted_at, skew_atm, skew_curvature, skew_strength, slope_atm,"
            " directional_bias, bias_strength, bias_confidence, r_squared, num_points)"
            " VALUES ('MU', '2026-09-23', '2026-09-26', 150, now(), 0.04, 0.01, 'smirk', 0.1,"
            " 'neutral', 0, 0.5, 0.8, 9)")
        conn.commit()


def _migrate():
    # Up to 3 only: a later file is not this test's business, and this schema
    # holds one table.
    with connect() as conn:
        return MigrationManager(conn).migrate(target_version=3)


def _state():
    with connect() as conn:
        columns = {r.column_name for r in conn.execute(
            "SELECT column_name FROM information_schema.columns"
            " WHERE table_schema = current_schema() AND table_name = 'bias_predictions'")}
        migrations = {r.version: r.checksum for r in conn.execute(
            "SELECT version, checksum FROM schema_migrations")}
        (rows,) = conn.execute("SELECT count(*) FROM bias_predictions").fetchone()
    return columns, migrations, rows


def test_it_drops_the_columns_once_and_records_its_checksum(at_version_2):
    assert _migrate() == 1
    columns, migrations, rows = _state()
    assert not columns & set(DEAD)
    assert {"directional_bias", "fused_bias", "trr_level", "sizing_alarm"} <= columns
    assert migrations == {1: None, 2: _sha256(_file(2)), 3: _sha256(_file(3))}
    assert rows == 1

    assert _migrate() == 0                       # a second start finds nothing pending
    assert _state() == (columns, migrations, rows)


@pytest.mark.parametrize("column", DEAD)
def test_a_value_in_any_of_them_stops_it_and_changes_nothing(at_version_2, column):
    with connect() as conn:
        conn.execute(f"UPDATE bias_predictions SET {column} = 5.2")
        conn.commit()

    with pytest.raises(RuntimeError, match="migration3_dead_columns_empty"):
        _migrate()

    columns, migrations, rows = _state()
    assert set(DEAD) <= columns
    assert sorted(migrations) == [1, 2]
    assert rows == 1
