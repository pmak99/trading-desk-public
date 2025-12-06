"""
Base repository with common database patterns.

Provides:
- Connection pooling support with fallback
- Standardized error handling
- Common query helpers

All SQLite repositories should inherit from BaseRepository to ensure
consistent connection handling and error management.
"""

import sqlite3
import logging
from abc import ABC
from contextlib import contextmanager
from pathlib import Path
from typing import Optional, List, Any

from src.domain.errors import Result, AppError, Ok, Err, ErrorCode

# TYPE_CHECKING import to avoid circular dependency
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.infrastructure.database.connection_pool import ConnectionPool

logger = logging.getLogger(__name__)

# Default connection timeout for all database operations (30 seconds)
DEFAULT_CONNECTION_TIMEOUT = 30


class BaseRepository(ABC):
    """
    Abstract base class for SQLite repositories.

    Provides common functionality:
    - Connection management (pooled or direct)
    - Context manager for safe connection handling
    - Standardized error wrapping

    Usage:
        class MyRepository(BaseRepository):
            def get_item(self, id: int) -> Result[Item, AppError]:
                try:
                    with self._get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT * FROM items WHERE id = ?", (id,))
                        row = cursor.fetchone()
                        if not row:
                            return Err(AppError(ErrorCode.NODATA, "Item not found"))
                        return Ok(Item.from_row(row))
                except sqlite3.Error as e:
                    return self._db_error(e, "get item")
    """

    def __init__(
        self,
        db_path: str | Path,
        pool: Optional['ConnectionPool'] = None,
        timeout: int = DEFAULT_CONNECTION_TIMEOUT,
    ):
        """
        Initialize repository.

        Args:
            db_path: Path to SQLite database
            pool: Optional connection pool (uses direct connections if None)
            timeout: Connection timeout in seconds (default: 30)
        """
        self.db_path = str(db_path)
        self.pool = pool
        self.timeout = timeout

    @contextmanager
    def _get_connection(self):
        """
        Get database connection from pool or create direct connection.

        Prefers connection pool if available, falls back to direct
        connection for testing or simple deployments.

        Yields:
            sqlite3.Connection: Database connection

        Usage:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(...)
        """
        if self.pool:
            # Use connection pool (preferred for production)
            with self.pool.get_connection() as conn:
                yield conn
        else:
            # Fallback to direct connection (testing/development)
            conn = sqlite3.connect(self.db_path, timeout=self.timeout)
            try:
                yield conn
            finally:
                conn.close()

    def _db_error(self, error: sqlite3.Error, operation: str) -> Err:
        """
        Create standardized database error result.

        Args:
            error: Original SQLite error
            operation: Description of the failed operation

        Returns:
            Err result with AppError containing database error code
        """
        message = f"Database error during {operation}: {error}"
        logger.error(message)
        return Err(AppError(ErrorCode.DBERROR, str(error)))

    def _no_data_error(self, message: str) -> Err:
        """
        Create standardized no-data error result.

        Args:
            message: Error message describing what data is missing

        Returns:
            Err result with AppError containing NODATA error code
        """
        return Err(AppError(ErrorCode.NODATA, message))

    def _execute_query(
        self,
        query: str,
        params: tuple = (),
        fetch_one: bool = False,
        fetch_all: bool = False,
    ) -> Result[Any, AppError]:
        """
        Execute a query with standardized error handling.

        Args:
            query: SQL query string
            params: Query parameters
            fetch_one: If True, return single row
            fetch_all: If True, return all rows

        Returns:
            Result with query result or AppError
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)

                if fetch_one:
                    return Ok(cursor.fetchone())
                elif fetch_all:
                    return Ok(cursor.fetchall())
                else:
                    conn.commit()
                    return Ok(cursor.rowcount)

        except sqlite3.Error as e:
            return self._db_error(e, "query execution")

    def _execute_insert(
        self,
        query: str,
        params: tuple,
        operation: str = "insert",
    ) -> Result[None, AppError]:
        """
        Execute an INSERT or UPDATE with standardized error handling.

        Args:
            query: SQL INSERT/UPDATE query
            params: Query parameters
            operation: Description for error messages

        Returns:
            Result with None on success or AppError on failure
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                conn.commit()
            return Ok(None)

        except sqlite3.Error as e:
            return self._db_error(e, operation)

    def _execute_batch(
        self,
        query: str,
        params_list: List[tuple],
        operation: str = "batch insert",
    ) -> Result[int, AppError]:
        """
        Execute batch INSERT with executemany.

        Args:
            query: SQL INSERT query
            params_list: List of parameter tuples
            operation: Description for error messages

        Returns:
            Result with count of affected rows or AppError
        """
        if not params_list:
            return Ok(0)

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.executemany(query, params_list)
                conn.commit()

            logger.info(f"Batch {operation}: {len(params_list)} rows")
            return Ok(len(params_list))

        except sqlite3.Error as e:
            return self._db_error(e, operation)

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

        except sqlite3.Error as e:
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
        db_path: str | Path,
        pool: Optional['ConnectionPool'] = None,
        max_failures: int = 10,
    ):
        """
        Initialize resilient repository.

        Args:
            db_path: Path to SQLite database
            pool: Optional connection pool
            max_failures: Consecutive failures before critical alert
        """
        super().__init__(db_path, pool)
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
