"""
Market Regime features for ML model.

Captures overall market conditions that affect earnings volatility:
- VIX level and trend
- SPY trend and momentum
- Market breadth indicators
"""

import logging
from typing import Dict, Optional
from datetime import date, timedelta
from functools import lru_cache

import yfinance as yf
import numpy as np

logger = logging.getLogger(__name__)

__all__ = [
    'MarketRegime',
    'get_market_regime',
    'get_market_features',
]


class MarketRegime:
    """Container for market regime data."""

    def __init__(
        self,
        vix_level: Optional[float] = None,
        vix_percentile: Optional[float] = None,
        vix_trend: Optional[str] = None,  # 'rising', 'falling', 'stable'
        spy_trend: Optional[str] = None,  # 'bullish', 'bearish', 'neutral'
        spy_momentum: Optional[float] = None,  # 20-day return
        market_breadth: Optional[str] = None,  # 'strong', 'weak', 'neutral'
    ):
        self.vix_level = vix_level
        self.vix_percentile = vix_percentile
        self.vix_trend = vix_trend
        self.spy_trend = spy_trend
        self.spy_momentum = spy_momentum
        self.market_breadth = market_breadth

    def to_features(self) -> Dict[str, float]:
        """Convert to ML feature dict."""
        features = {}

        # VIX features
        if self.vix_level is not None:
            features['vix_level'] = self.vix_level
            features['vix_high'] = 1.0 if self.vix_level > 25 else 0.0
            features['vix_low'] = 1.0 if self.vix_level < 15 else 0.0

        if self.vix_percentile is not None:
            features['vix_percentile'] = self.vix_percentile

        # VIX trend one-hot
        features['vix_rising'] = 1.0 if self.vix_trend == 'rising' else 0.0
        features['vix_falling'] = 1.0 if self.vix_trend == 'falling' else 0.0

        # SPY trend one-hot
        features['spy_bullish'] = 1.0 if self.spy_trend == 'bullish' else 0.0
        features['spy_bearish'] = 1.0 if self.spy_trend == 'bearish' else 0.0

        if self.spy_momentum is not None:
            features['spy_momentum_20d'] = self.spy_momentum

        # Market breadth
        features['breadth_strong'] = 1.0 if self.market_breadth == 'strong' else 0.0
        features['breadth_weak'] = 1.0 if self.market_breadth == 'weak' else 0.0

        return features


def _calculate_vix_percentile(vix_value: float, vix_history: list) -> float:
    """Calculate where current VIX falls in historical distribution."""
    if not vix_history:
        return 50.0
    below = sum(1 for v in vix_history if v < vix_value)
    return (below / len(vix_history)) * 100


def get_market_regime(as_of_date: date) -> MarketRegime:
    """
    Get market regime data for a given date.

    Args:
        as_of_date: Reference date

    Returns:
        MarketRegime with current market conditions
    """
    try:
        # Fetch VIX data
        vix = yf.Ticker("^VIX")
        start = as_of_date - timedelta(days=365)
        vix_hist = vix.history(start=start.isoformat(), end=as_of_date.isoformat())

        vix_level = None
        vix_percentile = None
        vix_trend = 'stable'

        if len(vix_hist) > 20:
            vix_level = vix_hist['Close'].iloc[-1]
            vix_history = vix_hist['Close'].tolist()
            vix_percentile = _calculate_vix_percentile(vix_level, vix_history[:-1])

            # VIX trend: compare 5-day average to 20-day average
            vix_5d = vix_hist['Close'].iloc[-5:].mean()
            vix_20d = vix_hist['Close'].iloc[-20:].mean()
            if vix_5d > vix_20d * 1.1:
                vix_trend = 'rising'
            elif vix_5d < vix_20d * 0.9:
                vix_trend = 'falling'

        # Fetch SPY data
        spy = yf.Ticker("SPY")
        spy_hist = spy.history(start=(as_of_date - timedelta(days=60)).isoformat(),
                               end=as_of_date.isoformat())

        spy_trend = 'neutral'
        spy_momentum = None

        if len(spy_hist) > 20:
            # 20-day momentum
            spy_momentum = (spy_hist['Close'].iloc[-1] / spy_hist['Close'].iloc[-20] - 1) * 100

            # Trend: 20-day SMA vs 50-day SMA
            sma_20 = spy_hist['Close'].iloc[-20:].mean()
            if len(spy_hist) >= 50:
                sma_50 = spy_hist['Close'].iloc[-50:].mean()
                if sma_20 > sma_50 * 1.02:
                    spy_trend = 'bullish'
                elif sma_20 < sma_50 * 0.98:
                    spy_trend = 'bearish'

        # Market breadth (simplified - using SPY volume trend)
        market_breadth = 'neutral'
        if len(spy_hist) > 10:
            vol_recent = spy_hist['Volume'].iloc[-5:].mean()
            vol_prior = spy_hist['Volume'].iloc[-10:-5].mean()
            if vol_recent > vol_prior * 1.2 and spy_momentum and spy_momentum > 0:
                market_breadth = 'strong'
            elif vol_recent > vol_prior * 1.2 and spy_momentum and spy_momentum < 0:
                market_breadth = 'weak'

        return MarketRegime(
            vix_level=vix_level,
            vix_percentile=vix_percentile,
            vix_trend=vix_trend,
            spy_trend=spy_trend,
            spy_momentum=spy_momentum,
            market_breadth=market_breadth,
        )

    except Exception as e:
        logger.warning(f"Failed to get market regime: {e}")
        return MarketRegime()


# Cache market regime for 1 hour (it doesn't change that often)
# Uses bounded cache to prevent unbounded memory growth
_market_regime_cache: Dict[str, tuple] = {}
_CACHE_TTL = 3600  # 1 hour
_CACHE_MAX_SIZE = 100  # Maximum cache entries


def _evict_old_cache_entries(current_time: float) -> None:
    """Evict expired and excess cache entries."""
    global _market_regime_cache

    # First, remove expired entries
    expired_keys = [
        key for key, (_, cached_time) in _market_regime_cache.items()
        if current_time - cached_time >= _CACHE_TTL
    ]
    for key in expired_keys:
        del _market_regime_cache[key]

    # Then, if still over limit, remove oldest entries (LRU eviction)
    if len(_market_regime_cache) >= _CACHE_MAX_SIZE:
        sorted_keys = sorted(
            _market_regime_cache.keys(),
            key=lambda k: _market_regime_cache[k][1]  # Sort by timestamp
        )
        # Remove oldest 20% of entries
        keys_to_remove = sorted_keys[:len(sorted_keys) // 5 + 1]
        for key in keys_to_remove:
            del _market_regime_cache[key]


def get_market_features(as_of_date: date) -> Dict[str, float]:
    """
    Get market regime features for ML model.

    Uses bounded caching to avoid repeated API calls while preventing
    unbounded memory growth.

    Args:
        as_of_date: Reference date

    Returns:
        Dict of market features
    """
    import time

    cache_key = as_of_date.isoformat()
    current_time = time.time()

    # Check cache
    if cache_key in _market_regime_cache:
        cached_features, cached_time = _market_regime_cache[cache_key]
        if current_time - cached_time < _CACHE_TTL:
            return cached_features

    # Evict old entries before adding new one
    _evict_old_cache_entries(current_time)

    # Fetch fresh data
    regime = get_market_regime(as_of_date)
    features = regime.to_features()

    # Cache the result
    _market_regime_cache[cache_key] = (features, current_time)

    return features
