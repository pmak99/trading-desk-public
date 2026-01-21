"""Domain logic for IV Crush trading system."""

from .vrp import calculate_vrp, get_vrp_tier
from .liquidity import classify_liquidity_tier
from .scoring import calculate_score, apply_sentiment_modifier
from .repositories import HistoricalMovesRepository, SentimentCacheRepository, VRPCacheRepository, is_valid_ticker
from .strategies import Strategy, generate_strategies
from .position_sizing import half_kelly, calculate_position_size
from .implied_move import (
    calculate_implied_move,
    find_atm_straddle,
    calculate_implied_move_from_chain,
)
from .ticker import (
    validate_ticker,
    normalize_ticker,
    safe_normalize_ticker,
    resolve_alias,
    InvalidTickerError,
    TICKER_ALIASES,
)
from .skew import analyze_skew, DirectionalBias, SkewAnalysis
from .direction import adjust_direction, get_direction, DirectionAdjustment

# Import from 2.0 (follows "Import 2.0, Don't Copy" pattern from CLAUDE.md)
import sys
from pathlib import Path
_two_zero_path = str(Path(__file__).parent.parent.parent.parent / "2.0")
if _two_zero_path not in sys.path:
    sys.path.insert(0, _two_zero_path)
from src.application.filters.weekly_options import has_weekly_options

__all__ = [
    "calculate_vrp",
    "get_vrp_tier",
    "classify_liquidity_tier",
    "calculate_score",
    "apply_sentiment_modifier",
    "HistoricalMovesRepository",
    "SentimentCacheRepository",
    "VRPCacheRepository",
    "is_valid_ticker",
    "Strategy",
    "generate_strategies",
    "half_kelly",
    "calculate_position_size",
    "calculate_implied_move",
    "find_atm_straddle",
    "calculate_implied_move_from_chain",
    # Ticker validation
    "validate_ticker",
    "normalize_ticker",
    "safe_normalize_ticker",
    "resolve_alias",
    "InvalidTickerError",
    "TICKER_ALIASES",
    # Skew analysis and direction adjustment
    "analyze_skew",
    "DirectionalBias",
    "SkewAnalysis",
    "adjust_direction",
    "get_direction",
    "DirectionAdjustment",
    # Weekly options detection
    "has_weekly_options",
]
