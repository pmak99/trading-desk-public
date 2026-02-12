# cloud/tests/test_direction.py
"""Tests for direction adjustment module (3-rule system).

Uses common/direction.py canonical implementation.
"""
import pytest
from src.domain.direction import (
    adjust_direction,
    get_direction,
    DirectionAdjustment,
    normalize_skew_bias,
    AdjustedBias,
)


class TestNormalizeSkewBias:
    """Tests for normalize_skew_bias function (5-level normalization)."""

    def test_normalizes_strong_bullish(self):
        assert normalize_skew_bias("strong_bullish") == "strong_bullish"
        assert normalize_skew_bias("STRONG_BULLISH") == "strong_bullish"

    def test_normalizes_weak_bullish_to_bullish(self):
        """Weak variants collapse to moderate for conflict detection."""
        assert normalize_skew_bias("weak_bullish") == "bullish"

    def test_normalizes_bullish(self):
        assert normalize_skew_bias("bullish") == "bullish"
        assert normalize_skew_bias("BULLISH") == "bullish"

    def test_normalizes_bearish_variants(self):
        assert normalize_skew_bias("strong_bearish") == "strong_bearish"
        assert normalize_skew_bias("weak_bearish") == "bearish"
        assert normalize_skew_bias("BEARISH") == "bearish"

    def test_normalizes_neutral(self):
        assert normalize_skew_bias("neutral") == "neutral"
        assert normalize_skew_bias("NEUTRAL") == "neutral"

    def test_normalizes_directionalbias_prefix(self):
        assert normalize_skew_bias("DirectionalBias.STRONG_BULLISH") == "strong_bullish"


class TestAdjustDirection:
    """Tests for adjust_direction function (3-rule system)."""

    # RULE 1: Neutral skew + sentiment -> sentiment breaks tie

    def test_rule1_neutral_skew_bullish_sentiment(self):
        """Neutral skew + bullish sentiment -> bullish."""
        result = adjust_direction("neutral", 0.5, "bullish")
        assert result.adjusted_bias == AdjustedBias.BULLISH
        assert result.rule_applied == "tiebreak_bullish"

    def test_rule1_neutral_skew_bearish_sentiment(self):
        """Neutral skew + bearish sentiment -> bearish."""
        result = adjust_direction("neutral", -0.5, "bearish")
        assert result.adjusted_bias == AdjustedBias.BEARISH
        assert result.rule_applied == "tiebreak_bearish"

    def test_rule1_neutral_skew_neutral_sentiment(self):
        """Neutral skew + neutral sentiment -> neutral."""
        result = adjust_direction("neutral", 0.0, "neutral")
        assert result.adjusted_bias == AdjustedBias.NEUTRAL
        assert result.rule_applied == "both_neutral"

    def test_rule1_infers_direction_from_score(self):
        """Direction inferred from score when not provided."""
        # Score >= 0.2 -> bullish
        result = adjust_direction("neutral", 0.3)
        assert result.adjusted_bias == AdjustedBias.BULLISH

        # Score <= -0.2 -> bearish
        result = adjust_direction("neutral", -0.3)
        assert result.adjusted_bias == AdjustedBias.BEARISH

        # -0.2 < score < 0.2 -> neutral
        result = adjust_direction("neutral", 0.1)
        assert result.adjusted_bias == AdjustedBias.NEUTRAL

    # RULE 2: Conflict -> go neutral (hedge)

    def test_rule2_bullish_skew_bearish_sentiment(self):
        """Bullish skew + bearish sentiment -> neutral (conflict)."""
        result = adjust_direction("bullish", -0.5, "bearish")
        assert result.adjusted_bias == AdjustedBias.NEUTRAL
        assert result.rule_applied == "conflict_hedge"

    def test_rule2_bearish_skew_bullish_sentiment(self):
        """Bearish skew + bullish sentiment -> neutral (conflict)."""
        result = adjust_direction("bearish", 0.5, "bullish")
        assert result.adjusted_bias == AdjustedBias.NEUTRAL
        assert result.rule_applied == "conflict_hedge"

    def test_rule2_strong_bullish_bearish_sentiment(self):
        """Strong bullish skew + bearish sentiment -> neutral."""
        result = adjust_direction("strong_bullish", -0.7, "bearish")
        assert result.adjusted_bias == AdjustedBias.NEUTRAL
        assert result.rule_applied == "conflict_hedge"

    # RULE 3: Otherwise keep skew bias

    def test_rule3_bullish_skew_bullish_sentiment(self):
        """Bullish skew + bullish sentiment -> bullish (aligned)."""
        result = adjust_direction("bullish", 0.5, "bullish")
        assert result.adjusted_bias == AdjustedBias.BULLISH
        assert result.rule_applied == "skew_dominates"

    def test_rule3_bearish_skew_bearish_sentiment(self):
        """Bearish skew + bearish sentiment -> bearish (aligned)."""
        result = adjust_direction("bearish", -0.5, "bearish")
        assert result.adjusted_bias == AdjustedBias.BEARISH
        assert result.rule_applied == "skew_dominates"

    def test_rule3_bullish_skew_neutral_sentiment(self):
        """Bullish skew + neutral sentiment -> bullish."""
        result = adjust_direction("bullish", 0.0, "neutral")
        assert result.adjusted_bias == AdjustedBias.BULLISH
        assert result.rule_applied == "skew_dominates"

    def test_rule3_strong_bearish_skew_neutral_sentiment(self):
        """Strong bearish skew + neutral sentiment -> strong_bearish (preserves strength)."""
        result = adjust_direction("strong_bearish", 0.1, "neutral")
        assert result.adjusted_bias == AdjustedBias.STRONG_BEARISH
        assert result.rule_applied == "skew_dominates"


class TestDirectionAdjustmentChanged:
    """Tests for changed property."""

    def test_changed_when_direction_differs(self):
        """Changed is True when adjusted differs from original."""
        result = adjust_direction("bullish", -0.5, "bearish")
        assert result.changed is True  # bullish -> neutral

    def test_not_changed_when_same(self):
        """Changed is False when adjusted matches original."""
        result = adjust_direction("bullish", 0.5, "bullish")
        assert result.changed is False  # bullish -> bullish


class TestGetDirection:
    """Tests for get_direction convenience function."""

    def test_no_skew_uses_sentiment_direction(self):
        """Without skew, sentiment direction is used."""
        result = get_direction(None, 0.5, "bullish")
        assert result == "BULLISH"

    def test_no_skew_uses_sentiment_score(self):
        """Without skew or direction, score determines direction."""
        assert get_direction(None, 0.5) == "BULLISH"
        assert get_direction(None, -0.5) == "BEARISH"
        assert get_direction(None, 0.0) == "NEUTRAL"

    def test_no_sentiment_uses_skew(self):
        """Without sentiment, skew is used."""
        assert get_direction("bullish", None) == "BULLISH"
        assert get_direction("strong_bearish", None) == "STRONG_BEARISH"
        assert get_direction("neutral", None) == "NEUTRAL"

    def test_both_none_returns_neutral(self):
        """Both None returns neutral."""
        assert get_direction(None, None) == "NEUTRAL"

    def test_uses_3_rule_adjustment(self):
        """With both, uses 3-rule adjustment."""
        # Rule 2: conflict -> neutral
        assert get_direction("bullish", -0.5, "bearish") == "NEUTRAL"

        # Rule 3: aligned -> keep skew
        assert get_direction("bearish", -0.5, "bearish") == "BEARISH"


class TestConfidence:
    """Tests for confidence calculation."""

    def test_high_sentiment_high_confidence(self):
        """Strong sentiment signal gives high confidence."""
        result = adjust_direction("neutral", 0.6, "bullish")
        assert result.confidence >= 0.8

    def test_weak_sentiment_lower_confidence(self):
        """Weak sentiment signal gives lower confidence."""
        result = adjust_direction("neutral", 0.2, "bullish")
        assert result.confidence < 0.5

    def test_both_neutral_low_confidence(self):
        """Both neutral gives low confidence."""
        result = adjust_direction("neutral", 0.0, "neutral")
        assert result.confidence < 0.5


class TestSizeModifier:
    """Tests for contrarian position sizing."""

    def test_strong_bullish_reduces_size(self):
        """Strong bullish sentiment -> reduce size (larger moves expected)."""
        result = adjust_direction("neutral", 0.7, "bullish")
        assert result.size_modifier == 0.9

    def test_strong_bearish_increases_size(self):
        """Strong bearish sentiment -> increase size (priced in)."""
        result = adjust_direction("neutral", -0.7, "bearish")
        assert result.size_modifier == 1.1

    def test_moderate_sentiment_neutral_sizing(self):
        """Moderate sentiment -> no size adjustment."""
        result = adjust_direction("neutral", 0.3, "bullish")
        assert result.size_modifier == 1.0
