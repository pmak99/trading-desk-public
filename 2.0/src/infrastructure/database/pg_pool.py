"""psycopg_pool wrapper. Replaces connection_pool.py.

Three things here are deliberate, and two of them contradict what the
migration plan originally asserted:

1. **The API is `connection()` / `close()`, not `get_connection()` /
   `close_all()`.** Those were the *SQLite* pool's names. Four live call
   sites used the old ones and every one of them is updated in this commit.

2. **`namedtuple_row` does NOT support `row['col']`** -- verified on
   psycopg 3.3.5, it raises `TypeError: tuple indices must be integers`.
   It is still the right factory, because positional `row[0]` access is by
   far the more common pattern here and `dict_row` would break all of it;
   but every name-subscript site has to become attribute access, and
   `dict(row)` has to become `row._asdict()`. Two known hazards it carries:
   a duplicate output column name raises `DataError` (so no `SELECT a.*,
   b.*` over tables sharing a column), and an unnamed expression becomes
   `f_column_` rather than failing.

3. **`check=` is not optional here.** `trading-telegram-bot.service` is
   `Type=simple`/`Restart=always` and holds this pool for days across the
   LAN to the server. Without a liveness check the pool hands out a
   server-closed connection and the next query fails. The SQLite pool this
   replaces had the same guard (`_is_connection_healthy`).

Sizing: max_size=12 because ConcurrentScanner runs max_workers=5
(concurrent_scanner.py) and harvest_mode.py runs its own
ThreadPoolExecutor(max_workers=10). Sizing off the first pool alone gives
8 and hits PoolTimeout under /harvest.
"""

import logging
import os
import threading
from dataclasses import dataclass, field

from psycopg.rows import namedtuple_row
from psycopg_pool import ConnectionPool

# One definition of the type config.
from common.db import configure_types  # noqa: E402

logger = logging.getLogger(__name__)

# Wait this long for a free pooled connection before raising PoolTimeout.
POOL_TIMEOUT_SECONDS = 30.0
# Wait this long for the TCP/auth handshake. libpq's default is no timeout,
# which turns an unreachable the server into a hang instead of an error.
CONNECT_TIMEOUT_SECONDS = 10

MIN_SIZE = 2
MAX_SIZE = 12

_pool: ConnectionPool | None = None
_lock = threading.Lock()


@dataclass(frozen=True)
class PostgresConfig:
    """Reporting/validation view of the PG* environment.

    The pool itself passes `conninfo=""` and lets libpq read the same
    variables at connect time, so `PGDATABASE=ivcrush_scratch ./trade.sh ...`
    works without this class being involved. Fields use default_factory so
    they reflect the environment at construction, not at import.
    """

    host: str = field(default_factory=lambda: os.getenv("PGHOST", "db.internal"))
    port: int = field(default_factory=lambda: int(os.getenv("PGPORT", "5432")))
    dbname: str = field(default_factory=lambda: os.getenv("PGDATABASE", "ivcrush"))
    user: str = field(default_factory=lambda: os.getenv("PGUSER", "trading"))
    password: str | None = field(default_factory=lambda: os.getenv("PGPASSWORD"))

    @property
    def is_configured(self) -> bool:
        return bool(self.host and self.dbname and self.user and self.password)

    def __str__(self) -> str:
        """Never render the password."""
        return f"{self.user}@{self.host}:{self.port}/{self.dbname}"


def get_pool() -> ConnectionPool:
    """Get the process-wide connection pool, creating it on first use."""
    global _pool
    if _pool is None:
        with _lock:                       # double-checked: 10 harvest workers
            if _pool is None:             # can race here
                cfg = PostgresConfig()
                _pool = ConnectionPool(
                    conninfo="",          # empty -> libpq reads PG* from env
                    min_size=MIN_SIZE,
                    max_size=MAX_SIZE,
                    timeout=POOL_TIMEOUT_SECONDS,
                    check=ConnectionPool.check_connection,
                    # numeric -> float, identical to common.db.connect(); see
                    # configure_types for why money is not handed out as Decimal.
                    configure=configure_types,
                    kwargs={
                        "row_factory": namedtuple_row,
                        "connect_timeout": CONNECT_TIMEOUT_SECONDS,
                    },
                    name="ivcrush",
                    open=True,
                )
                logger.debug(f"Opened Postgres pool: {cfg} (max_size={MAX_SIZE})")
    return _pool


def close_pool() -> None:
    """Close the pool. Safe to call when no pool was ever opened."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
        logger.debug("Closed Postgres pool")
