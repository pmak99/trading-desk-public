"""
Usage tracker with cost controls and budget management.
Tracks API calls, token usage, and enforces budget limits.

REFACTORED: Now uses SQLite backend for better concurrent performance.
Eliminates file lock bottleneck in multiprocessing scenarios.
"""

from src.core.usage_tracker_sqlite import UsageTrackerSQLite, BudgetExceededError
from typing import Dict, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

# Re-export for backward compatibility
__all__ = ['UsageTracker', 'BudgetExceededError']


class UsageTracker(UsageTrackerSQLite):
    """
    Usage tracker - now a thin wrapper around SQLiteTracker for backward compatibility.

    All functionality delegated to UsageTrackerSQLite which uses:
    - SQLite with WAL mode for concurrent read/write
    - ACID transactions (no race conditions)
    - No file locking bottleneck

    Maintains identical interface to old JSON-based implementation.
    """

    def __init__(self, config_path: str = "config/budget.yaml"):
        """Initialize usage tracker (delegates to SQLite backend)."""
        super().__init__(config_path=config_path)

    # Compatibility methods for legacy code
    def get_monthly_cost(self) -> float:
        """Get total cost for current month."""
        summary = self.get_usage_summary()
        return summary.get('total_cost', 0.0)

    def get_perplexity_cost(self) -> float:
        """Get Perplexity cost for current month."""
        summary = self.get_usage_summary()
        return summary.get('perplexity_cost', 0.0)

    def get_remaining_budget(self) -> float:
        """Get remaining budget for current month."""
        monthly_budget = self.config.get('monthly_budget', 5.0)
        return monthly_budget - self.get_monthly_cost()

    def get_remaining_perplexity_budget(self) -> float:
        """Get remaining Perplexity budget for current month."""
        perplexity_limit = self.config.get('perplexity_monthly_limit', 4.98)
        return perplexity_limit - self.get_perplexity_cost()

    def get_budget_percentage(self) -> float:
        """Get percentage of budget used."""
        monthly_budget = self.config.get('monthly_budget', 5.0)
        if monthly_budget == 0:
            return 0.0
        return (self.get_monthly_cost() / monthly_budget) * 100

    def get_dashboard_summary(self) -> Dict:
        """Get summary for dashboard display."""
        summary = self.get_usage_summary()
        monthly_budget = self.config.get('monthly_budget', 5.0)
        current_cost = summary.get('total_cost', 0.0)
        remaining = monthly_budget - current_cost
        percentage = (current_cost / monthly_budget * 100) if monthly_budget > 0 else 0.0

        today = self._current_date()
        daily_usage = summary.get('daily_usage', {})
        today_usage = daily_usage.get(today, {'calls': 0, 'cost': 0.0})

        return {
            'month': summary.get('month'),
            'budget': {
                'total': monthly_budget,
                'used': current_cost,
                'remaining': remaining,
                'percentage': percentage
            },
            'today': {
                'calls': today_usage.get('calls', 0),
                'cost': today_usage.get('cost', 0.0)
            },
            'total_calls': summary.get('total_calls', 0),
            'model_usage': summary.get('model_usage', {})
        }


# CLI for dashboard
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    tracker = UsageTracker()
    summary = tracker.get_dashboard_summary()

    logger.info("")
    logger.info('='*60)
    logger.info('API USAGE DASHBOARD')
    logger.info('='*60)
    logger.info("")
    logger.info(f"Month: {summary['month']}")
    logger.info("")
    logger.info('Budget:')
    logger.info(f"  Total: ${summary['budget']['total']:.2f}")
    logger.info(f"  Used:  ${summary['budget']['used']:.2f} ({summary['budget']['percentage']:.1f}%)")
    logger.info(f"  Remaining: ${summary['budget']['remaining']:.2f}")
    logger.info("")
    logger.info('Today:')
    logger.info(f"  API Calls: {summary['today']['calls']}")
    logger.info(f"  Cost: ${summary['today']['cost']:.4f}")
    logger.info("")
    logger.info(f"Total Calls This Month: {summary['total_calls']}")
    logger.info("")
    if summary['model_usage']:
        logger.info('Model Usage:')
        for model, usage in summary['model_usage'].items():
            logger.info(f"  {model}:")
            logger.info(f"    Calls: {usage['calls']}")
            logger.info(f"    Tokens: {usage['tokens']:,}")
            logger.info(f"    Cost: ${usage['cost']:.4f}")
    logger.info("")
    logger.info('='*60)
