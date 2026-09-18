"""One-shot Postgres connections for standalone scripts.

There are deliberately two ways to reach the database, and the split is by
process shape, not by tree:

- **`2.0/src/infrastructure/database/pg_pool.py`** — a pooled, liveness-checked
  singleton. For the engine and anything long-running: the always-on Telegram
  bot, `ConcurrentScanner`'s 5 workers, `/harvest`'s 10. Repositories take it.
- **This module** — a plain connection per invocation. For CLI scripts that
  start, do one job, and exit. A pool inside a process that makes one query and
  dies is pure overhead, and `min_size=2` would open a second connection it
  never uses.

Both read the same PG* environment variables through libpq, so
`PGDATABASE=ivcrush_scratch <anything>` works uniformly. Neither ever takes a
path: a wrong DSN fails to connect, where a wrong SQLite path silently created
an empty database (CLAUDE.md's `DB_PATH` warning — that whole failure mode is
gone).

`namedtuple_row` matches the pool, so a row behaves the same either way:
`row[0]` and `row.col`, never `row['col']`, and `row._asdict()` rather than
`dict(row)`.
"""

from contextlib import contextmanager

import psycopg
from psycopg.rows import namedtuple_row
from psycopg.types.numeric import FloatLoader

# Same rationale as the pool: libpq's default is no timeout, which turns an
# unreachable the server into a hang rather than an error.
CONNECT_TIMEOUT_SECONDS = 10


def configure_types(conn: psycopg.Connection) -> None:
    """Load `numeric` columns as Python float, on this connection.

    ⚠️ Money is numeric(14,2) since migration 2 (2026-09-13): stored exactly, to
    the cent, where double precision had put float residue into 117 of 316
    strategies.gain_loss values. psycopg's default loader would hand those
    columns to Python as `Decimal`, and every `float * Decimal` in the P&L,
    Sharpe and journal code would raise TypeError. So the exactness lives in the
    database -- storage, SUM(), ROUND() -- and Python keeps the float arithmetic
    it has always done. Also applies to `quantity`, which was already numeric.

    Called by `connect()` here, by the engine pool (`configure=`), and by the
    test pool, so a row reads the same whichever way it was fetched.
    """
    conn.adapters.register_loader("numeric", FloatLoader)


def connect(*, autocommit: bool = False) -> psycopg.Connection:
    """Open a connection using the PG* environment.

    Caller owns closing it; prefer `session()` where a `with` block fits.
    """
    conn = psycopg.connect(
        row_factory=namedtuple_row,
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        autocommit=autocommit,
    )
    configure_types(conn)
    return conn


@contextmanager
def session(*, autocommit: bool = False):
    """Connection as a context manager, committed on clean exit.

    `psycopg.Connection.__exit__` commits and then CLOSES, which is what a
    one-shot script wants and what the pool's context manager deliberately does
    not do (it returns the connection instead).
    """
    with connect(autocommit=autocommit) as conn:
        yield conn


def target() -> str:
    """Human-readable target for log lines. Never renders a password."""
    import os
    return (f"{os.getenv('PGUSER', 'trading')}@{os.getenv('PGHOST', 'db.internal')}"
            f":{os.getenv('PGPORT', '5432')}/{os.getenv('PGDATABASE', 'ivcrush')}")


# ---------------------------------------------------------------------------
# Type boundary
# ---------------------------------------------------------------------------

def as_date(value):
    """Coerce a date-ish value to `datetime.date`, or None if unusable.

    ⚠️ **This exists because of a defect class, not for tidiness.** Postgres
    returns `datetime.date` for a date column where SQLite returned a string,
    so every `datetime.strptime(row[i], "%Y-%m-%d")` left over from the SQLite
    era raises `TypeError: strptime() argument 1 must be str`. Nine such sites
    were found across the port, and most were wrapped in `except ValueError`,
    which does **not** catch TypeError — so they crash rather than degrade.
    Two of them (`journal/vrp_correlation`, `journal/models`) caught TypeError
    and returned None instead, which is worse: a silent wrong answer.

    Accepts a `date`, a `datetime`, or an ISO string, because both still reach
    this code — the CSV/journal path hands over strings, the database hands
    over objects.
    """
    from datetime import date as _date
    from datetime import datetime as _datetime

    if value is None:
        return None
    if isinstance(value, _datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    try:
        return _date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None
