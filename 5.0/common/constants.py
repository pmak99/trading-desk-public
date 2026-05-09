"""Shared constants for Trading Desk subsystems.

Single source of truth for VRP thresholds, liquidity tiers, scoring weights,
and sentiment modifiers. All subsystems import from here.

Sentiment modifier values and routing configuration are tuned via backtesting
and intentionally left as neutral defaults here. Configure for your own system.
"""

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
