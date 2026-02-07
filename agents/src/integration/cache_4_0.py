"""Integration wrapper for 4.0's caching and budget tracking.

Provides access to sentiment cache and budget tracker without duplication.
"""

import logging
import sys
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# Sentinel value for unknown/unparseable cache age.
# Using float('inf') ensures stale-cache checks naturally trigger a refresh,
# since any threshold comparison (e.g., age > 3.0) will be True.
CACHE_AGE_UNKNOWN = float('inf')

from ..utils.paths import MAIN_REPO, REPO_4_0

# Add 4.0/src to Python path
_main_repo = MAIN_REPO
_4_0_src = _main_repo / "4.0" / "src"
_4_0_src_str = str(_4_0_src)

# Remove if already in path (so we can re-insert)
if _4_0_src_str in sys.path:
    sys.path.remove(_4_0_src_str)

# Insert with priority
sys.path.insert(1, _4_0_src_str)  # Position 1 (2.0/ should be at 0)

# Import 4.0 components
from cache.sentiment_cache import SentimentCache
from cache.budget_tracker import BudgetTracker


class Cache4_0:
    """
    Wrapper for 4.0's caching and budget tracking.

    Provides access to:
    - sentiment_cache: 3-hour TTL sentiment caching
    - budget_tracker: $5/month, 40 calls/day tracking

    Example:
        cache = Cache4_0()
        sentiment = cache.get_cached_sentiment("NVDA", "2026-02-05")
        if cache.can_call_perplexity():
            # Make Perplexity API call
            cache.record_call("perplexity", cost=0.006)
    """

    def __init__(self):
        """Initialize cache and budget tracker."""
        self.sentiment_cache = SentimentCache()
        self.budget_tracker = BudgetTracker()

    def get_cached_sentiment(
        self,
        ticker: str,
        earnings_date: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get cached sentiment if available and fresh.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Earnings date (YYYY-MM-DD)

        Returns:
            Cached sentiment dict or None if not cached/expired
        """
        import json

        cached = self.sentiment_cache.get(ticker, earnings_date)
        if cached:
            # Parse JSON string back to dict
            try:
                return json.loads(cached.sentiment)
            except (json.JSONDecodeError, AttributeError):
                # Invalid cache entry, return None
                return None
        return None

    def cache_sentiment(
        self,
        ticker: str,
        earnings_date: str,
        sentiment_data: Dict[str, Any]
    ):
        """
        Cache sentiment data for 3 hours.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Earnings date (YYYY-MM-DD)
            sentiment_data: Sentiment data to cache
        """
        import json

        # Serialize dict to JSON string for storage
        sentiment_str = json.dumps(sentiment_data)

        # Store with source='perplexity'
        self.sentiment_cache.set(ticker, earnings_date, 'perplexity', sentiment_str)

    def can_call_perplexity(self) -> bool:
        """
        Check if budget allows Perplexity API call.

        Returns:
            True if budget allows, False otherwise
        """
        return self.budget_tracker.can_call()

    def record_call(
        self,
        cost: float = 0.006
    ):
        """
        Record an API call for budget tracking.

        Args:
            cost: Cost per call (default: $0.006)
        """
        self.budget_tracker.record_call(cost)

    def get_budget_status(self) -> Dict[str, Any]:
        """
        Get current budget status.

        Returns:
            Budget status dict with daily/monthly usage
        """
        info = self.budget_tracker.get_info()
        monthly_summary = self.budget_tracker.get_monthly_summary()

        return {
            'daily_calls': info.calls_today,
            'daily_limit': 40,
            'monthly_cost': monthly_summary.get('month_cost', 0.0),
            'monthly_budget': 5.00,
            'daily_remaining': info.calls_remaining,
            'monthly_remaining': max(
                0.0,
                5.00 - monthly_summary.get('month_cost', 0.0)
            )
        }

    def get_cache_statistics(self) -> Dict[str, Any]:
        """
        Get cache statistics for monitoring.

        Returns:
            Cache stats dict with hit rate, size, etc.
        """
        return self.sentiment_cache.get_stats()

    def clear_expired_cache(self) -> int:
        """
        Clear expired cache entries.

        Returns:
            Number of entries cleared
        """
        return self.sentiment_cache.clear_expired()

    def check_perplexity_health(self) -> Dict[str, Any]:
        """
        Check Perplexity API budget health.

        Returns:
            Health status dict with remaining calls and status
        """
        status = self.get_budget_status()

        # Determine health status
        daily_remaining = status['daily_remaining']
        monthly_remaining = status['monthly_remaining']

        if daily_remaining == 0 or monthly_remaining <= 0:
            health_status = 'error'
        elif daily_remaining < 5 or monthly_remaining < 0.50:
            health_status = 'degraded'
        else:
            health_status = 'ok'

        return {
            'status': health_status,
            'remaining_calls': daily_remaining,
            'monthly_remaining': monthly_remaining,
            'error': None if health_status == 'ok' else 'Budget limit approaching'
        }

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get detailed cache statistics for maintenance.

        Returns:
            Cache stats with total entries, stale entries, hit rate
        """
        from datetime import datetime, timedelta

        # Get cache database connection
        import sqlite3
        cache_db = _main_repo / "4.0" / "data" / "sentiment_cache.db"

        with sqlite3.connect(str(cache_db)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Total entries
            cursor.execute("SELECT COUNT(*) FROM sentiment_cache")
            total_entries = cursor.fetchone()[0]

            # Stale entries (>3 hours old)
            cutoff_time = datetime.now() - timedelta(hours=3)
            cursor.execute(
                "SELECT COUNT(*) FROM sentiment_cache WHERE cached_at < ?",
                (cutoff_time.isoformat(),)
            )
            stale_entries = cursor.fetchone()[0]

        # Note: Hit rate tracking not yet implemented in 4.0's cache
        # Would require tracking cache hits/misses in sentiment_cache

        return {
            'total_entries': total_entries,
            'stale_entries': stale_entries,
            'hit_rate': None  # Not yet tracked
        }

    def cleanup_sentiment_cache(self, max_age_hours: int = 3) -> int:
        """
        Clean up sentiment cache entries older than specified age.

        Args:
            max_age_hours: Maximum age in hours (default: 3)

        Returns:
            Number of entries removed
        """
        from datetime import datetime, timedelta

        cutoff_time = datetime.now() - timedelta(hours=max_age_hours)

        import sqlite3
        cache_db = _main_repo / "4.0" / "data" / "sentiment_cache.db"

        with sqlite3.connect(str(cache_db)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Delete old entries
            cursor.execute(
                "DELETE FROM sentiment_cache WHERE cached_at < ?",
                (cutoff_time.isoformat(),)
            )

            deleted_count = cursor.rowcount
            conn.commit()

        return deleted_count

    def cleanup_budget_tracker(self, max_age_days: int = 30) -> int:
        """
        Clean up budget tracker entries older than specified age.

        Args:
            max_age_days: Maximum age in days (default: 30)

        Returns:
            Number of entries removed
        """
        from datetime import datetime, timedelta

        cutoff_date = datetime.now() - timedelta(days=max_age_days)

        import sqlite3
        budget_db = _main_repo / "4.0" / "data" / "perplexity_budget.db"

        with sqlite3.connect(str(budget_db)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # Delete old entries (use date field for filtering)
            cutoff_date_str = cutoff_date.strftime('%Y-%m-%d')
            cursor.execute(
                "DELETE FROM api_budget WHERE date < ?",
                (cutoff_date_str,)
            )

            deleted_count = cursor.rowcount
            conn.commit()

        return deleted_count

    def get_cache_age_hours(
        self,
        ticker: str,
        earnings_date: str
    ) -> Optional[float]:
        """
        Get cache age in hours for a specific ticker/earnings_date.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Earnings date (YYYY-MM-DD)

        Returns:
            Cache age in hours, or None if not cached
        """
        import sqlite3

        cache_db = _main_repo / "4.0" / "data" / "sentiment_cache.db"

        try:
            with sqlite3.connect(str(cache_db)) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                cursor.execute(
                    "SELECT cached_at FROM sentiment_cache WHERE ticker = ? AND earnings_date = ?",
                    (ticker.upper(), earnings_date)
                )

                row = cursor.fetchone()

            if row and row[0]:
                cached_at_str = row[0]
                # Parse ISO format timestamp
                try:
                    cached_at = datetime.fromisoformat(cached_at_str.replace('Z', '+00:00'))
                    # Handle timezone-naive comparison
                    if cached_at.tzinfo is not None:
                        cached_at = cached_at.replace(tzinfo=None)
                    age_seconds = (datetime.now() - cached_at).total_seconds()
                    return age_seconds / 3600.0
                except (ValueError, TypeError):
                    logger.debug(f"Could not parse cache timestamp for {ticker}: {cached_at_str}")
                    return CACHE_AGE_UNKNOWN

            return None
        except Exception as e:
            # Return None on database access failure - cache age is optional
            logger.debug(f"Database error checking cache age for {ticker}: {e}")
            return None
