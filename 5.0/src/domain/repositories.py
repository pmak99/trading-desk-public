"""
Domain repositories for IV Crush 5.0.

Simple repositories for historical moves and sentiment cache.
Uses SQLite directly (no complex ORM).
"""

import sqlite3
from typing import Dict, Any, List, Optional
from datetime import date

from src.core.logging import log


class HistoricalMovesRepository:
    """Repository for historical earnings moves."""

    def __init__(self, db_path: str = "data/ivcrush.db"):
        self.db_path = db_path

    def get_moves(self, ticker: str, limit: int = 12) -> List[Dict[str, Any]]:
        """
        Get past earnings moves for ticker.

        Args:
            ticker: Stock symbol
            limit: Max moves to return (default 12 = 3 years quarterly)

        Returns:
            List of move dicts with gap_move_pct, intraday_move_pct, etc.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.execute(
                """
                SELECT ticker, earnings_date, gap_move_pct, intraday_move_pct,
                       close_before, close_after, direction
                FROM historical_moves
                WHERE ticker = ?
                ORDER BY earnings_date DESC
                LIMIT ?
                """,
                (ticker, limit)
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_average_move(self, ticker: str) -> Optional[float]:
        """
        Get average absolute move for VRP calculation.

        Returns:
            Average absolute gap move percent, or None if no data
        """
        moves = self.get_moves(ticker)
        if not moves:
            return None

        abs_moves = [abs(m["gap_move_pct"]) for m in moves if m.get("gap_move_pct")]
        if not abs_moves:
            return None

        return sum(abs_moves) / len(abs_moves)

    def count_moves(self, ticker: str) -> int:
        """Count historical moves for ticker."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM historical_moves WHERE ticker = ?",
                (ticker,)
            )
            return cursor.fetchone()[0]
        finally:
            conn.close()

    def save_move(self, move: Dict[str, Any]) -> bool:
        """
        Save a historical move record.

        Args:
            move: Dict with ticker, earnings_date, gap_move_pct, etc.

        Returns:
            True if saved successfully
        """
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO historical_moves
                (ticker, earnings_date, gap_move_pct, intraday_move_pct,
                 close_before, close_after, direction)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    move["ticker"],
                    move["earnings_date"],
                    move.get("gap_move_pct"),
                    move.get("intraday_move_pct"),
                    move.get("close_before"),
                    move.get("close_after"),
                    move.get("direction"),
                )
            )
            conn.commit()
            log("debug", "Saved move", ticker=move["ticker"], date=move["earnings_date"])
            return True
        except sqlite3.Error as e:
            log("error", "Failed to save move", error=str(e))
            return False
        finally:
            conn.close()


class SentimentCacheRepository:
    """Repository for cached AI sentiment data."""

    def __init__(self, db_path: str = "data/ivcrush.db"):
        self.db_path = db_path
        self._init_table()

    def _init_table(self):
        """Create sentiment_cache table if not exists."""
        conn = sqlite3.connect(self.db_path)
        try:
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
        finally:
            conn.close()

    def get_sentiment(self, ticker: str, earnings_date: str) -> Optional[Dict[str, Any]]:
        """
        Get cached sentiment for ticker.

        Args:
            ticker: Stock symbol
            earnings_date: Earnings date (YYYY-MM-DD)

        Returns:
            Sentiment dict if cached and not expired, None otherwise
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
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
        finally:
            conn.close()

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
            ticker: Stock symbol
            earnings_date: Earnings date (YYYY-MM-DD)
            sentiment: Sentiment dict with direction, score, tailwinds, headwinds
            ttl_hours: Time-to-live in hours (default 8 = pre-market cache)

        Returns:
            True if saved successfully
        """
        conn = sqlite3.connect(self.db_path)
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
        except sqlite3.Error as e:
            log("error", "Failed to cache sentiment", error=str(e))
            return False
        finally:
            conn.close()

    def clear_expired(self) -> int:
        """Clear expired cache entries. Returns count deleted."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "DELETE FROM sentiment_cache WHERE expires_at < datetime('now')"
            )
            count = cursor.rowcount
            conn.commit()
            if count > 0:
                log("info", "Cleared expired sentiment cache", count=count)
            return count
        finally:
            conn.close()

    def clear_all(self, ticker: Optional[str] = None) -> int:
        """
        Clear cache entries.

        Args:
            ticker: If provided, only clear for this ticker. Otherwise clear all.

        Returns:
            Count of deleted entries
        """
        conn = sqlite3.connect(self.db_path)
        try:
            if ticker:
                cursor = conn.execute(
                    "DELETE FROM sentiment_cache WHERE ticker = ?",
                    (ticker,)
                )
            else:
                cursor = conn.execute("DELETE FROM sentiment_cache")
            count = cursor.rowcount
            conn.commit()
            return count
        finally:
            conn.close()
