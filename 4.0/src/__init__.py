"""
4.0 AI-First Trading System

This module imports and re-exports 2.0 components to stay in sync with
the proven production system. We import rather than copy to ensure
any 2.0 improvements automatically flow to 4.0.
"""

import sys
from pathlib import Path

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
    print(f"Warning: Could not import 2.0 domain modules: {e}")

# Infrastructure imports
try:
    from infrastructure.cache.hybrid_cache import HybridCache
    from infrastructure.database.connection_pool import ConnectionPool
except ImportError as e:
    print(f"Warning: Could not import 2.0 infrastructure modules: {e}")

# Utils imports
try:
    from utils.rate_limiter import TokenBucketRateLimiter
    from utils.circuit_breaker import CircuitBreaker
except ImportError as e:
    print(f"Warning: Could not import 2.0 utils modules: {e}")

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
]
