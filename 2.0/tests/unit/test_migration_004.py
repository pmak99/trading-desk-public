"""Migration 4 applied to production's shape: sentiment_history.size_modifier.

The repo's own migration file, run by MigrationManager on a schema standing where
production stood at version 3 -- today's baseline table plus the column -- with
production's schema_migrations rows: the cutover baseline at 1, migrations 2 and 3
with their checksums. That is what the first apply_migrations() after the deploy
does on the server, without the data (which was rehearsed on a restored copy).

The column held the removed contrarian sizing's multiplier. The migration file
records the 17 rows that held anything but 1.0, and its guard refuses the drop
if the column holds a value that record does not name.
"""

import hashlib
import re

import pytest

from common.db import connect
from common.pgtesting import baseline_ddl
from src.infrastructure.database.migrations import migration_manager as mm
from src.infrastructure.database.migrations.migration_manager import (
    MigrationManager,
    _statements,
)

pytestmark = pytest.mark.pg

COLUMN = "size_modifier"
GUARD = "migration4_size_modifier_recorded"


def _file(version):
    (path,) = mm.MIGRATIONS_DIR.glob(f"{version:03d}_*.sql")
    return path


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_file_is_its_guard_and_the_drop_and_records_17_rows():
    guard, drop = _statements(_file(4).read_text())
    assert guard.startswith(f"ALTER TABLE sentiment_history ADD CONSTRAINT {GUARD}")
    assert drop == f"ALTER TABLE sentiment_history DROP COLUMN {COLUMN}"
    recorded = re.findall(r"\('([A-Z]+)', '(\d{4}-\d\d-\d\d)', (0\.8|1\.2)\)", guard)
    assert len(set(recorded)) == 17


def _columns(conn):
    return [r.column_name for r in conn.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = current_schema() AND table_name = 'sentiment_history'"
        " ORDER BY ordinal_position")]


@pytest.fixture
def at_version_3(pg_schema):
    """Returns the baseline's columns, in order."""
    two, three = _file(2), _file(3)
    with connect() as conn:
        conn.execute(baseline_ddl("sentiment_history"))
        baseline = _columns(conn)
        conn.execute(f"ALTER TABLE sentiment_history ADD COLUMN {COLUMN} double precision DEFAULT 1.0")
        conn.execute("CREATE TABLE schema_migrations (version bigint PRIMARY KEY,"
                     " name text NOT NULL, applied_at timestamptz NOT NULL, checksum text)")
        conn.execute("INSERT INTO schema_migrations VALUES (1, %s, now(), NULL),"
                     " (2, %s, now(), %s), (3, %s, now(), %s)",
                     (mm.BASELINE_NAME, two.stem[4:], _sha256(two), three.stem[4:], _sha256(three)))
        # One row at the default and one of the 17 the file records.
        conn.execute(
            "INSERT INTO sentiment_history (ticker, earnings_date, collected_at, source,"
            f" sentiment_text, sentiment_score, sentiment_direction, {COLUMN}) VALUES"
            " ('NVDA', '2026-08-26', now(), 'perplexity', 'beat expected', 0.3, 'bullish', DEFAULT),"
            " ('LULU', '2025-12-11', '2025-12-08 08:22:45-05', 'perplexity', 'weak guide', -0.6,"
            " 'bearish', 1.2)")
        conn.commit()
    return baseline


def _migrate():
    # Up to 4 only: a later file is not this test's business, and this schema
    # holds one table.
    with connect() as conn:
        return MigrationManager(conn).migrate(target_version=4)


def _state():
    with connect() as conn:
        columns = _columns(conn)
        migrations = {r.version: r.checksum for r in conn.execute(
            "SELECT version, checksum FROM schema_migrations")}
        rows = [tuple(r) for r in conn.execute(
            "SELECT ticker, earnings_date, collected_at, sentiment_score, sentiment_direction"
            " FROM sentiment_history ORDER BY ticker")]
        constraints = {r.conname for r in conn.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'sentiment_history'::regclass")}
    return columns, migrations, rows, constraints


def test_it_drops_the_column_once_and_records_its_checksum(at_version_3):
    _, _, rows_before, constraints_before = _state()

    assert _migrate() == 1
    columns, migrations, rows, constraints = _state()
    assert columns == at_version_3                     # the baseline's table, column for column
    assert COLUMN not in columns
    assert migrations == {1: None, 2: _sha256(_file(2)), 3: _sha256(_file(3)), 4: _sha256(_file(4))}
    assert rows == rows_before
    assert constraints == constraints_before           # the guard went with the column

    assert _migrate() == 0                             # a second start finds nothing pending
    assert _state() == (columns, migrations, rows, constraints)


@pytest.mark.parametrize("ticker, value", [
    ("NVDA", 0.8),     # a value on a row the file does not record
    ("LULU", 0.8),     # a recorded row holding a value other than its recorded one
])
def test_a_value_the_file_does_not_record_stops_it_and_changes_nothing(at_version_3, ticker, value):
    with connect() as conn:
        conn.execute(f"UPDATE sentiment_history SET {COLUMN} = %s WHERE ticker = %s", (value, ticker))
        conn.commit()
    _, _, rows_before, constraints_before = _state()

    with pytest.raises(RuntimeError, match=GUARD):
        _migrate()

    columns, migrations, rows, constraints = _state()
    assert COLUMN in columns
    assert sorted(migrations) == [1, 2, 3]
    assert rows == rows_before
    assert constraints == constraints_before
