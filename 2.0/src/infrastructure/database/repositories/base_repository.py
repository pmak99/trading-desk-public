"""
Base repository with common database patterns.

Provides:
- Pooled Postgres connections
- Standardized error handling
- A DELETE helper (`_execute_delete`, EarningsRepository's)

All repositories inherit from BaseRepository to ensure consistent connection
handling and error management.

Two things changed at the Postgres cutover and both are load-bearing:

- **There is no direct-connection fallback any more.** The SQLite version fell
  back to `sqlite3.connect(self.db_path)` when no pool was passed, which was
  only safe because a missing SQLite file is a valid thing to create. There is
  no equivalent that fails safely here, and there is no path to fall back to:
  repositories take a pool, and default to the process-wide one.
- **`except psycopg.Error` is not cosmetic.** `psycopg.Error` does not inherit
  `sqlite3.Error`, so leaving the old handlers in place would have stopped them
  catching anything and turned every `Result`/`Err(AppError)` return into a raw
  exception escaping the repository.
"""

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Optional

import psycopg

from src.domain.errors import AppError, Err, ErrorCode, Ok, Result
from src.infrastructure.database.pg_pool import get_pool

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)


class BaseRepository:
    """
    Base class for Postgres repositories. Not abstract: it declares no method a
    subclass must supply (until 2026-09-14 it was an ABC with no abstract methods).

    Provides common functionality:
    - Pooled connection management
    - Context manager for safe connection handling
    - Standardized error wrapping

    Usage:
        class MyRepository(BaseRepository):
            def get_item(self, id: int) -> Result[Item, AppError]:
                try:
                    with self._get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT id, name FROM items WHERE id = %s", (id,))
                        row = cursor.fetchone()
                        if not row:
                            return Err(AppError(ErrorCode.NODATA, "Item not found"))
                        return Ok(Item.from_row(row))
                except psycopg.Error as e:
                    return self._db_error(e, "get item")
    """

    def __init__(self, pool: Optional['ConnectionPool'] = None):
        """
        Initialize repository.

        Args:
            pool: Connection pool to use. Defaults to the process-wide pool,
                resolved lazily so constructing a repository never connects.
        """
        self._pool = pool

    @property
    def pool(self) -> 'ConnectionPool':
        if self._pool is None:
            self._pool = get_pool()
        return self._pool

    @contextmanager
    def _get_connection(self):
        """
        Check a connection out of the pool.

        The pool's own context manager commits on clean exit and rolls back on
        exception, so an uncommitted write is committed rather than discarded —
        the opposite of the SQLite pool's behaviour. Every writer below still
        commits explicitly; do not rely on the implicit commit.

        Yields:
            psycopg.Connection
        """
        with self.pool.connection() as conn:
            yield conn

    def _db_error(self, error: Exception, operation: str) -> Err:
        """
        Create standardized database error result.

        Args:
            error: Original database error
            operation: Description of the failed operation

        Returns:
            Err result with AppError containing database error code
        """
        message = f"Database error during {operation}: {error}"
        logger.error(message)
        return Err(AppError(ErrorCode.DBERROR, str(error)))

    # `_no_data_error`, `_execute_query`, `_execute_insert`, `_execute_batch` and
    # `_execute_batch_insert` were removed 2026-09-14: no repository called them,
    # only their own tests. Repositories write through `_get_connection()`.

    def _execute_delete(
        self,
        query: str,
        params: tuple = (),
        operation: str = "delete",
    ) -> Result[int, AppError]:
        """
        Execute DELETE with rowcount return.

        Args:
            query: SQL DELETE query
            params: Query parameters
            operation: Description for error messages

        Returns:
            Result with count of deleted rows or AppError
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                deleted_count = cursor.rowcount
                conn.commit()

            logger.info(f"Deleted {deleted_count} rows ({operation})")
            return Ok(deleted_count)

        except psycopg.Error as e:
            return self._db_error(e, operation)


class ResilientRepository(BaseRepository):
    """
    Repository base with failure counting and circuit breaker pattern.

    Use for non-critical logging/analytics where failures shouldn't
    break the main application flow.

    Features:
    - Tracks consecutive failures
    - Logs critical alert after threshold
    - Silent degradation (doesn't raise exceptions)
    """

    def __init__(
        self,
        pool: Optional['ConnectionPool'] = None,
        max_failures: int = 10,
    ):
        """
        Initialize resilient repository.

        Args:
            pool: Connection pool (defaults to the process-wide pool)
            max_failures: Consecutive failures before critical alert
        """
        super().__init__(pool)
        self.failure_count = 0
        self.max_failures = max_failures

    def _record_success(self) -> None:
        """Reset failure counter on successful operation."""
        self.failure_count = 0

    def _record_failure(self, error: Exception, operation: str) -> None:
        """
        Record failure and log appropriately.

        Args:
            error: The exception that occurred
            operation: Description of the failed operation
        """
        self.failure_count += 1

        if self.failure_count >= self.max_failures:
            logger.critical(
                f"Database {operation} has failed {self.failure_count} consecutive times! "
                f"Last error: {error}. Database may be unavailable or corrupted - investigate immediately!"
            )
        else:
            logger.error(
                f"Failed to {operation} ({self.failure_count}/{self.max_failures}): {error}"
            )

    @property
    def is_healthy(self) -> bool:
        """Check if repository is operating normally."""
        return self.failure_count < self.max_failures
