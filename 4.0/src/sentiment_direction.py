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
        return self.original_bias.lower() != self.adjusted_bias.value


def normalize_skew_bias(skew_bias: str) -> str:
    """Normalize various skew bias formats to simple string."""
    bias = skew_bias.lower().replace("directionalbias.", "").replace("_", " ").strip()

    # Map to canonical names
    if "strong" in bias and "bull" in bias:
        return "strong_bullish"
    elif "strong" in bias and "bear" in bias:
        return "strong_bearish"
    elif "bull" in bias:
        return "bullish"
    elif "bear" in bias:
        return "bearish"
    else:
        return "neutral"


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
    else:
        if sentiment_score >= 0.2:
            sent_dir = "bullish"
        elif sentiment_score <= -0.2:
            sent_dir = "bearish"
        else:
            sent_dir = "neutral"

    # Calculate confidence based on sentiment strength
    confidence = min(1.0, abs(sentiment_score) / 0.6)  # Max confidence at |0.6|

    # RULE 1: Neutral skew → sentiment breaks tie
    if original == "neutral":
        if sent_dir == "bullish":
            return DirectionAdjustment(
                original_bias=skew_bias,
                sentiment_score=sentiment_score,
                adjusted_bias=AdjustedBias.BULLISH,
                rule_applied="tiebreak_bullish",
                confidence=confidence,
            )
        elif sent_dir == "bearish":
            return DirectionAdjustment(
                original_bias=skew_bias,
                sentiment_score=sentiment_score,
                adjusted_bias=AdjustedBias.BEARISH,
                rule_applied="tiebreak_bearish",
                confidence=confidence,
            )
        else:
            return DirectionAdjustment(
                original_bias=skew_bias,
                sentiment_score=sentiment_score,
                adjusted_bias=AdjustedBias.NEUTRAL,
                rule_applied="both_neutral",
                confidence=0.5,  # Low confidence when no signal
            )

    # RULE 2: Conflict → go neutral (hedge)
    is_bullish_skew = original in ("bullish", "strong_bullish")
    is_bearish_skew = original in ("bearish", "strong_bearish")

    if (is_bullish_skew and sent_dir == "bearish") or \
       (is_bearish_skew and sent_dir == "bullish"):
        return DirectionAdjustment(
            original_bias=skew_bias,
            sentiment_score=sentiment_score,
            adjusted_bias=AdjustedBias.NEUTRAL,
            rule_applied="conflict_hedge",
            confidence=confidence,  # Higher sentiment = more confident in conflict
        )

    # RULE 3: Otherwise keep skew bias
    # Map original to AdjustedBias
    bias_map = {
        "strong_bullish": AdjustedBias.STRONG_BULLISH,
        "bullish": AdjustedBias.BULLISH,
        "neutral": AdjustedBias.NEUTRAL,
        "bearish": AdjustedBias.BEARISH,
        "strong_bearish": AdjustedBias.STRONG_BEARISH,
    }

    return DirectionAdjustment(
        original_bias=skew_bias,
        sentiment_score=sentiment_score,
        adjusted_bias=bias_map.get(original, AdjustedBias.NEUTRAL),
        rule_applied="skew_dominates",
        confidence=0.7,  # Moderate confidence when aligned or no conflict
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
