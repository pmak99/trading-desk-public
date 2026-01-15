"""Integration wrapper for 4.0's caching and budget tracking.

Provides access to sentiment cache and budget tracker without duplication.
"""

import sys
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

# Find main repo root (handles both main repo and worktrees)
def _find_main_repo() -> Path:
    """Find main repository root, handling worktrees correctly."""
    try:
        # Get git common dir (works in both main repo and worktrees)
        result = subprocess.run(
            ['git', 'rev-parse', '--git-common-dir'],
            capture_output=True,
            text=True,
            check=True,
            cwd=Path(__file__).parent
        )
        git_common_dir = Path(result.stdout.strip())

        # If commondir path is relative, make it absolute
        if not git_common_dir.is_absolute():
            git_common_dir = (Path(__file__).parent / git_common_dir).resolve()

        # Main repo is parent of .git directory
        main_repo = git_common_dir.parent
        return main_repo
    except:
        # Fallback: assume we're in main repo
        return Path(__file__).parent.parent.parent.parent

# Add 4.0/src to Python path
_main_repo = _find_main_repo()
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
