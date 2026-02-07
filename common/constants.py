"""Shared constants for Trading Desk subsystems.

Single source of truth for VRP thresholds, liquidity tiers, scoring weights,
and sentiment modifiers. All subsystems import from here.
"""

# =============================================================================
# VRP Thresholds - BALANCED mode (default across all subsystems)
# =============================================================================
VRP_EXCELLENT = 1.8   # Top tier, high confidence
VRP_GOOD = 1.4        # Tradeable
VRP_MARGINAL = 1.2    # Minimum edge, size down
# < 1.2 = SKIP (no edge)

# Minimum historical quarters for reliable VRP calculation
MIN_QUARTERS = 4

# VRP normalization: ratio at which VRP component reaches max score (100)
VRP_MAX_RATIO = 7.0


# =============================================================================
# Liquidity Thresholds - RELAXED Feb 2026
# =============================================================================
# Spread thresholds (bid-ask spread as % of mid price)
SPREAD_EXCELLENT = 12.0   # <= 12%
SPREAD_GOOD = 18.0        # <= 18%
SPREAD_WARNING = 25.0     # <= 25%
# > 25% = REJECT

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
# =============================================================================
SENTIMENT_STRONG_BULLISH_THRESHOLD = 0.6
SENTIMENT_BULLISH_THRESHOLD = 0.2
SENTIMENT_BEARISH_THRESHOLD = -0.2
SENTIMENT_STRONG_BEARISH_THRESHOLD = -0.6

SENTIMENT_MODIFIER_STRONG_BULLISH = 0.12   # +12%
SENTIMENT_MODIFIER_BULLISH = 0.07          # +7%
SENTIMENT_MODIFIER_NEUTRAL = 0.0           # 0%
SENTIMENT_MODIFIER_BEARISH = -0.07         # -7%
SENTIMENT_MODIFIER_STRONG_BEARISH = -0.12  # -12%


# =============================================================================
# Confidence Calculation
# =============================================================================
CONFIDENCE_DIVISOR = 0.6  # |score| / CONFIDENCE_DIVISOR → sentiment_strength (max 1.0)


# =============================================================================
# Contrarian Position Sizing (based on 2025 backtest analysis)
# =============================================================================
STRONG_BULLISH_THRESHOLD = 0.6   # Score >= 0.6 triggers size reduction
STRONG_BEARISH_THRESHOLD = -0.6  # Score <= -0.6 triggers size increase
SIZE_MODIFIER_BULLISH = 0.9      # 10% size reduction for strong bullish
SIZE_MODIFIER_BEARISH = 1.1      # 10% size increase for strong bearish
HIGH_BULLISH_WARNING_THRESHOLD = 0.7  # Score >= 0.7 triggers warning


# =============================================================================
# Budget Limits
# =============================================================================
PERPLEXITY_DAILY_LIMIT = 40
PERPLEXITY_MONTHLY_BUDGET = 5.00
PERPLEXITY_WARN_THRESHOLD = 0.80  # 80% = 32 calls
PERPLEXITY_COST_PER_CALL_ESTIMATE = 0.006
