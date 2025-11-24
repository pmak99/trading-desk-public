"""
Database connection pooling for improved concurrent performance.

Implements thread-safe connection pooling with automatic cleanup
and health checks. Prevents connection exhaustion and reduces overhead.
"""

import sqlite3
import logging
import threading
from pathlib import Path
from typing import Optional
from contextlib import contextmanager
from queue import Queue, Empty, Full

logger = logging.getLogger(__name__)


class ConnectionPool:
    """
    Thread-safe SQLite connection pool.

    Maintains a pool of reusable database connections to avoid overhead
    of creating new connections for each query.

    Features:
    - Thread-safe connection checkout/checkin
    - Automatic connection health checks
    - Configurable pool size
    - WAL mode support for concurrent reads
    - Connection timeout handling
    """

    def __init__(
        self,
        db_path: Path,
        pool_size: int = 5,
        max_overflow: int = 10,
        connection_timeout: int = 30,
        pool_timeout: int = 5.0,
    ):
        """
        Initialize connection pool.

        Args:
            db_path: Path to SQLite database
            pool_size: Base pool size (always maintained)
            max_overflow: Additional connections allowed beyond pool_size
            connection_timeout: SQLite connection timeout in seconds
            pool_timeout: Max seconds to wait for available connection
        """
        self.db_path = db_path
        self.pool_size = pool_size
        self.max_overflow = max_overflow
        self.connection_timeout = connection_timeout
        self.pool_timeout = pool_timeout

        # Connection queue (thread-safe)
        self._pool: Queue = Queue(maxsize=pool_size + max_overflow)

        # Track total connections created
        self._total_connections = 0
        self._lock = threading.Lock()

        # Initialize pool with base connections
        self._initialize_pool()

        logger.info(
            f"ConnectionPool initialized: db={db_path}, "
            f"pool_size={pool_size}, max_overflow={max_overflow}"
        )

    def _initialize_pool(self) -> None:
        """Create initial pool connections."""
        for _ in range(self.pool_size):
            conn = self._create_connection()
            if conn:
                self._pool.put(conn, block=False)

    def _create_connection(self) -> Optional[sqlite3.Connection]:
        """
        Create a new database connection.

        Returns:
            New connection or None on error
        """
        try:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=self.connection_timeout,
                check_same_thread=False,  # Allow connection sharing across threads
            )

            # Enable WAL mode for better concurrency
            conn.execute('PRAGMA journal_mode=WAL')

            # Enable foreign keys
            conn.execute('PRAGMA foreign_keys=ON')

            # Row factory for dict-like access
            conn.row_factory = sqlite3.Row

            with self._lock:
                self._total_connections += 1

            logger.debug(
                f"Created connection #{self._total_connections} to {self.db_path}"
            )
            return conn

        except sqlite3.Error as e:
            logger.error(f"Failed to create connection: {e}")
            return None

    def _is_connection_healthy(self, conn: sqlite3.Connection) -> bool:
        """
        Check if connection is still healthy.

        Args:
            conn: Connection to check

        Returns:
            True if healthy, False otherwise
        """
        try:
            # Simple query to verify connection
            conn.execute('SELECT 1').fetchone()
            return True
        except sqlite3.Error:
            return False

    @contextmanager
    def get_connection(self):
        """
        Get a connection from the pool (context manager).

        Automatically returns connection to pool when done.

        Example:
            with pool.get_connection() as conn:
                cursor = conn.execute('SELECT ...')

        Yields:
            Database connection

        Raises:
            TimeoutError: If no connection available within pool_timeout
            RuntimeError: If unable to create connection
        """
        conn = None
        try:
            # Try to get existing connection from pool
            try:
                conn = self._pool.get(timeout=self.pool_timeout)
            except Empty:
                # Pool exhausted - try to create overflow connection
                with self._lock:
                    if self._total_connections < (self.pool_size + self.max_overflow):
                        conn = self._create_connection()
                        if not conn:
                            raise RuntimeError("Failed to create database connection")
                    else:
                        raise TimeoutError(
                            f"Connection pool exhausted (max={self.pool_size + self.max_overflow})"
                        )

            # Health check
            if not self._is_connection_healthy(conn):
                logger.warning("Unhealthy connection detected, creating new one")
                conn.close()
                conn = self._create_connection()
                if not conn:
                    raise RuntimeError("Failed to create replacement connection")

            yield conn

        finally:
            # Return connection to pool
            if conn:
                try:
                    # Commit any pending transaction
                    if conn.in_transaction:
                        conn.commit()

                    # Return to pool if space available
                    try:
                        self._pool.put_nowait(conn)
                    except Full:
                        # Pool full (overflow connection) - close it
                        conn.close()
                        with self._lock:
                            self._total_connections -= 1
                        logger.debug("Closed overflow connection")

                except sqlite3.Error as e:
                    logger.error(f"Error returning connection to pool: {e}")
                    conn.close()
                    with self._lock:
                        self._total_connections -= 1

    def close_all(self) -> None:
        """Close all connections in the pool."""
        closed_count = 0

        # Close all connections in queue
        while True:
            try:
                conn = self._pool.get_nowait()
                conn.close()
                closed_count += 1
            except Empty:
                break

        with self._lock:
            self._total_connections = 0

        logger.info(f"ConnectionPool closed: {closed_count} connections")

    def stats(self) -> dict:
        """
        Get pool statistics.

        Returns:
            Dict with pool stats
        """
        with self._lock:
            return {
                'pool_size': self.pool_size,
                'max_overflow': self.max_overflow,
                'total_connections': self._total_connections,
                'available': self._pool.qsize(),
                'in_use': self._total_connections - self._pool.qsize(),
            }


# Global connection pool (initialized by container)
_global_pool: Optional[ConnectionPool] = None


def get_pool(db_path: Optional[Path] = None) -> ConnectionPool:
    """
    Get global connection pool (singleton pattern).

    Args:
        db_path: Database path (only needed for first call)

    Returns:
        Global ConnectionPool instance

    Raises:
        RuntimeError: If pool not initialized and no db_path provided
    """
    global _global_pool

    if _global_pool is None:
        if db_path is None:
            raise RuntimeError("Connection pool not initialized (provide db_path)")

        _global_pool = ConnectionPool(db_path)

    return _global_pool


def close_global_pool() -> None:
    """Close global connection pool."""
    global _global_pool

    if _global_pool:
        _global_pool.close_all()
        _global_pool = None
