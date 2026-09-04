"""Domain repositories package — backward-compat re-exports."""

from src.domain.repositories.connection_pool import (
    ConnectionPool,
    get_pool,
    cleanup_all_pools,
    is_valid_ticker,
    validate_date,
    validate_limit,
    validate_days,
    TICKER_PATTERN,
    DATE_PATTERN,
    _normalize_ticker,
)
from src.domain.repositories.historical_moves import HistoricalMovesRepository
from src.domain.repositories.sentiment_cache import SentimentCacheRepository
from src.domain.repositories.vrp_cache import VRPCacheRepository

__all__ = [
    "ConnectionPool",
    "get_pool",
    "cleanup_all_pools",
    "is_valid_ticker",
    "validate_date",
    "validate_limit",
    "validate_days",
    "TICKER_PATTERN",
    "DATE_PATTERN",
    "_normalize_ticker",
    "HistoricalMovesRepository",
    "SentimentCacheRepository",
    "VRPCacheRepository",
]
