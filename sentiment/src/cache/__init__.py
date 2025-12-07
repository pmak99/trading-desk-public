"""Cache utilities for 4.0 AI-First Trading System."""

from .sentiment_cache import (
    SentimentCache,
    CachedSentiment,
    get_cached_sentiment,
    cache_sentiment,
)
from .budget_tracker import (
    BudgetTracker,
    BudgetInfo,
    BudgetStatus,
    check_budget,
    record_perplexity_call,
    get_budget_status,
)
from .sentiment_history import (
    SentimentHistory,
    SentimentRecord,
    SentimentDirection,
    record_sentiment,
    record_outcome,
    get_pending_outcomes,
    get_sentiment_stats,
)

__all__ = [
    # Sentiment cache (temporary, 3-hour TTL)
    "SentimentCache",
    "CachedSentiment",
    "get_cached_sentiment",
    "cache_sentiment",
    # Budget tracker
    "BudgetTracker",
    "BudgetInfo",
    "BudgetStatus",
    "check_budget",
    "record_perplexity_call",
    "get_budget_status",
    # Sentiment history (permanent, for backtesting)
    "SentimentHistory",
    "SentimentRecord",
    "SentimentDirection",
    "record_sentiment",
    "record_outcome",
    "get_pending_outcomes",
    "get_sentiment_stats",
]
