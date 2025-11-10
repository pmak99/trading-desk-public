"""
SQLite base class for thread-safe database access.

Provides common functionality for SQLite-backed trackers:
- Thread-local connection management
- WAL mode for concurrent access
- Context manager support
- Connection pooling and cleanup

Used by UsageTrackerSQLite and IVHistoryTracker to eliminate code duplication.
"""

# Standard library imports
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class SQLiteBase:
    """
    Base class for thread-safe SQLite database access.

    Features:
    - Thread-local connections (one per thread)
    - WAL mode for concurrent reads/writes
    - Automatic connection cleanup
    - Context manager support

    Example:
        class MyTracker(SQLiteBase):
            def __init__(self, db_path="data/my_tracker.db"):
                super().__init__(db_path)
                self._init_database()

            def _init_database(self):
                conn = self._get_connection()
                conn.execute("CREATE TABLE IF NOT EXISTS my_table ...")
                conn.commit()
    """

    def __init__(self, db_path: str, timeout: float = 30.0):
        """
        Initialize SQLite base.

        Args:
            db_path: Path to SQLite database file
            timeout: Connection timeout in seconds (default: 30.0)
        """
        self.db_path = Path(db_path)
        self.timeout = timeout

        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Thread-local storage for connections
        self._local = threading.local()

        logger.debug(f"Initialized SQLiteBase for {self.db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """
        Get thread-local database connection.

        Returns:
            Thread-local SQLite connection with WAL mode enabled

        Notes:
            - Creates new connection if one doesn't exist for this thread
            - Enables WAL mode for concurrent access
            - Sets row_factory to sqlite3.Row for dict-like access
        """
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=self.timeout
            )

            # Enable WAL mode for concurrent access
            # WAL = Write-Ahead Logging, allows multiple readers + 1 writer
            self._local.conn.execute("PRAGMA journal_mode=WAL")

            # Set busy timeout (in milliseconds)
            self._local.conn.execute(f"PRAGMA busy_timeout={int(self.timeout * 1000)}")

            # Enable dict-like row access
            self._local.conn.row_factory = sqlite3.Row

            logger.debug(f"Created new connection for thread {threading.current_thread().name}")

        return self._local.conn

    def close(self):
        """
        Close database connection for current thread.

        Safe to call multiple times. Handles exceptions gracefully.
        """
        if hasattr(self._local, 'conn') and self._local.conn:
            try:
                self._local.conn.close()
                logger.debug(f"Closed connection for thread {threading.current_thread().name}")
            except Exception as e:
                logger.debug(f"Error closing connection: {e}")
            finally:
                self._local.conn = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures connection is closed."""
        self.close()
        return False

    def execute_query(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        """
        Execute a query and return cursor.

        Args:
            query: SQL query string
            params: Query parameters (default: empty tuple)

        Returns:
            Cursor with query results

        Example:
            cursor = self.execute_query(
                "SELECT * FROM users WHERE name = ?",
                ("Alice",)
            )
            for row in cursor:
                print(row['name'])
        """
        conn = self._get_connection()
        return conn.execute(query, params)

    def execute_and_commit(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        """
        Execute a query and commit transaction.

        Args:
            query: SQL query string
            params: Query parameters (default: empty tuple)

        Returns:
            Cursor with query results

        Example:
            self.execute_and_commit(
                "INSERT INTO users (name) VALUES (?)",
                ("Alice",)
            )
        """
        conn = self._get_connection()
        cursor = conn.execute(query, params)
        conn.commit()
        return cursor

    def begin_transaction(self):
        """Begin an immediate transaction."""
        conn = self._get_connection()
        conn.execute("BEGIN IMMEDIATE")

    def commit(self):
        """Commit current transaction."""
        conn = self._get_connection()
        conn.commit()

    def rollback(self):
        """Rollback current transaction."""
        conn = self._get_connection()
        conn.rollback()
