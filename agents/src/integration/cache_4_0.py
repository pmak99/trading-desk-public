"""Integration wrapper for 4.0's caching and budget tracking.

Provides access to sentiment cache and budget tracker without duplication.
"""

import sys
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

# Add 4.0/src to Python path
_4_0_src = Path(__file__).parent.parent.parent.parent / "4.0" / "src"
if str(_4_0_src) not in sys.path:
    sys.path.insert(0, str(_4_0_src))

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
        return self.sentiment_cache.get(ticker, earnings_date)

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
        self.sentiment_cache.set(ticker, earnings_date, sentiment_data)

    def can_call_perplexity(self) -> bool:
        """
        Check if budget allows Perplexity API call.

        Returns:
            True if budget allows, False otherwise
        """
        return self.budget_tracker.can_call("perplexity")

    def record_call(
        self,
        service: str = "perplexity",
        cost: float = 0.006
    ):
        """
        Record an API call for budget tracking.

        Args:
            service: Service name (default: "perplexity")
            cost: Cost per call (default: $0.006)
        """
        self.budget_tracker.record_call(service, cost)

    def get_budget_status(self) -> Dict[str, Any]:
        """
        Get current budget status.

        Returns:
            Budget status dict with daily/monthly usage
        """
        return {
            'daily_calls': self.budget_tracker.get_daily_calls(),
            'daily_limit': 40,
            'monthly_cost': self.budget_tracker.get_monthly_cost(),
            'monthly_budget': 5.00,
            'daily_remaining': max(0, 40 - self.budget_tracker.get_daily_calls()),
            'monthly_remaining': max(
                0.0,
                5.00 - self.budget_tracker.get_monthly_cost()
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
