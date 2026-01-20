"""
Sentiment-adjusted directional bias.

Ported from 4.0/src/sentiment_direction.py.
Adjusts skew-based directional bias using AI sentiment signals.

3-Rule System:
1. Neutral skew + sentiment → sentiment breaks tie
2. Conflict (bullish skew + bearish sentiment) → go neutral (hedge)
3. Otherwise → keep skew bias
"""

from dataclasses import dataclass
from typing import Optional

from src.domain.skew import DirectionalBias


@dataclass
class DirectionAdjustment:
    """Result of sentiment-adjusted direction calculation."""
    original_bias: str       # Original skew bias
    sentiment_score: float   # AI sentiment score (-1 to +1)
    adjusted_bias: str       # Final direction (BULLISH/BEARISH/NEUTRAL)
    rule_applied: str        # Which rule was used
    confidence: float        # Adjustment confidence (0-1)

    @property
    def changed(self) -> bool:
        """Did sentiment change the direction?"""
        return _normalize_bias(self.original_bias) != self.adjusted_bias.lower()


def _normalize_bias(skew_bias: str) -> str:
    """
    Normalize skew bias to simple direction.

    Maps 7-level DirectionalBias to 3-level direction:
    - strong_bullish, bullish, weak_bullish → bullish
    - neutral → neutral
    - strong_bearish, bearish, weak_bearish → bearish
    """
    bias = skew_bias.lower().replace("_", " ").strip()

    if "bull" in bias:
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
    """Calculate confidence score for the adjustment."""
    # Base confidence from sentiment strength (0 to 1, max at |0.6|)
    sentiment_strength = min(1.0, abs(sentiment_score) / 0.6)

    if rule == "both_neutral":
        # Both skew and sentiment neutral - low confidence
        return 0.3 + (sentiment_strength * 0.2)

    if rule in ("tiebreak_bullish", "tiebreak_bearish"):
        # Sentiment breaking a neutral tie
        return sentiment_strength

    if rule == "conflict_hedge":
        # Conflicting signals - confidence in hedging scales with sentiment
        return sentiment_strength

    if rule == "skew_dominates":
        # Skew dominates - boost if sentiment aligns
        is_bullish_skew = original == "bullish"
        is_bearish_skew = original == "bearish"
        sentiment_aligns = (
            (is_bullish_skew and sent_dir == "bullish") or
            (is_bearish_skew and sent_dir == "bearish")
        )
        if sentiment_aligns:
            return min(1.0, 0.6 + (sentiment_strength * 0.4))
        else:
            return 0.6

    return 0.5


def adjust_direction(
    skew_bias: str,
    sentiment_score: float,
    sentiment_direction: Optional[str] = None,
) -> DirectionAdjustment:
    """
    Adjust directional bias using sentiment signal.

    Simple 3-rule system covering >99% of real cases.

    Args:
        skew_bias: Original bias from skew analyzer (e.g., "NEUTRAL", "BULLISH")
        sentiment_score: Sentiment score from -1.0 to +1.0
        sentiment_direction: Optional explicit direction from AI

    Returns:
        DirectionAdjustment with original, adjusted bias, and rule applied
    """
    # Normalize input
    original = _normalize_bias(skew_bias)

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

    # RULE 1: Neutral skew → sentiment breaks tie
    if original == "neutral":
        if sent_dir == "bullish":
            rule = "tiebreak_bullish"
            adjusted = "BULLISH"
        elif sent_dir == "bearish":
            rule = "tiebreak_bearish"
            adjusted = "BEARISH"
        else:
            rule = "both_neutral"
            adjusted = "NEUTRAL"

        return DirectionAdjustment(
            original_bias=skew_bias,
            sentiment_score=sentiment_score,
            adjusted_bias=adjusted,
            rule_applied=rule,
            confidence=_calculate_confidence(sentiment_score, rule, sent_dir, original),
        )

    # RULE 2: Conflict → go neutral (hedge)
    is_bullish_skew = original == "bullish"
    is_bearish_skew = original == "bearish"

    if (is_bullish_skew and sent_dir == "bearish") or \
       (is_bearish_skew and sent_dir == "bullish"):
        rule = "conflict_hedge"
        return DirectionAdjustment(
            original_bias=skew_bias,
            sentiment_score=sentiment_score,
            adjusted_bias="NEUTRAL",
            rule_applied=rule,
            confidence=_calculate_confidence(sentiment_score, rule, sent_dir, original),
        )

    # RULE 3: Otherwise keep skew bias
    rule = "skew_dominates"
    adjusted = original.upper()

    return DirectionAdjustment(
        original_bias=skew_bias,
        sentiment_score=sentiment_score,
        adjusted_bias=adjusted,
        rule_applied=rule,
        confidence=_calculate_confidence(sentiment_score, rule, sent_dir, original),
    )


def get_direction(
    skew_bias: Optional[str],
    sentiment_score: Optional[float],
    sentiment_direction: Optional[str] = None,
) -> str:
    """
    Get final direction from skew and sentiment.

    Convenience function that handles missing data gracefully.

    Args:
        skew_bias: Skew bias (can be None)
        sentiment_score: Sentiment score (can be None)
        sentiment_direction: Optional explicit direction

    Returns:
        Direction string: "BULLISH", "BEARISH", or "NEUTRAL"
    """
    # No skew data - use sentiment only
    if skew_bias is None:
        if sentiment_direction:
            return sentiment_direction.upper()
        if sentiment_score is not None:
            if sentiment_score >= 0.2:
                return "BULLISH"
            elif sentiment_score <= -0.2:
                return "BEARISH"
        return "NEUTRAL"

    # No sentiment - use skew only
    if sentiment_score is None and sentiment_direction is None:
        return _normalize_bias(skew_bias).upper()

    # Both available - use 3-rule adjustment
    result = adjust_direction(
        skew_bias=skew_bias,
        sentiment_score=sentiment_score or 0.0,
        sentiment_direction=sentiment_direction,
    )
    return result.adjusted_bias
