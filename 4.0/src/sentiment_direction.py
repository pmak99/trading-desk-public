"""
Sentiment-adjusted directional bias.

Adjusts 2.0's skew-based directional bias using AI sentiment signals.
Simple 3-rule system covering >99% of real cases.

Rules:
1. Neutral skew + sentiment → sentiment breaks tie
2. Conflict (bullish skew + bearish sentiment) → go neutral (hedge)
3. Otherwise → keep skew bias
"""

from dataclasses import dataclass
from typing import Optional
from enum import Enum


# Sentiment classification thresholds
# These determine when a sentiment score is considered bullish/bearish vs neutral
SENTIMENT_BULLISH_THRESHOLD = 0.2   # Score >= 0.2 is bullish
SENTIMENT_BEARISH_THRESHOLD = -0.2  # Score <= -0.2 is bearish

# Confidence calculation parameters
# CONFIDENCE_DIVISOR: sentiment_strength = min(1.0, abs(score) / CONFIDENCE_DIVISOR)
# At |score| = 0.6, sentiment_strength = 1.0 (max confidence from sentiment alone)
CONFIDENCE_DIVISOR = 0.6


class AdjustedBias(Enum):
    """Simplified bias for 4.0 (maps to 2.0 DirectionalBias)."""
    STRONG_BULLISH = "strong_bullish"
    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    STRONG_BEARISH = "strong_bearish"

    def is_bullish(self) -> bool:
        return self in {AdjustedBias.BULLISH, AdjustedBias.STRONG_BULLISH}

    def is_bearish(self) -> bool:
        return self in {AdjustedBias.BEARISH, AdjustedBias.STRONG_BEARISH}


@dataclass
class DirectionAdjustment:
    """Result of sentiment-adjusted direction calculation."""
    original_bias: str
    sentiment_score: float
    adjusted_bias: AdjustedBias
    rule_applied: str
    confidence: float  # 0-1, higher = more confident in adjustment

    @property
    def changed(self) -> bool:
        """Did sentiment change the direction?"""
        # Normalize original for proper comparison
        original_normalized = normalize_skew_bias(self.original_bias)
        return original_normalized != self.adjusted_bias.value


def normalize_skew_bias(skew_bias: str) -> str:
    """Normalize various skew bias formats to simple string.

    Maps 2.0's 7-level system to 4.0's 5-level system:
    - STRONG_BULLISH → strong_bullish
    - BULLISH → bullish
    - WEAK_BULLISH → bullish (treated as bullish for conflict detection)
    - NEUTRAL → neutral
    - WEAK_BEARISH → bearish (treated as bearish for conflict detection)
    - BEARISH → bearish
    - STRONG_BEARISH → strong_bearish
    """
    bias = skew_bias.lower().replace("directionalbias.", "").replace("_", " ").strip()

    # Map to canonical names (order matters - check "strong" first, then "weak")
    if "strong" in bias and "bull" in bias:
        return "strong_bullish"
    elif "strong" in bias and "bear" in bias:
        return "strong_bearish"
    elif "weak" in bias and "bull" in bias:
        return "bullish"  # Weak bullish → treat as bullish
    elif "weak" in bias and "bear" in bias:
        return "bearish"  # Weak bearish → treat as bearish
    elif "bull" in bias:
        return "bullish"
    elif "bear" in bias:
        return "bearish"
    else:
        return "neutral"


def _calculate_confidence(
    sentiment_score: float,
    rule: str,
    sent_dir: str,
    original: str,
) -> float:
    """
    Calculate confidence score consistently across all rules.

    Confidence reflects how sure we are about the adjustment decision.

    Args:
        sentiment_score: Raw sentiment score (-1 to +1)
        rule: Which rule was applied
        sent_dir: Sentiment direction (bullish/bearish/neutral)
        original: Normalized original skew bias

    Returns:
        Confidence score between 0.0 and 1.0
    """
    # Base confidence from sentiment strength (0 to 1, max at |CONFIDENCE_DIVISOR|)
    sentiment_strength = min(1.0, abs(sentiment_score) / CONFIDENCE_DIVISOR)

    if rule == "both_neutral":
        # Both skew and sentiment are neutral - low confidence in any direction
        # Base 0.3 + small boost from any weak sentiment signal
        return 0.3 + (sentiment_strength * 0.2)

    if rule in ("tiebreak_bullish", "tiebreak_bearish"):
        # Sentiment breaking a neutral tie - confidence scales with sentiment strength
        return sentiment_strength

    if rule == "conflict_hedge":
        # Conflicting signals - confidence in hedging scales with sentiment strength
        return sentiment_strength

    if rule == "skew_dominates":
        # Skew dominates - base 0.6 + boost if sentiment aligns
        is_bullish_skew = original in ("bullish", "strong_bullish")
        is_bearish_skew = original in ("bearish", "strong_bearish")
        sentiment_aligns = (
            (is_bullish_skew and sent_dir == "bullish") or
            (is_bearish_skew and sent_dir == "bearish")
        )
        if sentiment_aligns:
            # Aligned sentiment boosts confidence
            return min(1.0, 0.6 + (sentiment_strength * 0.4))
        else:
            # Neutral sentiment - moderate base confidence
            return 0.6

    # Fallback (shouldn't reach here)
    return 0.5


def adjust_direction(
    skew_bias: str,
    sentiment_score: float,
    sentiment_direction: Optional[str] = None,
) -> DirectionAdjustment:
    """
    Adjust directional bias using sentiment signal.

    Simple 3-rule version covering >99% of real cases.

    Args:
        skew_bias: Original bias from 2.0 skew analyzer
                   (e.g., "NEUTRAL", "BULLISH", "DirectionalBias.STRONG_BULLISH")
        sentiment_score: Sentiment score from -1.0 to +1.0
        sentiment_direction: Optional explicit direction ("bullish", "bearish", "neutral")

    Returns:
        DirectionAdjustment with original, adjusted bias, and rule applied
    """
    # Normalize input
    original = normalize_skew_bias(skew_bias)

    # Determine sentiment direction from score if not provided
    if sentiment_direction:
        sent_dir = sentiment_direction.lower()
        valid_directions = {"bullish", "bearish", "neutral"}
        if sent_dir not in valid_directions:
            raise ValueError(
                f"Invalid sentiment_direction '{sentiment_direction}'. "
                f"Must be one of: {valid_directions}"
            )
    else:
        # Use configurable thresholds for sentiment classification
        if sentiment_score >= SENTIMENT_BULLISH_THRESHOLD:
            sent_dir = "bullish"
        elif sentiment_score <= SENTIMENT_BEARISH_THRESHOLD:
            sent_dir = "bearish"
        else:
            sent_dir = "neutral"

    # RULE 1: Neutral skew → sentiment breaks tie
    if original == "neutral":
        if sent_dir == "bullish":
            rule = "tiebreak_bullish"
            adjusted = AdjustedBias.BULLISH
        elif sent_dir == "bearish":
            rule = "tiebreak_bearish"
            adjusted = AdjustedBias.BEARISH
        else:
            rule = "both_neutral"
            adjusted = AdjustedBias.NEUTRAL

        return DirectionAdjustment(
            original_bias=skew_bias,
            sentiment_score=sentiment_score,
            adjusted_bias=adjusted,
            rule_applied=rule,
            confidence=_calculate_confidence(sentiment_score, rule, sent_dir, original),
        )

    # RULE 2: Conflict → go neutral (hedge)
    is_bullish_skew = original in ("bullish", "strong_bullish")
    is_bearish_skew = original in ("bearish", "strong_bearish")

    if (is_bullish_skew and sent_dir == "bearish") or \
       (is_bearish_skew and sent_dir == "bullish"):
        rule = "conflict_hedge"
        return DirectionAdjustment(
            original_bias=skew_bias,
            sentiment_score=sentiment_score,
            adjusted_bias=AdjustedBias.NEUTRAL,
            rule_applied=rule,
            confidence=_calculate_confidence(sentiment_score, rule, sent_dir, original),
        )

    # RULE 3: Otherwise keep skew bias
    bias_map = {
        "strong_bullish": AdjustedBias.STRONG_BULLISH,
        "bullish": AdjustedBias.BULLISH,
        "neutral": AdjustedBias.NEUTRAL,
        "bearish": AdjustedBias.BEARISH,
        "strong_bearish": AdjustedBias.STRONG_BEARISH,
    }

    rule = "skew_dominates"
    return DirectionAdjustment(
        original_bias=skew_bias,
        sentiment_score=sentiment_score,
        adjusted_bias=bias_map.get(original, AdjustedBias.NEUTRAL),
        rule_applied=rule,
        confidence=_calculate_confidence(sentiment_score, rule, sent_dir, original),
    )


def format_adjustment(adj: DirectionAdjustment) -> str:
    """Format adjustment for display."""
    arrow = "→" if adj.changed else "="
    change_indicator = " (CHANGED)" if adj.changed else ""

    return (
        f"Direction: {adj.original_bias} {arrow} {adj.adjusted_bias.value.upper()}{change_indicator}\n"
        f"  Sentiment: {adj.sentiment_score:+.2f}\n"
        f"  Rule: {adj.rule_applied}\n"
        f"  Confidence: {adj.confidence:.0%}"
    )


# Convenience function for quick adjustment
def quick_adjust(skew_bias: str, sentiment_score: float) -> str:
    """Quick adjustment returning just the adjusted bias string."""
    result = adjust_direction(skew_bias, sentiment_score)
    return result.adjusted_bias.value.upper()
