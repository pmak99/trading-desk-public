"""Dialect and driver facts the port depends on, asserted against a real server.

Everything here was measured, not assumed. Three of these contradict what the
migration plan asserted before it was executed -- see the docstrings. Marked
`pg` so a machine with no Postgres skips rather than fails.
"""
import pytest

psycopg = pytest.importorskip("psycopg")
pytestmark = pytest.mark.pg


@pytest.fixture
def pg():
    # The test database, not PGDATABASE: these probe dialect behaviour and have
    # no business opening a session in production.
    from common.pgtesting import connect_test_database
    try:
        conn = connect_test_database(psycopg)
    except Exception as exc:
        pytest.skip(f"no Postgres available: {exc}")
    yield conn
    conn.close()


# --------------------------------------------------------------------------
# Dialect
# --------------------------------------------------------------------------

def test_date_minus_date_is_integer_days(pg):
    """The nearest-date joins subtract two DATE columns -- integer days.
    This is the case the conversion table gets right."""
    got = pg.execute("SELECT '2026-03-01'::date - '2026-02-01'::date").fetchone()[0]
    assert got == 28 and isinstance(got, int)


def test_timestamp_minus_timestamp_is_interval_NOT_integer(pg):
    """...and this is the case it gets wrong. julianday(a)-julianday(b) -> a-b
    yields INTERVAL for timestamp columns, so `> %s` against an int raises
    'operator does not exist: interval > integer'. Live at
    sync_earnings_calendar.py and queries.sql."""
    got = pg.execute("SELECT '2026-03-01'::timestamp - '2026-02-01'::timestamp").fetchone()[0]
    assert not isinstance(got, int)
    with pytest.raises(psycopg.errors.UndefinedFunction):
        pg.execute("SELECT '2026-03-01'::timestamp - '2026-02-01'::timestamp > 5")
    pg.rollback()


def test_days_between_timestamps_uses_epoch(pg):
    """The correct replacement for a timestamp operand."""
    got = pg.execute(
        "SELECT EXTRACT(EPOCH FROM ('2026-03-01'::timestamptz "
        "- '2026-02-01'::timestamptz))/86400"
    ).fetchone()[0]
    assert int(got) == 28


def test_integer_literal_into_boolean_is_rejected(pg):
    """earnings_repository wrote literal 1 into `confirmed`, which the baseline
    declares boolean. Postgres has no implicit int->bool cast."""
    pg.execute("CREATE TEMP TABLE b (flag boolean)")
    with pytest.raises(psycopg.errors.DatatypeMismatch):
        pg.execute("INSERT INTO b (flag) VALUES (1)")
    pg.rollback()


def test_iso_date_string_parameter_binds_to_a_date_column(pg):
    """The most suspicious-looking thing in the port, and it is safe:
    psycopg sends str as unknown-OID, so Postgres infers date from context."""
    pg.execute("CREATE TEMP TABLE d (day date)")
    pg.execute("INSERT INTO d (day) VALUES (%s)", ("2026-03-01",))
    assert pg.execute("SELECT COUNT(*) FROM d WHERE day = %s",
                      ("2026-03-01",)).fetchone()[0] == 1
    pg.rollback()


def test_interval_offset_from_an_integer_parameter(pg):
    """date('now','+N days') has no direct equivalent -- an integer cannot be
    concatenated into an interval implicitly. This is the form that works and
    is what earnings_repository/prices_repository now use."""
    got = pg.execute(
        "SELECT DATE '2026-03-01' + (%s || ' days')::interval", (5,)
    ).fetchone()[0]
    assert got.date().isoformat() == "2026-03-06"


# --------------------------------------------------------------------------
# Driver: the row factory. These three are why the port touches every
# name-subscript call site rather than only the SQL.
# --------------------------------------------------------------------------

def test_namedtuple_row_rejects_string_subscript(pg):
    """⚠️ The plan claimed namedtuple_row 'preserves both row["col"] and
    row[0]'. It does not -- string subscript raises. Positional and attribute
    access both work, which is why it is still the right choice, but every
    row['x'] site had to become row.x."""
    from psycopg.rows import namedtuple_row
    pg.row_factory = namedtuple_row
    row = pg.execute("SELECT 1 AS last_updated").fetchone()
    assert row[0] == 1
    assert row.last_updated == 1
    with pytest.raises(TypeError):
        row["last_updated"]


def test_dict_of_a_namedtuple_row_fails_use_asdict(pg):
    """analysis_repository returned `[dict(row) for row in rows]`. Under
    namedtuple_row that raises TypeError; `row._asdict()` is the replacement."""
    from psycopg.rows import namedtuple_row
    pg.row_factory = namedtuple_row
    row = pg.execute("SELECT 1 AS a, 2 AS b").fetchone()
    with pytest.raises(TypeError):
        dict(row)
    assert row._asdict() == {"a": 1, "b": 2}


def test_duplicate_output_column_names_raise(pg):
    """A namedtuple cannot carry two fields of the same name, so `SELECT a.*,
    b.*` over tables sharing a column is a runtime DataError, not a silently
    shadowed column. Worth knowing before writing a join."""
    from psycopg.rows import namedtuple_row
    pg.row_factory = namedtuple_row
    with pytest.raises(psycopg.DataError):
        pg.execute("SELECT 1 AS dup, 2 AS dup").fetchone()


def test_unnamed_expression_becomes_f_column(pg):
    """The benign half of the same rule: an unnamed expression is renamed
    rather than raising, so positional access still works."""
    from psycopg.rows import namedtuple_row
    pg.row_factory = namedtuple_row
    row = pg.execute("SELECT '2026-03-01'::date - '2026-02-01'::date").fetchone()
    assert row[0] == 28
    assert row._fields == ("f_column_",)


# --------------------------------------------------------------------------
# Driver: error hierarchy
# --------------------------------------------------------------------------

def test_psycopg_error_is_not_a_sqlite3_error():
    """The reason all 22 `except sqlite3.Error` blocks had to change rather
    than being left as harmless dead code: they would stop catching, and every
    repository's Result/Err(AppError) contract would break into a raw raise."""
    import sqlite3
    assert not issubclass(psycopg.Error, sqlite3.Error)


def test_a_test_cannot_apply_a_migration_to_the_production_database():
    from common.pgtesting import refuse_production_migration

    class Conn:
        def __init__(self, name):
            self.name = name

        def execute(self, sql):
            name = self.name
            return type("Row", (), {"fetchone": lambda _self: (name,)})()

    with pytest.raises(RuntimeError, match="apply a migration to 'ivcrush'"):
        refuse_production_migration(Conn("ivcrush"), production="ivcrush")
    refuse_production_migration(Conn("ivcrush_test"), production="ivcrush")   # a test database is fine
