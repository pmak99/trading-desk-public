"""Repository for cached AI sentiment data."""

import sqlite3
from typing import Dict, Any, Optional

from src.core.logging import log
from src.domain.repositories.connection_pool import (
    _normalize_ticker, validate_date, get_pool,
)


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
        ticker = _normalize_ticker(ticker)
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
        ticker = _normalize_ticker(ticker)
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
                ticker = _normalize_ticker(ticker)
                cursor = conn.execute(
                    "DELETE FROM sentiment_cache WHERE ticker = ?",
                    (ticker,)
                )
            else:
                cursor = conn.execute("DELETE FROM sentiment_cache")
            count = cursor.rowcount
            conn.commit()
            return count
