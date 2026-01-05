"""
Domain repositories for IV Crush 5.0.

Simple repositories for historical moves and sentiment cache.
Uses SQLite directly with connection pooling.
"""

import atexit
import re
import sqlite3
import threading
from queue import Queue, Empty
from contextlib import contextmanager
from typing import Dict, Any, List, Optional
from datetime import date

from src.core.logging import log

# Input validation patterns - allow BRK.B, BF.A style tickers
TICKER_PATTERN = re.compile(r'^[A-Z]{1,5}(\.[A-Z]{1,2})?$')
DATE_PATTERN = re.compile(r'^\d{4}-\d{2}-\d{2}$')


def validate_ticker(ticker: str) -> str:
    """Validate and normalize ticker symbol."""
    ticker = ticker.upper().strip()
    if not TICKER_PATTERN.match(ticker):
        raise ValueError(f"Invalid ticker format: {ticker}")
    return ticker


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


class ConnectionPool:
    """Simple SQLite connection pool for better performance."""

    def __init__(self, db_path: str, max_connections: int = 5):
        self.db_path = db_path
        self._pool: Queue = Queue(maxsize=max_connections)
        self._max = max_connections
        self._created = 0
        self._lock = threading.Lock()  # Protect _created counter

    def _create_connection(self) -> sqlite3.Connection:
        """Create a new connection."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
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


class HistoricalMovesRepository:
    """Repository for historical earnings moves."""

    def __init__(self, db_path: str = "data/ivcrush.db"):
        self.db_path = db_path
        self._pool = get_pool(db_path)

    def get_moves(self, ticker: str, limit: int = 12) -> List[Dict[str, Any]]:
        """
        Get past earnings moves for ticker.

        Args:
            ticker: Stock symbol (1-5 uppercase letters)
            limit: Max moves to return (1-100, default 12)

        Returns:
            List of move dicts with gap_move_pct, intraday_move_pct, etc.

        Raises:
            ValueError: If ticker or limit is invalid
        """
        ticker = validate_ticker(ticker)
        limit = validate_limit(limit)

        with self._pool.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT ticker, earnings_date, gap_move_pct, intraday_move_pct,
                       prev_close, earnings_close,
                       CASE WHEN gap_move_pct >= 0 THEN 'UP' ELSE 'DOWN' END as direction
                FROM historical_moves
                WHERE ticker = ?
                ORDER BY earnings_date DESC
                LIMIT ?
                """,
                (ticker, limit)
            )
            rows = cursor.fetchall()
            # Map column names for compatibility
            results = []
            for row in rows:
                d = dict(row)
                d['close_before'] = d.pop('prev_close', None)
                d['close_after'] = d.pop('earnings_close', None)
                results.append(d)
            return results

    def get_average_move(self, ticker: str, metric: str = "intraday") -> Optional[float]:
        """
        Get average absolute move for VRP calculation.

        Args:
            ticker: Stock symbol
            metric: "intraday" (default, matches 2.0) or "gap"

        Returns:
            Average absolute move percent, or None if no data
        """
        ticker = validate_ticker(ticker)
        moves = self.get_moves(ticker)
        if not moves:
            return None

        # Use intraday_move_pct by default (matches 2.0 behavior)
        move_key = "intraday_move_pct" if metric == "intraday" else "gap_move_pct"
        abs_moves = [abs(m[move_key]) for m in moves if m.get(move_key)]
        if not abs_moves:
            return None

        return sum(abs_moves) / len(abs_moves)

    def get_next_earnings(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Get next upcoming earnings date for ticker from calendar.

        Args:
            ticker: Stock symbol

        Returns:
            Dict with earnings_date, timing (BMO/AMC), or None if not found
        """
        ticker = validate_ticker(ticker)

        with self._pool.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT earnings_date, timing
                FROM earnings_calendar
                WHERE ticker = ? AND earnings_date >= date('now')
                ORDER BY earnings_date ASC
                LIMIT 1
                """,
                (ticker,)
            )
            row = cursor.fetchone()
            if row:
                return {"earnings_date": row["earnings_date"], "timing": row["timing"]}
            return None

    def count_moves(self, ticker: str) -> int:
        """Count historical moves for ticker."""
        ticker = validate_ticker(ticker)

        with self._pool.get_connection() as conn:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM historical_moves WHERE ticker = ?",
                (ticker,)
            )
            return cursor.fetchone()[0]

    def save_move(self, move: Dict[str, Any]) -> bool:
        """
        Save a historical move record.

        Args:
            move: Dict with ticker, earnings_date, gap_move_pct, etc.

        Returns:
            True if saved successfully

        Raises:
            ValueError: If ticker or date is invalid
            sqlite3.Error: On database errors (except duplicates)
        """
        ticker = validate_ticker(move["ticker"])
        earnings_date = validate_date(move["earnings_date"])

        with self._pool.get_connection() as conn:
            try:
                # Map to actual database schema columns
                prev_close = move.get("prev_close") or move.get("close_before")
                earnings_close = move.get("earnings_close") or move.get("close_after")

                conn.execute(
                    """
                    INSERT OR REPLACE INTO historical_moves
                    (ticker, earnings_date, gap_move_pct, intraday_move_pct,
                     prev_close, earnings_open, earnings_high, earnings_low,
                     earnings_close, close_move_pct)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ticker,
                        earnings_date,
                        move.get("gap_move_pct"),
                        move.get("intraday_move_pct"),
                        prev_close,
                        move.get("earnings_open"),
                        move.get("earnings_high"),
                        move.get("earnings_low"),
                        earnings_close,
                        move.get("close_move_pct"),
                    )
                )
                conn.commit()
                log("debug", "Saved move", ticker=ticker, date=earnings_date)
                return True
            except sqlite3.IntegrityError:
                # Duplicate is OK - idempotent operation
                log("debug", "Move already exists", ticker=ticker, date=earnings_date)
                return True
            except sqlite3.Error as e:
                log("error", "Failed to save move", error=str(e), ticker=ticker)
                raise  # Re-raise for caller to handle

    def get_position_limits(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Get position limits and tail risk data for ticker.

        Returns:
            Dict with tail_risk_ratio, tail_risk_level, max_contracts, max_notional,
            or None if not found or table doesn't exist
        """
        ticker = validate_ticker(ticker)

        with self._pool.get_connection() as conn:
            try:
                cursor = conn.execute(
                    """
                    SELECT ticker, tail_risk_ratio, tail_risk_level,
                           max_contracts, max_notional, avg_move, max_move, num_quarters
                    FROM position_limits
                    WHERE ticker = ?
                    """,
                    (ticker,)
                )
                row = cursor.fetchone()
                if row:
                    return {
                        "ticker": row["ticker"],
                        "tail_risk_ratio": row["tail_risk_ratio"],
                        "tail_risk_level": row["tail_risk_level"],
                        "max_contracts": row["max_contracts"],
                        "max_notional": row["max_notional"],
                        "avg_move": row["avg_move"],
                        "max_move": row["max_move"],
                        "num_quarters": row["num_quarters"],
                    }
                return None
            except sqlite3.OperationalError as e:
                # Table might not exist yet - gracefully return None
                if "no such table" in str(e):
                    log("debug", "position_limits table not found", ticker=ticker)
                    return None
                raise


class SentimentCacheRepository:
    """Repository for cached AI sentiment data."""

    def __init__(self, db_path: str = "data/ivcrush.db"):
        self.db_path = db_path
        self._pool = get_pool(db_path)
        self._init_table()

    def _init_table(self):
        """Create sentiment_cache table if not exists."""
        with self._pool.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sentiment_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    earnings_date TEXT NOT NULL,
                    direction TEXT,
                    score REAL,
                    tailwinds TEXT,
                    headwinds TEXT,
                    raw_response TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    UNIQUE(ticker, earnings_date)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sentiment_ticker_date
                ON sentiment_cache(ticker, earnings_date)
            """)
            conn.commit()

    def get_sentiment(self, ticker: str, earnings_date: str) -> Optional[Dict[str, Any]]:
        """
        Get cached sentiment for ticker.

        Args:
            ticker: Stock symbol (1-5 uppercase letters)
            earnings_date: Earnings date (YYYY-MM-DD)

        Returns:
            Sentiment dict if cached and not expired, None otherwise
        """
        ticker = validate_ticker(ticker)
        earnings_date = validate_date(earnings_date)

        with self._pool.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT ticker, earnings_date, direction, score, tailwinds, headwinds,
                       raw_response, created_at, expires_at
                FROM sentiment_cache
                WHERE ticker = ? AND earnings_date = ?
                  AND expires_at > datetime('now')
                """,
                (ticker, earnings_date)
            )
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None

    def save_sentiment(
        self,
        ticker: str,
        earnings_date: str,
        sentiment: Dict[str, Any],
        ttl_hours: int = 8
    ) -> bool:
        """
        Cache sentiment data.

        Args:
            ticker: Stock symbol (1-5 uppercase letters)
            earnings_date: Earnings date (YYYY-MM-DD)
            sentiment: Sentiment dict with direction, score, tailwinds, headwinds
            ttl_hours: Time-to-live in hours (default 8 = pre-market cache)

        Returns:
            True if saved successfully

        Raises:
            ValueError: If ticker or date is invalid
            sqlite3.Error: On database errors
        """
        ticker = validate_ticker(ticker)
        earnings_date = validate_date(earnings_date)

        if not (0 <= ttl_hours <= 168):  # Max 1 week
            raise ValueError(f"Invalid ttl_hours: {ttl_hours} (must be 0-168)")

        with self._pool.get_connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO sentiment_cache
                    (ticker, earnings_date, direction, score, tailwinds, headwinds,
                     raw_response, created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now', '+' || ? || ' hours'))
                    """,
                    (
                        ticker,
                        earnings_date,
                        sentiment.get("direction"),
                        sentiment.get("score"),
                        sentiment.get("tailwinds"),
                        sentiment.get("headwinds"),
                        sentiment.get("raw"),
                        ttl_hours,
                    )
                )
                conn.commit()
                log("debug", "Cached sentiment", ticker=ticker, ttl_hours=ttl_hours)
                return True
            except sqlite3.IntegrityError:
                # Duplicate is OK - idempotent
                return True
            except sqlite3.Error as e:
                log("error", "Failed to cache sentiment", error=str(e), ticker=ticker)
                raise

    def clear_expired(self) -> int:
        """Clear expired cache entries. Returns count deleted."""
        with self._pool.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM sentiment_cache WHERE expires_at < datetime('now')"
            )
            count = cursor.rowcount
            conn.commit()
            if count > 0:
                log("info", "Cleared expired sentiment cache", count=count)
            return count

    def clear_all(self, ticker: Optional[str] = None) -> int:
        """
        Clear cache entries.

        Args:
            ticker: If provided, only clear for this ticker. Otherwise clear all.

        Returns:
            Count of deleted entries
        """
        with self._pool.get_connection() as conn:
            if ticker:
                ticker = validate_ticker(ticker)
                cursor = conn.execute(
                    "DELETE FROM sentiment_cache WHERE ticker = ?",
                    (ticker,)
                )
            else:
                cursor = conn.execute("DELETE FROM sentiment_cache")
            count = cursor.rowcount
            conn.commit()
            return count
