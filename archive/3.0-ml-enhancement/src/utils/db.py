"""
Database utilities for 3.0 ML Earnings Scanner.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

__all__ = ['get_db_connection']


@contextmanager
def get_db_connection(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager for database connections.

    Ensures connections are properly closed even if exceptions occur.

    Args:
        db_path: Path to the SQLite database file

    Yields:
        sqlite3.Connection: Database connection

    Example:
        with get_db_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM table")
    """
    conn = sqlite3.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
