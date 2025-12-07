"""
4.0 AI-First Trading System

This module imports and re-exports 2.0 components to stay in sync with
the proven production system. We import rather than copy to ensure
any 2.0 improvements automatically flow to 4.0.
"""

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Add 2.0/src to Python path - stay in sync with 2.0
_2_0_src = Path(__file__).parent.parent.parent / "2.0" / "src"
if _2_0_src.exists():
    sys.path.insert(0, str(_2_0_src))

# Domain layer imports
try:
    from domain.types import (
        Money, Strike, OptionQuote, OptionChain,
        ImpliedMove, VRPResult, Strategy
    )
    from domain.errors import Result, Ok, Err
    from domain.enums import (
        EarningsTiming, OptionType, Recommendation,
        StrategyType, DirectionalBias
    )
    from domain.liquidity import LiquidityTier
except ImportError as e:
    logger.warning(f"Could not import 2.0 domain modules: {e}")

# Infrastructure imports
try:
    from infrastructure.cache.hybrid_cache import HybridCache
    from infrastructure.database.connection_pool import ConnectionPool
except ImportError as e:
    logger.warning(f"Could not import 2.0 infrastructure modules: {e}")

# Utils imports
try:
    from utils.rate_limiter import TokenBucketRateLimiter
    from utils.circuit_breaker import CircuitBreaker
except ImportError as e:
    logger.warning(f"Could not import 2.0 utils modules: {e}")

# 4.0 Sentiment-adjusted direction
try:
    from .sentiment_direction import (
        adjust_direction, format_adjustment, quick_adjust,
        DirectionAdjustment, AdjustedBias
    )
except ImportError as e:
    logger.warning(f"Could not import sentiment_direction: {e}")

__version__ = "4.0.0"
__all__ = [
    # Domain
    "Money", "Strike", "OptionQuote", "OptionChain",
    "ImpliedMove", "VRPResult", "Strategy",
    "Result", "Ok", "Err",
    "EarningsTiming", "OptionType", "Recommendation",
    "StrategyType", "DirectionalBias",
    "LiquidityTier",
    # Infrastructure
    "HybridCache", "ConnectionPool",
    # Utils
    "TokenBucketRateLimiter", "CircuitBreaker",
    # 4.0 Sentiment
    "adjust_direction", "format_adjustment", "quick_adjust",
    "DirectionAdjustment", "AdjustedBias",
]
