"""
IV History Tracker - tracks historical implied volatility for IV Rank calculation.

Implements the missing IV Rank feature by maintaining a rolling 52-week
history of IV values for each ticker.
"""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import logging
import threading

logger = logging.getLogger(__name__)


class IVHistoryTracker:
    """
    Tracks historical IV data to calculate IV Rank (percentile).

    IV Rank = Percentile of current IV within 52-week range
    - IV Rank 75% = current IV is higher than 75% of past year's values
    - IV Rank 25% = current IV is in bottom quartile (low volatility)
    """

    def __init__(self, db_path: str = "data/iv_history.db"):
        """
        Initialize IV history tracker.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Thread-local connections
        self._local = threading.local()

        # Initialize database
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local database connection."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30.0
            )
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.row_factory = sqlite3.Row
        return self._local.conn

    def _init_database(self):
        """Initialize database schema."""
        conn = self._get_connection()

        conn.executescript("""
            CREATE TABLE IF NOT EXISTS iv_history (
                ticker TEXT NOT NULL,
                date TEXT NOT NULL,
                iv_value REAL NOT NULL,
                timestamp TEXT NOT NULL,
                PRIMARY KEY (ticker, date)
            );

            CREATE INDEX IF NOT EXISTS idx_ticker_date ON iv_history(ticker, date DESC);
            CREATE INDEX IF NOT EXISTS idx_date ON iv_history(date);
        """)

        conn.commit()

    def record_iv(self, ticker: str, iv_value: float, date: Optional[str] = None):
        """
        Record IV value for a ticker.

        Args:
            ticker: Ticker symbol
            iv_value: IV percentage (e.g., 75.5 for 75.5%)
            date: Date string (YYYY-MM-DD), defaults to today
        """
        if iv_value <= 0:
            return  # Skip invalid values

        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')

        conn = self._get_connection()

        try:
            conn.execute(
                """INSERT OR REPLACE INTO iv_history (ticker, date, iv_value, timestamp)
                   VALUES (?, ?, ?, ?)""",
                (ticker, date, iv_value, datetime.now().isoformat())
            )
            conn.commit()
            logger.debug(f"{ticker}: Recorded IV {iv_value}% for {date}")

        except Exception as e:
            logger.warning(f"{ticker}: Failed to record IV: {e}")
            conn.rollback()

    def calculate_iv_rank(self, ticker: str, current_iv: float) -> float:
        """
        Calculate IV Rank (percentile of current IV in 52-week range).

        IV Rank Formula:
        - Get all IV values for ticker in past 52 weeks
        - Calculate: (# of days with IV < current) / (total # of days) * 100
        - Result: Percentile (0-100)

        Args:
            ticker: Ticker symbol
            current_iv: Current IV percentage

        Returns:
            IV Rank (0-100), or 0 if insufficient data
        """
        if current_iv <= 0:
            return 0.0

        conn = self._get_connection()

        # Get 52-week lookback date
        lookback_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')

        # Get all IV values in past 52 weeks
        cursor = conn.execute(
            """SELECT iv_value FROM iv_history
               WHERE ticker = ? AND date >= ?
               ORDER BY date ASC""",
            (ticker, lookback_date)
        )

        iv_values = [row['iv_value'] for row in cursor]

        if len(iv_values) < 30:  # Need at least 30 data points for reliable percentile
            logger.debug(f"{ticker}: Insufficient IV history ({len(iv_values)} days) for IV Rank")
            return 0.0

        # Calculate percentile: How many values are below current IV?
        below_current = sum(1 for iv in iv_values if iv < current_iv)
        iv_rank = (below_current / len(iv_values)) * 100

        logger.debug(
            f"{ticker}: IV Rank = {iv_rank:.1f}% "
            f"(current {current_iv}% vs {len(iv_values)}-day history)"
        )

        return round(iv_rank, 1)

    def get_iv_stats(self, ticker: str) -> dict:
        """
        Get IV statistics for a ticker (52-week range).

        Args:
            ticker: Ticker symbol

        Returns:
            Dict with min, max, avg, current, count
        """
        conn = self._get_connection()

        lookback_date = (datetime.now() - timedelta(days=365)).strftime('%Y-%m-%d')

        cursor = conn.execute(
            """SELECT
                   MIN(iv_value) as min_iv,
                   MAX(iv_value) as max_iv,
                   AVG(iv_value) as avg_iv,
                   COUNT(*) as count,
                   MAX(date) as latest_date
               FROM iv_history
               WHERE ticker = ? AND date >= ?""",
            (ticker, lookback_date)
        )

        row = cursor.fetchone()

        if row and row['count'] > 0:
            # Get latest IV
            cursor2 = conn.execute(
                """SELECT iv_value FROM iv_history
                   WHERE ticker = ?
                   ORDER BY date DESC LIMIT 1""",
                (ticker,)
            )
            latest_row = cursor2.fetchone()
            latest_iv = latest_row['iv_value'] if latest_row else 0

            return {
                'min_iv': round(row['min_iv'], 2) if row['min_iv'] else 0,
                'max_iv': round(row['max_iv'], 2) if row['max_iv'] else 0,
                'avg_iv': round(row['avg_iv'], 2) if row['avg_iv'] else 0,
                'latest_iv': round(latest_iv, 2),
                'data_points': row['count'],
                'latest_date': row['latest_date']
            }

        return {
            'min_iv': 0,
            'max_iv': 0,
            'avg_iv': 0,
            'latest_iv': 0,
            'data_points': 0,
            'latest_date': None
        }

    def cleanup_old_data(self, days_to_keep: int = 400):
        """
        Clean up IV data older than specified days.

        Args:
            days_to_keep: Number of days to keep (default: 400, ~13 months)
        """
        conn = self._get_connection()

        cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).strftime('%Y-%m-%d')

        try:
            cursor = conn.execute(
                "DELETE FROM iv_history WHERE date < ?",
                (cutoff_date,)
            )
            deleted = cursor.rowcount
            conn.commit()

            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old IV records (before {cutoff_date})")

        except Exception as e:
            logger.warning(f"Failed to cleanup old IV data: {e}")
            conn.rollback()

    def close(self):
        """Close database connection."""
        if hasattr(self._local, 'conn') and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
