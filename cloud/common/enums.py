"""Shared enumerations for domain concepts.

Canonical source for all enums used across subsystems.
2.0/src/domain/enums.py re-exports these for backward compatibility.
"""

from enum import Enum


class EarningsTiming(Enum):
    """When earnings are announced relative to market hours."""

    BMO = "BMO"  # Before Market Open (pre-market announcement)
    AMC = "AMC"  # After Market Close (post-market announcement)
    DMH = "DMH"  # During Market Hours (rare)
    UNKNOWN = "UNKNOWN"


class OptionType(Enum):
    """Option contract type."""

    CALL = "call"
    PUT = "put"


class Action(Enum):
    """Trading action recommendation."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    SKIP = "SKIP"


class Recommendation(Enum):
    """Quality rating for VRP opportunities."""

    EXCELLENT = "excellent"  # VRP ratio >= 2.0x
    GOOD = "good"            # VRP ratio >= 1.5x
    MARGINAL = "marginal"    # VRP ratio >= 1.2x
    SKIP = "skip"            # VRP ratio < 1.2x


class MarketState(Enum):
    """Current market state."""

    PREMARKET = "premarket"    # 4:00 AM - 9:30 AM ET
    REGULAR = "regular"        # 9:30 AM - 4:00 PM ET
    AFTERHOURS = "afterhours"  # 4:00 PM - 8:00 PM ET
    CLOSED = "closed"          # 8:00 PM - 4:00 AM ET


class ExpirationCycle(Enum):
    """Option expiration cycle type."""

    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"


class SettlementType(Enum):
    """Option settlement timing."""

    AM = "AM"  # Settled at market open
    PM = "PM"  # Settled at market close


class StrategyType(Enum):
    """Options strategy types for earnings trades."""

    BULL_PUT_SPREAD = "bull_put_spread"      # Credit spread below price (bullish/neutral)
    BEAR_CALL_SPREAD = "bear_call_spread"    # Credit spread above price (bearish/neutral)
    IRON_CONDOR = "iron_condor"              # Dual credit spreads (neutral)
    IRON_BUTTERFLY = "iron_butterfly"        # Tight dual spreads at ATM (neutral)


class DirectionalBias(Enum):
    """
    Market directional bias from skew analysis with strength levels.

    Thresholds are defined in SkewAnalyzerEnhanced:
    - THRESHOLD_NEUTRAL = 0.3   (|slope| <= 0.3 -> NEUTRAL)
    - THRESHOLD_WEAK = 0.8      (0.3 < |slope| <= 0.8 -> WEAK)
    - THRESHOLD_STRONG = 1.5    (|slope| > 1.5 -> STRONG)
    - Default (0.8 < |slope| <= 1.5) -> MODERATE (no prefix)
    """

    STRONG_BEARISH = "strong_bearish"    # |slope| > THRESHOLD_STRONG, slope > 0 (puts very expensive)
    BEARISH = "bearish"                  # THRESHOLD_WEAK < |slope| <= THRESHOLD_STRONG, slope > 0
    WEAK_BEARISH = "weak_bearish"        # THRESHOLD_NEUTRAL < |slope| <= THRESHOLD_WEAK, slope > 0
    NEUTRAL = "neutral"                  # |slope| <= THRESHOLD_NEUTRAL (balanced IV)
    WEAK_BULLISH = "weak_bullish"        # THRESHOLD_NEUTRAL < |slope| <= THRESHOLD_WEAK, slope < 0
    BULLISH = "bullish"                  # THRESHOLD_WEAK < |slope| <= THRESHOLD_STRONG, slope < 0
    STRONG_BULLISH = "strong_bullish"    # |slope| > THRESHOLD_STRONG, slope < 0 (calls very expensive)

    def is_bullish(self) -> bool:
        """Check if bias is bullish (any strength)."""
        return self in {
            DirectionalBias.WEAK_BULLISH,
            DirectionalBias.BULLISH,
            DirectionalBias.STRONG_BULLISH,
        }

    def is_bearish(self) -> bool:
        """Check if bias is bearish (any strength)."""
        return self in {
            DirectionalBias.WEAK_BEARISH,
            DirectionalBias.BEARISH,
            DirectionalBias.STRONG_BEARISH,
        }

    def is_neutral(self) -> bool:
        """Check if bias is neutral."""
        return self == DirectionalBias.NEUTRAL

    def strength(self) -> int:
        """
        Return bias strength level.

        Returns:
            0 = NEUTRAL
            1 = WEAK
            2 = MODERATE (no prefix)
            3 = STRONG
        """
        if self == DirectionalBias.NEUTRAL:
            return 0
        elif self in {DirectionalBias.WEAK_BULLISH, DirectionalBias.WEAK_BEARISH}:
            return 1
        elif self in {DirectionalBias.BULLISH, DirectionalBias.BEARISH}:
            return 2
        else:  # STRONG
            return 3


class AdjustedBias(Enum):
    """Simplified 5-level bias for sentiment adjustment (maps to DirectionalBias)."""
    STRONG_BULLISH = "strong_bullish"
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    STRONG_BEARISH = "strong_bearish"

    def is_bullish(self) -> bool:
        return self in {AdjustedBias.BULLISH, AdjustedBias.STRONG_BULLISH}

    def is_bearish(self) -> bool:
        return self in {AdjustedBias.BEARISH, AdjustedBias.STRONG_BEARISH}
