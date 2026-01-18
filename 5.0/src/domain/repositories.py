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

    def get_moves_batch(self, tickers: List[str], limit: int = 12) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get historical moves for multiple tickers in a single query.

        Reduces N+1 query pattern (30 separate queries → 1 batch query).
        Returns dict mapping ticker to list of moves.

        Args:
            tickers: List of stock symbols
            limit: Max moves per ticker (1-100, default 12)

        Returns:
            Dict mapping ticker -> list of move dicts

        Example:
            moves = repo.get_moves_batch(["AAPL", "NVDA", "MSFT"])
            aapl_moves = moves.get("AAPL", [])
        """
        if not tickers:
            return {}

        # Validate all tickers
        validated_tickers = [validate_ticker(t) for t in tickers]
        limit = validate_limit(limit)

        # Build placeholders for IN clause
        placeholders = ",".join("?" for _ in validated_tickers)

        with self._pool.get_connection() as conn:
            # Use window function to get top N moves per ticker
            cursor = conn.execute(
                f"""
                WITH ranked AS (
                    SELECT ticker, earnings_date, gap_move_pct, intraday_move_pct,
                           prev_close, earnings_close,
                           CASE WHEN gap_move_pct >= 0 THEN 'UP' ELSE 'DOWN' END as direction,
                           ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY earnings_date DESC) as rn
                    FROM historical_moves
                    WHERE ticker IN ({placeholders})
                )
                SELECT ticker, earnings_date, gap_move_pct, intraday_move_pct,
                       prev_close, earnings_close, direction
                FROM ranked
                WHERE rn <= ?
                ORDER BY ticker, earnings_date DESC
                """,
                (*validated_tickers, limit)
            )
            rows = cursor.fetchall()

            # Group by ticker
            result: Dict[str, List[Dict[str, Any]]] = {t: [] for t in validated_tickers}
            for row in rows:
                d = dict(row)
                ticker = d["ticker"]
                d['close_before'] = d.pop('prev_close', None)
                d['close_after'] = d.pop('earnings_close', None)
                if ticker in result:
                    result[ticker].append(d)

            return result

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


class VRPCacheRepository:
    """
    Repository for cached VRP (Volatility Risk Premium) calculations.

    Reduces Tradier API calls by caching implied move and VRP data with smart TTL:
    - 6 hours when earnings >3 days away (options prices stable)
    - 1 hour when earnings ≤3 days (need fresher data near expiry)

    Expected impact: 90 → 10 API calls per /whisper scan (89% reduction).
    """

    # TTL based on earnings proximity
    TTL_HOURS_FAR = 6       # earnings > 3 days away
    TTL_HOURS_NEAR = 1      # earnings <= 3 days away
    NEAR_THRESHOLD_DAYS = 3

    def __init__(self, db_path: str = "data/ivcrush.db"):
        self.db_path = db_path
        self._pool = get_pool(db_path)
        self._init_table()

    def _init_table(self):
        """Create vrp_cache table if not exists."""
        with self._pool.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vrp_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker TEXT NOT NULL,
                    earnings_date TEXT NOT NULL,
                    implied_move_pct REAL NOT NULL,
                    vrp_ratio REAL NOT NULL,
                    vrp_tier TEXT NOT NULL,
                    historical_mean REAL,
                    price REAL,
                    expiration TEXT,
                    used_real_data INTEGER,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    UNIQUE(ticker, earnings_date)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_vrp_ticker_date
                ON vrp_cache(ticker, earnings_date)
            """)
            conn.commit()

    def _calculate_ttl_hours(self, earnings_date: str) -> int:
        """
        Calculate TTL based on earnings proximity.

        Smart TTL:
        - Far (>3 days): 6 hours - options prices are stable
        - Near (≤3 days): 1 hour - need fresher data as earnings approach

        Uses Eastern Time (market timezone) for consistent behavior
        across local development and Cloud Run (UTC).
        """
        try:
            from datetime import datetime
            from src.core.config import today_et
            earnings = datetime.strptime(earnings_date, "%Y-%m-%d").date()
            today = datetime.strptime(today_et(), "%Y-%m-%d").date()
            days_until = (earnings - today).days

            if days_until <= self.NEAR_THRESHOLD_DAYS:
                return self.TTL_HOURS_NEAR
            return self.TTL_HOURS_FAR
        except (ValueError, TypeError):
            # Default to shorter TTL if date parsing fails
            return self.TTL_HOURS_NEAR

    def get_vrp(self, ticker: str, earnings_date: str) -> Optional[Dict[str, Any]]:
        """
        Get cached VRP data for ticker.

        Args:
            ticker: Stock symbol (1-5 uppercase letters)
            earnings_date: Earnings date (YYYY-MM-DD)

        Returns:
            VRP data dict if cached and not expired, None otherwise
        """
        ticker = validate_ticker(ticker)
        earnings_date = validate_date(earnings_date)

        with self._pool.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT ticker, earnings_date, implied_move_pct, vrp_ratio, vrp_tier,
                       historical_mean, price, expiration, used_real_data,
                       created_at, expires_at
                FROM vrp_cache
                WHERE ticker = ? AND earnings_date = ?
                  AND expires_at > datetime('now')
                """,
                (ticker, earnings_date)
            )
            row = cursor.fetchone()
            if row:
                return {
                    "ticker": row["ticker"],
                    "earnings_date": row["earnings_date"],
                    "implied_move_pct": row["implied_move_pct"],
                    "vrp_ratio": row["vrp_ratio"],
                    "vrp_tier": row["vrp_tier"],
                    "historical_mean": row["historical_mean"],
                    "price": row["price"],
                    "expiration": row["expiration"],
                    "used_real_data": bool(row["used_real_data"]),
                    "from_cache": True,
                }
            return None

    def save_vrp(
        self,
        ticker: str,
        earnings_date: str,
        vrp_data: Dict[str, Any],
    ) -> bool:
        """
        Cache VRP data.

        Args:
            ticker: Stock symbol (1-5 uppercase letters)
            earnings_date: Earnings date (YYYY-MM-DD)
            vrp_data: VRP dict with implied_move_pct, vrp_ratio, vrp_tier, etc.

        Returns:
            True if saved successfully
        """
        ticker = validate_ticker(ticker)
        earnings_date = validate_date(earnings_date)

        ttl_hours = self._calculate_ttl_hours(earnings_date)

        with self._pool.get_connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO vrp_cache
                    (ticker, earnings_date, implied_move_pct, vrp_ratio, vrp_tier,
                     historical_mean, price, expiration, used_real_data,
                     created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'),
                            datetime('now', '+' || ? || ' hours'))
                    """,
                    (
                        ticker,
                        earnings_date,
                        vrp_data.get("implied_move_pct"),
                        vrp_data.get("vrp_ratio"),
                        vrp_data.get("vrp_tier"),
                        vrp_data.get("historical_mean"),
                        vrp_data.get("price"),
                        vrp_data.get("expiration"),
                        1 if vrp_data.get("used_real_data") else 0,
                        ttl_hours,
                    )
                )
                conn.commit()
                log("debug", "Cached VRP", ticker=ticker, ttl_hours=ttl_hours)
                return True
            except sqlite3.IntegrityError:
                # Duplicate is OK - idempotent
                return True
            except sqlite3.Error as e:
                log("error", "Failed to cache VRP", error=str(e), ticker=ticker)
                raise

    def clear_expired(self) -> int:
        """Clear expired cache entries. Returns count deleted."""
        with self._pool.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM vrp_cache WHERE expires_at < datetime('now')"
            )
            count = cursor.rowcount
            conn.commit()
            if count > 0:
                log("info", "Cleared expired VRP cache", count=count)
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
                    "DELETE FROM vrp_cache WHERE ticker = ?",
                    (ticker,)
                )
            else:
                cursor = conn.execute("DELETE FROM vrp_cache")
            count = cursor.rowcount
            conn.commit()
            return count

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics for monitoring."""
        with self._pool.get_connection() as conn:
            cursor = conn.execute("""
                SELECT
                    COUNT(*) as total_entries,
                    SUM(CASE WHEN expires_at > datetime('now') THEN 1 ELSE 0 END) as valid_entries,
                    SUM(CASE WHEN expires_at <= datetime('now') THEN 1 ELSE 0 END) as expired_entries
                FROM vrp_cache
            """)
            row = cursor.fetchone()
            return {
                "total_entries": row[0] or 0,
                "valid_entries": row[1] or 0,
                "expired_entries": row[2] or 0,
            }
