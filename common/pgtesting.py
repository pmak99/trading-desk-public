"""Shared Postgres test fixtures.

Defined once here and re-exported from both conftests, because the two suites
run with different rootdirs (`cd 2.0 && pytest tests/` vs `pytest
scripts/tests/` from the repo root), so neither can see the other's conftest.
A single definition beats two that drift.

Import into a conftest with:

    from common.pgtesting import pg_pool, pg_schema  # noqa: F401
"""

import os
import uuid

import pytest

# ⚠️ Tests never run in the production database. Until 2026-09-13 this fixture
# connected with the ambient PG* environment -- which on the Mac and on the server is
# 2.0/.env, i.e. PGDATABASE=ivcrush -- so every pg-marked test created and
# dropped its schema INSIDE PRODUCTION. One day of local runs put ~220k inserts
# and deletes through prod's pg_attribute and ~560 MB through its WAL, and the
# lock-timeout path below terminated every idle-in-transaction session in that
# database, which in production is the Telegram bot. A dedicated database makes
# both impossible: nothing a test does can name a production object.
TEST_DATABASE_ENV = "PGTEST_DATABASE"
DEFAULT_TEST_DATABASE = "ivcrush_test"


def pgtest_database() -> str:
    """Name of the database tests build their throwaway schemas in.

    Refuses to return the ambient PGDATABASE: pointing PGTEST_DATABASE at
    production must fail loudly, not quietly restore the old behaviour.
    """
    name = os.environ.get(TEST_DATABASE_ENV, DEFAULT_TEST_DATABASE)
    if name == os.environ.get("PGDATABASE"):
        raise RuntimeError(
            f"{TEST_DATABASE_ENV}={name!r} is the same database as PGDATABASE. "
            "Tests must not run in the database the application uses."
        )
    return name


def connect_test_database(psycopg, **kwargs):
    """Connect to the test database, creating it on first use.

    Created on demand so CI (a fresh server) and a new machine need no setup
    step. CREATE DATABASE needs CREATEDB, which the application role holds for
    exactly this and for the monthly restore-verify.
    """
    from psycopg import sql

    name = pgtest_database()
    try:
        return psycopg.connect(dbname=name, **kwargs)
    except psycopg.OperationalError as exc:
        if "does not exist" not in str(exc):
            raise
    with psycopg.connect(dbname="postgres", autocommit=True) as admin:
        try:
            admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        except psycopg.errors.DuplicateDatabase:
            pass                                  # a parallel run won the race
    return psycopg.connect(dbname=name, **kwargs)


@pytest.fixture
def pg_schema(monkeypatch):
    """A throwaway Postgres schema that every connection in the test lands in.

    The Postgres equivalent of the `tmp_path` SQLite databases these tests used
    to build. Setting `PGOPTIONS` is what makes it work for scripts under test:
    they call `common.db.connect()` themselves, and libpq reads that variable,
    so their connections get the same `search_path` without any of them having
    to be monkeypatched individually.

    The schema lives in the test database (`pgtest_database()`), never the one
    PGDATABASE names, and PGDATABASE is repointed for the test's duration so a
    script under test that calls `common.db.connect()` lands there too. Tables
    can be created freely and nothing can reach production; the schema is
    dropped afterwards.
    """
    psycopg = pytest.importorskip("psycopg")

    try:
        admin = connect_test_database(psycopg)
    except RuntimeError:
        raise
    except Exception as exc:                      # pragma: no cover - env dependent
        pytest.skip(f"no Postgres available: {exc}")

    schema = f"t_{uuid.uuid4().hex[:12]}"
    admin.execute(f'CREATE SCHEMA "{schema}"')
    admin.commit()
    monkeypatch.setenv("PGDATABASE", pgtest_database())
    monkeypatch.setenv("PGOPTIONS", f"-csearch_path={schema}")
    try:
        yield schema
    finally:
        # ⚠️ A test that raises between connect() and close() leaves an open
        # transaction holding locks, and DROP SCHEMA CASCADE then blocks
        # FOREVER -- the whole suite hangs instead of reporting the failure
        # that caused it. Hit for real while porting. So: bound the wait, and
        # if something is still holding on, terminate it and say so. A test
        # harness must fail loudly, never hang.
        admin.execute("SET lock_timeout = '10s'")
        try:
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
            admin.commit()
        except psycopg.errors.LockNotAvailable as locked:
            admin.rollback()
            killed = admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity"
                " WHERE pid <> pg_backend_pid() AND datname = current_database()"
                "   AND state = 'idle in transaction'"
            ).fetchall()
            admin.commit()
            admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
            admin.commit()
            raise AssertionError(
                f"schema {schema} was locked by {len(killed)} leaked connection(s) "
                "-- a test left one open, probably by raising between connect() "
                "and close(). Use `with connect() as conn:`."
            ) from locked
        finally:
            admin.close()


@pytest.fixture
def pg_pool(pg_schema):
    """A connection pool bound to the throwaway schema.

    Deliberately NOT the process-wide `get_pool()` — a test must not be able
    to write through the same pool production code holds.
    """
    pytest.importorskip("psycopg")
    from psycopg.rows import namedtuple_row
    from psycopg_pool import ConnectionPool

    from common.db import configure_types

    pool = ConnectionPool(
        conninfo="", min_size=1, max_size=2, timeout=10.0,
        # PGDATABASE, not pgtest_database(): pg_schema has already pointed it at
        # the test database, and the guard in pgtest_database() would read that
        # as "tests are about to run in the application database" and refuse.
        kwargs={"row_factory": namedtuple_row,
                "dbname": os.environ["PGDATABASE"],
                "options": f"-csearch_path={pg_schema}"},
        configure=configure_types,
        open=True,
    )
    try:
        yield pool
    finally:
        pool.close()


# ---------------------------------------------------------------------------
# Building fixture tables FROM the generated baseline schema.
# ---------------------------------------------------------------------------

BASELINE_SQL = (
    __import__("pathlib").Path(__file__).resolve().parents[1]
    / "2.0" / "sql" / "baseline_schema.sql"
)


def baseline_ddl(*tables: str) -> str:
    """The CREATE TABLE / CREATE INDEX / CREATE VIEW statements for `tables`, verbatim.

    Fixtures build their tables from `2.0/sql/baseline_schema.sql` rather than
    transcribing a copy. Transcription is exactly how the first migration plan
    invented an `id` column that does not exist and dropped two that live code
    writes; a fixture that drifts from the real schema tests the wrong thing
    while looking green.
    """
    import re

    # Strip comments BEFORE splitting on ";" -- a comment containing a
    # semicolon (there is one) otherwise cuts a statement in half and the
    # CREATE it documents becomes unfindable.
    sql = "\n".join(
        line for line in BASELINE_SQL.read_text().splitlines()
        if not line.strip().startswith("--")
    )
    wanted = set(tables)
    out = []
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        m = re.match(r'CREATE (?:UNIQUE )?(TABLE|INDEX|VIEW)\s+"?([\w.]+)"?', stmt, re.I)
        if not m:
            continue
        if m.group(1).upper() in ("TABLE", "VIEW"):
            if m.group(2) in wanted:
                out.append(stmt)
        else:
            on = re.search(r"\bON\s+\"?(\w+)\"?", stmt, re.I)
            if on and on.group(1) in wanted:
                out.append(stmt)
    missing = wanted - {
        re.match(r'CREATE (?:TABLE|VIEW)\s+"?(\w+)"?', s, re.I).group(1)
        for s in out if s.upper().startswith(("CREATE TABLE", "CREATE VIEW"))
    }
    if missing:
        raise AssertionError(f"not in baseline_schema.sql: {sorted(missing)}")
    return ";\n".join(out) + ";"


# --- no migration reaches the production database from a test -----------------
# 2026-09-14: a test ran an entry point that migrates at start while PGDATABASE
# named production, and applied migration 3 there mid-suite. The fixture that
# installs this check lives beside the migration manager
# (src/infrastructure/database/migrations/production_guard.py): common imports
# nothing from src.

PRODUCTION_DATABASE = os.environ.get("PGDATABASE")   # read before any fixture repoints it


def refuse_production_migration(conn, production=PRODUCTION_DATABASE) -> None:
    current = conn.execute("SELECT current_database()").fetchone()[0]
    if production and current == production:
        raise RuntimeError(
            f"a test tried to apply a migration to {current!r} (PGDATABASE). Patch the "
            "entry point's migration call in the test, or use the pg_schema database.")
