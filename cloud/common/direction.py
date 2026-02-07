"""
Sentiment-adjusted directional bias.

Canonical implementation of the 3-rule direction system.
Adjusts 2.0's skew-based directional bias using AI sentiment signals.

Rules:
1. Neutral skew + sentiment -> sentiment breaks tie
2. Conflict (bullish skew + bearish sentiment) -> go neutral (hedge)
3. Otherwise -> keep skew bias

Position Sizing (contrarian signal based on 2025 backtest data):
- Strong bullish sentiment correlates with LARGER moves (reduce size)
- Strong bearish sentiment correlates with SMALLER moves (increase size)
"""

from dataclasses import dataclass
from typing import Optional

from .enums import AdjustedBias
from .constants import (
    SENTIMENT_BULLISH_THRESHOLD,
    SENTIMENT_BEARISH_THRESHOLD,
    CONFIDENCE_DIVISOR,
    STRONG_BULLISH_THRESHOLD,
    STRONG_BEARISH_THRESHOLD,
    SIZE_MODIFIER_BULLISH,
    SIZE_MODIFIER_BEARISH,
    HIGH_BULLISH_WARNING_THRESHOLD,
)


@dataclass
class DirectionAdjustment:
    """Result of sentiment-adjusted direction calculation."""
    original_bias: str
    sentiment_score: float
    adjusted_bias: AdjustedBias
    rule_applied: str
    confidence: float  # 0-1, higher = more confident in adjustment
    size_modifier: float = 1.0  # Contrarian sizing: 0.9 for strong bullish, 1.1 for strong bearish

    @property
    def changed(self) -> bool:
        """Did sentiment change the direction?"""
        original_normalized = normalize_skew_bias(self.original_bias)
        return original_normalized != self.adjusted_bias.value

    @property
    def high_bullish_warning(self) -> bool:
        """Flag when sentiment is very bullish - correlates with larger moves."""
        return self.sentiment_score >= HIGH_BULLISH_WARNING_THRESHOLD


def get_size_modifier(sentiment_score: float) -> float:
    """
    Calculate contrarian position sizing modifier based on sentiment.

    Based on 2025 backtest analysis:
    - Strong bullish sentiment correlates with LARGER moves (avg 5.47%)
      -> Reduce position size to limit risk
    - Strong bearish sentiment correlates with SMALLER moves (avg 3.69%)
      -> Bad news priced in, increase position size

    CAUTION: Based on n=23 samples (as of Feb 2026). The bullish average is
    driven by 3 outliers (INTC -17%, ORCL -14.5%, MSFT -11.85%). Without
    outliers, strong bullish avg is only 3.09%. Treat as HYPOTHESIS until
    n=50+ samples collected. The HIGH_BULLISH_WARNING (>=0.7) for tail risk
    IS validated (23% of strong bullish had >10% crashes).

    Args:
        sentiment_score: Sentiment score from -1.0 to +1.0

    Returns:
        Size modifier: 0.9 for strong bullish, 1.1 for strong bearish, 1.0 otherwise
    """
    if sentiment_score >= STRONG_BULLISH_THRESHOLD:
        return SIZE_MODIFIER_BULLISH
    elif sentiment_score <= STRONG_BEARISH_THRESHOLD:
        return SIZE_MODIFIER_BEARISH
    return 1.0


def normalize_skew_bias(skew_bias: str) -> str:
    """Normalize various skew bias formats to simple string.

    Maps 2.0's 7-level system to 4.0's 5-level system:
    - STRONG_BULLISH -> strong_bullish
    - BULLISH -> bullish
    - WEAK_BULLISH -> bullish (treated as bullish for conflict detection)
    - NEUTRAL -> neutral
    - WEAK_BEARISH -> bearish (treated as bearish for conflict detection)
    - BEARISH -> bearish
    - STRONG_BEARISH -> strong_bearish
    """
    bias = skew_bias.lower().replace("directionalbias.", "").replace("_", " ").strip()

    # Map to canonical names (order matters - check "strong" first, then "weak")
    if "strong" in bias and "bull" in bias:
        return "strong_bullish"
    elif "strong" in bias and "bear" in bias:
        return "strong_bearish"
    elif "weak" in bias and "bull" in bias:
        return "bullish"  # Weak bullish -> treat as bullish
    elif "weak" in bias and "bear" in bias:
        return "bearish"  # Weak bearish -> treat as bearish
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
        return 0.3 + (sentiment_strength * 0.2)

    if rule in ("tiebreak_bullish", "tiebreak_bearish"):
        return sentiment_strength

    if rule == "conflict_hedge":
        return sentiment_strength

    if rule == "skew_dominates":
        is_bullish_skew = original in ("bullish", "strong_bullish")
        is_bearish_skew = original in ("bearish", "strong_bearish")
        sentiment_aligns = (
            (is_bullish_skew and sent_dir == "bullish") or
            (is_bearish_skew and sent_dir == "bearish")
        )
        if sentiment_aligns:
            return min(1.0, 0.6 + (sentiment_strength * 0.4))
        else:
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
        if sentiment_score >= SENTIMENT_BULLISH_THRESHOLD:
            sent_dir = "bullish"
        elif sentiment_score <= SENTIMENT_BEARISH_THRESHOLD:
            sent_dir = "bearish"
        else:
            sent_dir = "neutral"

    # RULE 1: Neutral skew -> sentiment breaks tie
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
            size_modifier=get_size_modifier(sentiment_score),
        )

    # RULE 2: Conflict -> go neutral (hedge)
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
            size_modifier=get_size_modifier(sentiment_score),
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
        size_modifier=get_size_modifier(sentiment_score),
    )


def format_adjustment(adj: DirectionAdjustment) -> str:
    """Format adjustment for display."""
    arrow = "→" if adj.changed else "="
    change_indicator = " (CHANGED)" if adj.changed else ""

    # Build size modifier display
    if adj.size_modifier < 1.0:
        size_display = f"  Size Modifier: {adj.size_modifier:.0%} (reduce - high bullish = larger moves)\n"
    elif adj.size_modifier > 1.0:
        size_display = f"  Size Modifier: {adj.size_modifier:.0%} (increase - bearish = priced in)\n"
    else:
        size_display = ""

    # High bullish warning
    warning = ""
    if adj.high_bullish_warning:
        warning = (
            "\n  HIGH BULLISH WARNING: Strong bullish sentiment (>=0.7)\n"
            "      correlates with LARGER moves. Consider reduced sizing."
        )

    return (
        f"Direction: {adj.original_bias} {arrow} {adj.adjusted_bias.value.upper()}{change_indicator}\n"
        f"  Sentiment: {adj.sentiment_score:+.2f}\n"
        f"  Rule: {adj.rule_applied}\n"
        f"  Confidence: {adj.confidence:.0%}\n"
        f"{size_display}{warning}"
    ).rstrip()


def quick_adjust(skew_bias: str, sentiment_score: float) -> str:
    """Quick adjustment returning just the adjusted bias string."""
    result = adjust_direction(skew_bias, sentiment_score)
    return result.adjusted_bias.value.upper()


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
            if sentiment_score >= SENTIMENT_BULLISH_THRESHOLD:
                return "BULLISH"
            elif sentiment_score <= SENTIMENT_BEARISH_THRESHOLD:
                return "BEARISH"
        return "NEUTRAL"

    # No sentiment - use skew only
    if sentiment_score is None and sentiment_direction is None:
        return normalize_skew_bias(skew_bias).upper()

    # Both available - use 3-rule adjustment
    result = adjust_direction(
        skew_bias=skew_bias,
        sentiment_score=sentiment_score or 0.0,
        sentiment_direction=sentiment_direction,
    )
    return result.adjusted_bias.value.upper()
