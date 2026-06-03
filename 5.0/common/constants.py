"""Shared constants for Trading Desk subsystems.

Single source of truth for VRP thresholds, liquidity tiers, scoring weights,
and sentiment modifiers. All subsystems import from here.

Sentiment modifier values and routing configuration are tuned via backtesting
and intentionally left as neutral defaults here. Configure for your own system.
"""

import os

# =============================================================================
# VRP Thresholds - BALANCED mode (default across all subsystems)
# =============================================================================
VRP_EXCELLENT = 1.8   # Top tier, high confidence
VRP_GOOD = 1.4        # Tradeable
VRP_MARGINAL = 1.2    # Minimum edge, size down
# < VRP_MARGINAL = SKIP (no edge)

# Minimum historical quarters for reliable VRP calculation
MIN_QUARTERS = 4

# VRP normalization: ratio at which VRP component reaches max score (100)
VRP_MAX_RATIO = 7.0


# =============================================================================
# Liquidity Thresholds
# =============================================================================
# Spread thresholds (bid-ask spread as % of mid price)
SPREAD_EXCELLENT = 12.0
SPREAD_GOOD = 18.0
SPREAD_WARNING = 25.0
# > SPREAD_WARNING = REJECT

# Tier ordering (for min/max comparisons)
TIER_ORDER = {"REJECT": 0, "WARNING": 1, "GOOD": 2, "EXCELLENT": 3}

# Liquidity tier scores for composite scoring
LIQUIDITY_SCORES = {
    "EXCELLENT": 100,
    "GOOD": 80,
    "WARNING": 60,
    "REJECT": 20,  # Still penalized but strategies allowed
}


# =============================================================================
# Scoring Weights - Composite score calculation
# =============================================================================
WEIGHT_VRP = 0.55
WEIGHT_MOVE = 0.25
WEIGHT_LIQUIDITY = 0.20


# =============================================================================
# Sentiment Modifiers - Applied to base score
# 4.0 Score = 2.0 Score x (1 + modifier)
# Tune these values based on your own sentiment accuracy backtesting.
# =============================================================================
SENTIMENT_STRONG_BULLISH_THRESHOLD = 0.6
SENTIMENT_BULLISH_THRESHOLD = 0.2
SENTIMENT_BEARISH_THRESHOLD = -0.2
SENTIMENT_STRONG_BEARISH_THRESHOLD = -0.6

SENTIMENT_MODIFIER_STRONG_BULLISH = 0.0
SENTIMENT_MODIFIER_BULLISH = 0.0
SENTIMENT_MODIFIER_NEUTRAL = 0.0
SENTIMENT_MODIFIER_BEARISH = 0.0
SENTIMENT_MODIFIER_STRONG_BEARISH = 0.0

# Sentiment directions with insufficient predictive signal — treated as neutral in routing.
# Populate based on your own sentiment accuracy analysis.
ZEROED_SENTIMENT_DIRECTIONS: frozenset = frozenset()


# =============================================================================
# Confidence Calculation
# =============================================================================
CONFIDENCE_DIVISOR = 0.6  # |score| / CONFIDENCE_DIVISOR → sentiment_strength (max 1.0)


# =============================================================================
# Contrarian Position Sizing
# Tune these thresholds based on your own backtest analysis.
# =============================================================================
STRONG_BULLISH_THRESHOLD = 0.6
STRONG_BEARISH_THRESHOLD = -0.6
SIZE_MODIFIER_BULLISH = 1.0   # Set below 1.0 to reduce size on strong bullish signals
SIZE_MODIFIER_BEARISH = 1.0   # Set above 1.0 to increase size on strong bearish signals
HIGH_BULLISH_WARNING_THRESHOLD = 0.7


# =============================================================================
# ORATS Feature Flag
# =============================================================================

# Set ORATS_ENABLED=false in .env to pause the ORATS subscription.
# All ORATS API calls are skipped; branched logic is preserved for re-enabling.
ORATS_ENABLED: bool = os.getenv("ORATS_ENABLED", "true").lower() == "true"


# =============================================================================
# ORATS Signal Fusion
# =============================================================================

# rSlp30 distribution (measured on position_limits tickers)
# Z-score normalization required: raw thresholds at 0 would classify ~60% as STRONG_BULLISH
RSLP30_MEAN = 1.036
RSLP30_STD  = 1.294

# Sorted breakpoints: (upper_bound, numeric_level) → first threshold exceeded wins
# Produces balanced distribution: 6.6% | 15.6% | 17.7% | 21.3% | 18.0% | 14.9% | 5.9%
RSLP30_THRESHOLDS = [
    (RSLP30_MEAN - 1.50 * RSLP30_STD, -3),  # < -0.905 → STRONG_BEARISH
    (RSLP30_MEAN - 0.75 * RSLP30_STD, -2),  # < +0.065 → BEARISH
    (RSLP30_MEAN - 0.25 * RSLP30_STD, -1),  # < +0.712 → WEAK_BEARISH
    (RSLP30_MEAN + 0.25 * RSLP30_STD,  0),  # < +1.359 → NEUTRAL
    (RSLP30_MEAN + 0.75 * RSLP30_STD, +1),  # < +2.006 → WEAK_BULLISH
    (RSLP30_MEAN + 1.50 * RSLP30_STD, +2),  # < +2.976 → BULLISH
    (float('inf'),                     +3),  # else      → STRONG_BULLISH
]

# At median Tradier R²=0.226, ORATS drives 69% of fused signal (0.5 / (0.226 + 0.5))
ORATS_SKEW_CONFIDENCE = 0.5

# iee/fcst ratio at which Rule B fires (reduce position 50%)
IEE_DIVERGENCE_RATIO  = 1.5

# Validated: 58.7% win +$218k below 2.0x vs 48.3% win -$231k above
FCST_ERN_IV_THRESHOLD = 2.0
