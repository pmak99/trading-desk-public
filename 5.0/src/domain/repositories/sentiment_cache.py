"""Repository for cached AI sentiment data."""

import json
import sqlite3
from typing import Dict, Any, Optional

from src.core.logging import log
from src.domain.repositories.connection_pool import (
    _normalize_ticker, validate_date, get_pool,
)

# Matches 2.0's actively-used convention (CLAUDE.md: "3-hour TTL sentiment
# cache"; every /analyze, /council, /whisper skill filters cached_at against
# this window before trusting a cached entry).
DEFAULT_READ_TTL_HOURS = 3

# Matches 2.0's /maintenance cleanup convention (CLAUDE.md: "Remove expired
# sentiment cache entries (>24 hours old)"). Distinct from the read-time TTL
# above -- this is periodic physical deletion of genuinely stale rows, not
# a per-read usability check.
DEFAULT_CLEANUP_HOURS = 24


class SentimentCacheRepository:
    """
    Repository for cached AI sentiment data.

    Reads and writes the SAME `sentiment_cache` table that 2.0 actively
    uses (schema: ticker, date, source, sentiment [JSON blob], cached_at;
    PRIMARY KEY (ticker, date)). Fixed 2026-07-27: this class previously
    expected a completely different schema of its own invention (id,
    earnings_date, direction, score, tailwinds, headwinds, raw_response,
    created_at, expires_at) that was never actually applied to the shared
    database -- every read/write against the real ivcrush.db crashed with
    "no such column: earnings_date", so in practice 5.0 silently fell back
    to creating its own empty, ephemeral local file (wiped on every Cloud
    Run cold start, since min-instances=0) and never shared a single
    sentiment entry with 2.0 in either direction.

    Two schema differences bridged transparently for existing 5.0 callers
    (all of which expect a dict with direction/score/tailwinds/headwinds):
    - 2.0 has no `expires_at` column; TTL is enforced at READ time via
      `cached_at`, not stored per-row. save_sentiment's `ttl_hours` param
      is kept for call-site backward compatibility but no longer controls
      anything -- there's no per-row expiry to set on a shared table other
      readers (2.0) also write.
    - 2.0's JSON blob uses `catalysts`/`risks` (see e.g. /council, /analyze
      skill prompts); 5.0's own Perplexity client and callers use
      `tailwinds`/`headwinds`. Both are accepted on write and read back
      correctly regardless of which side (2.0 or 5.0) wrote the entry.
    """

    def __init__(self, db_path: str = "data/ivcrush.db"):
        self.db_path = db_path
        self._pool = get_pool(db_path)
        self._init_table()

    def _init_table(self):
        """
        Create sentiment_cache table if not exists, using the SAME schema
        2.0 already has in production. CREATE TABLE IF NOT EXISTS is a
        no-op against the real ivcrush.db (table already exists there);
        this only matters for a fresh/test database that doesn't have the
        table yet, which must get the real, shared-compatible schema, not
        a 5.0-only one.
        """
        with self._pool.get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sentiment_cache (
                    ticker TEXT NOT NULL,
                    date TEXT NOT NULL,
                    source TEXT NOT NULL,
                    sentiment TEXT NOT NULL,
                    cached_at TEXT NOT NULL,
                    PRIMARY KEY (ticker, date)
                )
            """)
            conn.commit()

    def get_sentiment(
        self,
        ticker: str,
        earnings_date: str,
        ttl_hours: int = DEFAULT_READ_TTL_HOURS,
    ) -> Optional[Dict[str, Any]]:
        """
        Get cached sentiment for ticker, regardless of which system (2.0
        or 5.0) wrote it.

        Args:
            ticker: Stock symbol (1-5 uppercase letters)
            earnings_date: Earnings date (YYYY-MM-DD)
            ttl_hours: Freshness window -- entries older than this are
                treated as a cache miss. Default matches 2.0's convention.

        Returns:
            Sentiment dict (ticker, earnings_date, source, direction,
            score, tailwinds, headwinds, raw_response, created_at) if
            cached and fresh, None otherwise.
        """
        ticker = _normalize_ticker(ticker)
        earnings_date = validate_date(earnings_date)

        with self._pool.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT ticker, date, source, sentiment, cached_at
                FROM sentiment_cache
                WHERE ticker = ? AND date = ?
                  AND cached_at > datetime('now', '-' || ? || ' hours')
                """,
                (ticker, earnings_date, ttl_hours)
            )
            row = cursor.fetchone()
            if not row:
                return None

            try:
                data = json.loads(row["sentiment"])
            except (json.JSONDecodeError, TypeError) as e:
                log("warn", "Corrupt sentiment_cache entry, treating as miss",
                    ticker=ticker, error=str(e))
                return None

            return {
                "ticker": row["ticker"],
                "earnings_date": row["date"],
                "source": row["source"],
                "direction": data.get("direction"),
                "score": data.get("score"),
                "tailwinds": data.get("tailwinds") or data.get("catalysts"),
                "headwinds": data.get("headwinds") or data.get("risks"),
                "raw_response": data.get("raw") or data.get("raw_response"),
                "created_at": row["cached_at"],
            }

    def save_sentiment(
        self,
        ticker: str,
        earnings_date: str,
        sentiment: Dict[str, Any],
        ttl_hours: int = 8,
        source: str = "perplexity",
    ) -> bool:
        """
        Cache sentiment data into the shared sentiment_cache table.

        Args:
            ticker: Stock symbol (1-5 uppercase letters)
            earnings_date: Earnings date (YYYY-MM-DD)
            sentiment: Sentiment dict with direction, score, and either
                tailwinds/headwinds (5.0 convention) or catalysts/risks
                (2.0 convention) -- either is accepted.
            ttl_hours: Accepted for call-site backward compatibility;
                UNUSED for storage. The shared table has no per-row
                expiry column (2.0 also writes to it) -- freshness is a
                uniform read-time policy via get_sentiment's ttl_hours,
                not something a single writer can set per-entry.
            source: Which system/method produced this ('perplexity',
                'council', 'websearch', ...) -- matches 2.0's convention.
                Since the primary key is (ticker, date) with no source
                component, the most recent write always wins regardless
                of source, matching 2.0's existing INSERT OR REPLACE
                behavior.

        Returns:
            True if saved successfully.

        Raises:
            ValueError: If ticker or date is invalid.
            sqlite3.Error: On database errors.
        """
        ticker = _normalize_ticker(ticker)
        earnings_date = validate_date(earnings_date)

        if not (0 <= ttl_hours <= 168):  # Max 1 week -- validation kept even though unused for storage
            raise ValueError(f"Invalid ttl_hours: {ttl_hours} (must be 0-168)")

        payload = {
            "direction": sentiment.get("direction"),
            "score": sentiment.get("score"),
            "catalysts": sentiment.get("tailwinds") or sentiment.get("catalysts"),
            "risks": sentiment.get("headwinds") or sentiment.get("risks"),
            "raw": sentiment.get("raw") or sentiment.get("raw_response"),
        }

        with self._pool.get_connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO sentiment_cache
                    (ticker, date, source, sentiment, cached_at)
                    VALUES (?, ?, ?, ?, datetime('now'))
                    """,
                    (ticker, earnings_date, source, json.dumps(payload)),
                )
                conn.commit()
                log("debug", "Cached sentiment", ticker=ticker, source=source)
                return True
            except sqlite3.Error as e:
                log("error", "Failed to cache sentiment", error=str(e), ticker=ticker)
                raise

    def clear_expired(self, hours: int = DEFAULT_CLEANUP_HOURS) -> int:
        """
        Clear cache entries older than `hours` (default 24, matching 2.0's
        /maintenance cleanup). Returns count deleted.
        """
        with self._pool.get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM sentiment_cache WHERE cached_at < datetime('now', '-' || ? || ' hours')",
                (hours,)
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
