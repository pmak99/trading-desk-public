"""6.0 Utility modules.

Common utilities for formatting, paths, schemas, and async timeout handling.
"""

from .formatter import format_whisper_results, format_cross_ticker_warnings
from .paths import MAIN_REPO, REPO_2_0, REPO_4_0, REPO_5_0, DB_PATH, SENTIMENT_CACHE_DB
from .schemas import (
    SentimentFetchResponse,
    TickerAnalysisResponse,
    PositionLimits,
    TickerMetadata,
    PatternResult,
    ExplanationResponse,
    AnomalyDetectionResponse,
    HealthCheckResponse,
    PreFlightResponse,
    NewsFetchResponse,
    NewsHeadline,
)
from .retry import with_retry, is_transient_error
from .timeout import gather_with_timeout, run_with_timeout

__all__ = [
    # Formatter
    'format_whisper_results',
    'format_cross_ticker_warnings',
    # Paths
    'MAIN_REPO',
    'REPO_2_0',
    'REPO_4_0',
    'REPO_5_0',
    'DB_PATH',
    'SENTIMENT_CACHE_DB',
    # Schemas
    'SentimentFetchResponse',
    'TickerAnalysisResponse',
    'PositionLimits',
    'TickerMetadata',
    'PatternResult',
    'ExplanationResponse',
    'AnomalyDetectionResponse',
    'HealthCheckResponse',
    'PreFlightResponse',
    'NewsFetchResponse',
    'NewsHeadline',
    # Retry
    'with_retry',
    'is_transient_error',
    # Timeout
    'gather_with_timeout',
    'run_with_timeout',
]
