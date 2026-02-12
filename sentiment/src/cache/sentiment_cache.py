"""
Sentiment Cache for sentiment AI-First Trading System

Caches Perplexity/WebSearch sentiment results to avoid duplicate API calls.
Uses SQLite for persistence across sessions.

Cache Key Format: sentiment:{TICKER}:{YYYY-MM-DD}:{SOURCE}
TTL: 3 hours (10800 seconds)
"""

import os
import re
import sqlite3
import threading
from datetime import datetime, date as date_class, timedelta, timezone
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

_db_lock = threading.Lock()


@dataclass
class CachedSentiment:
    """Cached sentiment result."""
    ticker: str
    date: str
    source: str  # "perplexity" or "websearch"
    sentiment: str  # The actual sentiment text/analysis
    cached_at: datetime

    @property
    def is_expired(self) -> bool:
        """Check if cache entry has expired (3 hour TTL).

        Note: Database stores cached_at as UTC ISO strings. When parsed,
        datetime.fromisoformat() returns naive datetime for strings without
        timezone suffix. We treat these naive datetimes as UTC since that's
        how they were stored (via datetime.now(timezone.utc).isoformat()).
        """
        now = datetime.now(timezone.utc)
        # Naive datetimes from database are stored as UTC, so attach UTC timezone
        # Aware datetimes are converted to UTC for comparison
        if self.cached_at.tzinfo is None:
            cached_at_utc = self.cached_at.replace(tzinfo=timezone.utc)
        else:
            cached_at_utc = self.cached_at.astimezone(timezone.utc)
        return now - cached_at_utc > timedelta(hours=3)

    @property
    def age_minutes(self) -> int:
        """Age of cache entry in minutes."""
        now = datetime.now(timezone.utc)
        # Same timezone handling as is_expired
        if self.cached_at.tzinfo is None:
            cached_at_utc = self.cached_at.replace(tzinfo=timezone.utc)
        else:
            cached_at_utc = self.cached_at.astimezone(timezone.utc)
        return int((now - cached_at_utc).total_seconds() / 60)


class SentimentCache:
    """
    SQLite-backed sentiment cache with 3-hour TTL.

    Usage:
        cache = SentimentCache()

        # Check cache
        cached = cache.get("NVDA", "2025-12-09")
        if cached:
            print(f"Cache hit! ({cached.source}, {cached.age_minutes}m old)")
            return cached.sentiment

        # Fetch fresh sentiment...
        sentiment = fetch_from_perplexity(ticker)

        # Store in cache
        cache.set("NVDA", "2025-12-09", "perplexity", sentiment)
    """

    DEFAULT_TTL_HOURS = 3
    VALID_SOURCES = {"perplexity", "websearch"}

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize cache with optional custom database path.

        Path resolution order:
        1. Explicit db_path argument
        2. SENTIMENT_DB_PATH environment variable
        3. Default: <4.0>/data/sentiment_cache.db (relative to module location)
        """
        if db_path is None:
            env_path = os.environ.get("SENTIMENT_DB_PATH")
            if env_path:
                db_path = Path(env_path)
            else:
                db_path = Path(__file__).parent.parent.parent / "data" / "sentiment_cache.db"

        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS sentiment_cache (
                        ticker TEXT NOT NULL,
                        date TEXT NOT NULL,
                        source TEXT NOT NULL,
                        sentiment TEXT NOT NULL,
                        cached_at TEXT NOT NULL,
                        PRIMARY KEY (ticker, date, source)
                    )
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_sentiment_ticker_date
                    ON sentiment_cache(ticker, date)
                """)
                conn.commit()

    def get(self, ticker: str, date: str, earnings_date: str = None) -> Optional[CachedSentiment]:
        """
        Get cached sentiment for ticker on date.

        Returns the newest non-expired entry, preferring perplexity over websearch.
        Returns None if no valid cache entry exists or if earnings have already passed.

        Args:
            ticker: Stock ticker (will be uppercased and validated)
            date: Date string (YYYY-MM-DD format)
            earnings_date: Optional earnings date; if passed, cached sentiment is
                          invalidated after earnings occur to force fresh fetch.
        """
        ticker = ticker.upper()
        if not ticker or not re.match(r'^[A-Z]{1,5}(\.[A-Z]{1,2})?$', ticker):
            raise ValueError(f"Invalid ticker format: {ticker}")

        # If earnings date has passed, don't return cached pre-earnings sentiment
        if earnings_date:
            try:
                ed = datetime.strptime(earnings_date, '%Y-%m-%d').date() if isinstance(earnings_date, str) else earnings_date
                if ed < date_class.today():
                    # Earnings already happened, cached sentiment is stale
                    return None
            except (ValueError, TypeError):
                pass

        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                conn.row_factory = sqlite3.Row

                # Get all entries for ticker+date, ordered by preference
                cursor = conn.execute("""
                    SELECT ticker, date, source, sentiment, cached_at
                    FROM sentiment_cache
                    WHERE ticker = ? AND date = ?
                    ORDER BY
                        CASE source WHEN 'perplexity' THEN 0 ELSE 1 END,
                        cached_at DESC
                """, (ticker, date))

                for row in cursor:
                    cached_at = datetime.fromisoformat(row['cached_at'])
                    entry = CachedSentiment(
                        ticker=row['ticker'],
                        date=row['date'],
                        source=row['source'],
                        sentiment=row['sentiment'],
                        cached_at=cached_at
                    )

                    if not entry.is_expired:
                        return entry

                return None

    def set(self, ticker: str, date: str, source: str, sentiment: str) -> None:
        """
        Store sentiment in cache.

        Args:
            ticker: Stock ticker (will be uppercased)
            date: Date string (YYYY-MM-DD format)
            source: "perplexity" or "websearch"
            sentiment: The sentiment analysis text

        Raises:
            ValueError: If source is not "perplexity" or "websearch"
        """
        if source not in self.VALID_SOURCES:
            raise ValueError(f"Invalid source '{source}'. Must be one of: {self.VALID_SOURCES}")

        ticker = ticker.upper()
        if not ticker or not re.match(r'^[A-Z]{1,5}(\.[A-Z]{1,2})?$', ticker):
            raise ValueError(f"Invalid ticker format: {ticker}")
        # Use UTC for consistent timezone handling
        cached_at = datetime.now(timezone.utc).isoformat()

        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO sentiment_cache
                    (ticker, date, source, sentiment, cached_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (ticker, date, source, sentiment, cached_at))
                conn.commit()

    def clear_expired(self) -> int:
        """Remove expired cache entries. Returns count of deleted entries."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=self.DEFAULT_TTL_HOURS)).isoformat()

        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                cursor = conn.execute("""
                    DELETE FROM sentiment_cache
                    WHERE cached_at < ?
                """, (cutoff,))
                conn.commit()
                return cursor.rowcount

    def clear_all(self) -> int:
        """Clear all cache entries. Returns count of deleted entries."""
        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                cursor = conn.execute("DELETE FROM sentiment_cache")
                conn.commit()
                return cursor.rowcount

    def stats(self) -> dict:
        """Get cache statistics."""
        with _db_lock:
            with sqlite3.connect(self.db_path, check_same_thread=False) as conn:
                conn.row_factory = sqlite3.Row

                total = conn.execute("SELECT COUNT(*) as cnt FROM sentiment_cache").fetchone()['cnt']

                by_source = {}
                for row in conn.execute("""
                    SELECT source, COUNT(*) as cnt
                    FROM sentiment_cache
                    GROUP BY source
                """):
                    by_source[row['source']] = row['cnt']

                # Count expired
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=self.DEFAULT_TTL_HOURS)).isoformat()
                expired = conn.execute("""
                    SELECT COUNT(*) as cnt
                    FROM sentiment_cache
                    WHERE cached_at < ?
                """, (cutoff,)).fetchone()['cnt']

                return {
                    "total_entries": total,
                    "by_source": by_source,
                    "expired": expired,
                    "valid": total - expired
                }


# Convenience function for slash commands
def get_cached_sentiment(ticker: str, date: str = None) -> Optional[str]:
    """
    Quick helper to get cached sentiment.

    Args:
        ticker: Stock ticker
        date: Optional date (defaults to today)

    Returns:
        Cached sentiment text or None if not cached/expired
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    cache = SentimentCache()
    result = cache.get(ticker, date)

    if result:
        return result.sentiment
    return None


def cache_sentiment(ticker: str, sentiment: str, source: str = "perplexity", date: str = None) -> None:
    """
    Quick helper to cache sentiment.

    Args:
        ticker: Stock ticker
        sentiment: Sentiment analysis text
        source: "perplexity" or "websearch"
        date: Optional date (defaults to today)

    Raises:
        ValueError: If source is not "perplexity" or "websearch"
    """
    if date is None:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    cache = SentimentCache()
    cache.set(ticker, date, source, sentiment)
