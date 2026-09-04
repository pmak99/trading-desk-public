"""Repository for cached VRP (Volatility Risk Premium) calculations."""

import sqlite3
from typing import Dict, Any, Optional

from src.core.logging import log
from src.domain.repositories.connection_pool import (
    _normalize_ticker, validate_date, get_pool,
)


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
                    has_weekly_options INTEGER DEFAULT 1,
                    weekly_reason TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    UNIQUE(ticker, earnings_date)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_vrp_ticker_date
                ON vrp_cache(ticker, earnings_date)
            """)
            # Migration: add columns to existing tables
            try:
                conn.execute("ALTER TABLE vrp_cache ADD COLUMN has_weekly_options INTEGER DEFAULT 1")
            except sqlite3.OperationalError:
                pass  # Column already exists
            try:
                conn.execute("ALTER TABLE vrp_cache ADD COLUMN weekly_reason TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass  # Column already exists
            conn.commit()

    def _calculate_ttl_hours(self, earnings_date: str) -> int:
        """
        Calculate TTL based on earnings proximity.

        Smart TTL:
        - Far (>3 days): 6 hours - options prices are stable
        - Near (<=3 days): 1 hour - need fresher data as earnings approach

        Timezone Assumptions:
        - All date comparisons use Eastern Time (US market timezone).
        - earnings_date: stored as YYYY-MM-DD in ET (from Alpha Vantage/Finnhub,
          which report dates in US market context).
        - today_et(): returns current date in America/New_York timezone via
          src.core.config, which uses pytz.timezone('US/Eastern').
        - Cloud Run runs in UTC, but today_et() converts to ET so TTL
          transitions happen at midnight ET, not midnight UTC. This ensures
          a 3-day threshold is consistent regardless of deployment timezone.
        - Cache expiry (expires_at) is also computed in ET via now_et() + timedelta.
        """
        try:
            from datetime import datetime
            from src.core.config import today_et
            # Both dates are in ET context: earnings_date from DB (stored in ET),
            # today_et() returns current date in Eastern Time
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
        ticker = _normalize_ticker(ticker)
        earnings_date = validate_date(earnings_date)

        with self._pool.get_connection() as conn:
            cursor = conn.execute(
                """
                SELECT ticker, earnings_date, implied_move_pct, vrp_ratio, vrp_tier,
                       historical_mean, price, expiration, used_real_data,
                       has_weekly_options, weekly_reason,
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
                    "has_weekly_options": bool(row["has_weekly_options"]) if row["has_weekly_options"] is not None else True,
                    "weekly_reason": row["weekly_reason"] or "",
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
        ticker = _normalize_ticker(ticker)
        earnings_date = validate_date(earnings_date)

        ttl_hours = self._calculate_ttl_hours(earnings_date)

        with self._pool.get_connection() as conn:
            try:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO vrp_cache
                    (ticker, earnings_date, implied_move_pct, vrp_ratio, vrp_tier,
                     historical_mean, price, expiration, used_real_data,
                     has_weekly_options, weekly_reason,
                     created_at, expires_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'),
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
                        1 if vrp_data.get("has_weekly_options", True) else 0,
                        vrp_data.get("weekly_reason", ""),
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
                ticker = _normalize_ticker(ticker)
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
