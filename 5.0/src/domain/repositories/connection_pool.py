"""SQLite connection pool and input validation shared by all repositories."""

import atexit
import re
import sqlite3
import threading
from queue import Queue, Empty
from contextlib import contextmanager
from typing import Dict

from src.core.logging import log

# Input validation patterns - allow BRK.B, BF.A style tickers
TICKER_PATTERN = re.compile(r'^[A-Z]{1,5}(\.[A-Z]{1,2})?$')
DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def _normalize_ticker(ticker: str) -> str:
    """
    Validate and normalize ticker symbol (internal use).

    For public ticker validation, use src.domain.ticker module which
    provides validate_ticker() (boolean check) and normalize_ticker()
    (with alias resolution).
    """
    ticker = ticker.upper().strip()
    if not TICKER_PATTERN.match(ticker):
        raise ValueError(f"Invalid ticker format: {ticker}")
    return ticker


def is_valid_ticker(ticker: str) -> bool:
    """
    Check if ticker format is valid without raising exception.

    Filters out preferred stocks (COF-PI), warrants (ACHR+), units (SPAC.U),
    and other non-standard tickers that don't have options.

    Returns:
        True if ticker is valid for options analysis, False otherwise.
    """
    if not ticker:
        return False
    ticker = ticker.upper().strip()
    return bool(TICKER_PATTERN.match(ticker))


def validate_date(date_str: str) -> str:
    """Validate date string format."""
    if not DATE_PATTERN.match(date_str):
        raise ValueError(f"Invalid date format: {date_str} (expected YYYY-MM-DD)")
    return date_str


def validate_limit(limit: int) -> int:
    """Validate limit parameter."""
    if not (1 <= limit <= 100):
        raise ValueError(f"Invalid limit: {limit} (must be 1-100)")
    return limit


def validate_days(days: int) -> int:
    """Validate days parameter for date range queries."""
    if not (1 <= days <= 365):
        raise ValueError(f"Invalid days: {days} (must be 1-365)")
    return days


class ConnectionPool:
    """Simple SQLite connection pool for better performance."""

    def __init__(self, db_path: str, max_connections: int = 15):
        self.db_path = db_path
        self._pool: Queue = Queue(maxsize=max_connections)
        self._max = max_connections
        self._created = 0
        self._lock = threading.Lock()  # Protect _created counter

    def _create_connection(self) -> sqlite3.Connection:
        """Create a new connection."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        # CRITICAL: Enable foreign key constraints
        conn.execute('PRAGMA foreign_keys=ON')
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def get_connection(self):
        """Get a connection from the pool."""
        conn = None
        try:
            # Try to get from pool
            try:
                conn = self._pool.get_nowait()
            except Empty:
                # Create new if under limit (thread-safe check)
                with self._lock:
                    if self._created < self._max:
                        conn = self._create_connection()
                        self._created += 1
                # If we didn't create one, wait for pool
                if conn is None:
                    conn = self._pool.get(timeout=30)
            yield conn
        finally:
            if conn:
                # Rollback any uncommitted state before returning to pool.
                # Without this, a connection used for a write that didn't
                # commit (e.g. exception path) holds a RESERVED lock, causing
                # SQLITE_LOCKED for the next writer — which bypasses timeout=30.
                try:
                    conn.rollback()
                except Exception:
                    pass
                try:
                    self._pool.put_nowait(conn)
                except Exception:
                    # Pool full, close this one
                    conn.close()

    def close_all(self):
        """Close all connections in the pool."""
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                conn.close()
            except Empty:
                break
        self._created = 0


# Global connection pools (one per database path)
_pools: Dict[str, ConnectionPool] = {}


def get_pool(db_path: str) -> ConnectionPool:
    """Get or create a connection pool for the given database."""
    if db_path not in _pools:
        _pools[db_path] = ConnectionPool(db_path)
    return _pools[db_path]


def cleanup_all_pools():
    """Close all connection pools. Called on process exit."""
    for path, pool in list(_pools.items()):  # Use list() to avoid mutation during iteration
        try:
            pool.close_all()
            log("debug", "Closed connection pool", db_path=path)
        except (sqlite3.Error, OSError) as e:
            # Only catch database and OS errors, not system exceptions
            log("warn", "Failed to close pool", db_path=path, error=str(e))
    _pools.clear()


# Register cleanup handler for graceful shutdown
atexit.register(cleanup_all_pools)
