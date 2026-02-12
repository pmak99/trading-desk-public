"""
Unit tests for sentiment sentiment-adjusted directional bias.

Tests the 3-rule system:
1. Neutral skew + sentiment → sentiment breaks tie
2. Conflict (bullish skew + bearish sentiment) → go neutral (hedge)
3. Otherwise → keep skew bias
"""

import pytest
import sys
from pathlib import Path

# Add sentiment/src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sentiment_direction import (
    adjust_direction,
    normalize_skew_bias,
    quick_adjust,
    format_adjustment,
    AdjustedBias,
    DirectionAdjustment,
)


class TestNormalizeSkewBias:
    """Tests for normalizing various skew bias input formats."""

    def test_simple_uppercase(self):
        """Standard uppercase input."""
        assert normalize_skew_bias("NEUTRAL") == "neutral"
        assert normalize_skew_bias("BULLISH") == "bullish"
        assert normalize_skew_bias("BEARISH") == "bearish"
        assert normalize_skew_bias("STRONG_BULLISH") == "strong_bullish"
        assert normalize_skew_bias("STRONG_BEARISH") == "strong_bearish"

    def test_directional_bias_prefix(self):
        """Input with DirectionalBias. prefix from 2.0."""
        assert normalize_skew_bias("DirectionalBias.NEUTRAL") == "neutral"
        assert normalize_skew_bias("DirectionalBias.BULLISH") == "bullish"
        assert normalize_skew_bias("DirectionalBias.STRONG_BULLISH") == "strong_bullish"

    def test_weak_bias_mapping(self):
        """WEAK_BULLISH/BEARISH should map to bullish/bearish."""
        assert normalize_skew_bias("WEAK_BULLISH") == "bullish"
        assert normalize_skew_bias("WEAK_BEARISH") == "bearish"
        assert normalize_skew_bias("DirectionalBias.WEAK_BULLISH") == "bullish"
        assert normalize_skew_bias("DirectionalBias.WEAK_BEARISH") == "bearish"

    def test_lowercase_and_spaces(self):
        """Handles lowercase and various formats."""
        assert normalize_skew_bias("neutral") == "neutral"
        assert normalize_skew_bias("strong bullish") == "strong_bullish"
        assert normalize_skew_bias("strong bearish") == "strong_bearish"

    def test_unknown_defaults_neutral(self):
        """Unknown input defaults to neutral."""
        assert normalize_skew_bias("unknown") == "neutral"
        assert normalize_skew_bias("") == "neutral"
        assert normalize_skew_bias("random_text") == "neutral"


class TestRule1TiebreakNeutralSkew:
    """Rule 1: Neutral skew + sentiment → sentiment breaks tie."""

    def test_neutral_skew_bullish_sentiment(self):
        """Neutral skew + bullish sentiment → BULLISH."""
        adj = adjust_direction("NEUTRAL", 0.4)
        assert adj.adjusted_bias == AdjustedBias.BULLISH
        assert adj.rule_applied == "tiebreak_bullish"
        assert adj.changed is True

    def test_neutral_skew_strong_bullish_sentiment(self):
        """Neutral skew + strong bullish sentiment → BULLISH."""
        adj = adjust_direction("NEUTRAL", 0.8)
        assert adj.adjusted_bias == AdjustedBias.BULLISH
        assert adj.rule_applied == "tiebreak_bullish"
        assert adj.confidence >= 1.0  # Max confidence at 0.6+

    def test_neutral_skew_bearish_sentiment(self):
        """Neutral skew + bearish sentiment → BEARISH."""
        adj = adjust_direction("NEUTRAL", -0.4)
        assert adj.adjusted_bias == AdjustedBias.BEARISH
        assert adj.rule_applied == "tiebreak_bearish"
        assert adj.changed is True

    def test_neutral_skew_strong_bearish_sentiment(self):
        """Neutral skew + strong bearish sentiment → BEARISH."""
        adj = adjust_direction("NEUTRAL", -0.7)
        assert adj.adjusted_bias == AdjustedBias.BEARISH
        assert adj.rule_applied == "tiebreak_bearish"

    def test_neutral_skew_neutral_sentiment(self):
        """Neutral skew + neutral sentiment → NEUTRAL."""
        adj = adjust_direction("NEUTRAL", 0.1)
        assert adj.adjusted_bias == AdjustedBias.NEUTRAL
        assert adj.rule_applied == "both_neutral"
        assert adj.changed is False

    def test_neutral_skew_edge_bullish(self):
        """Neutral skew + sentiment at edge (0.2) → BULLISH."""
        adj = adjust_direction("NEUTRAL", 0.2)
        assert adj.adjusted_bias == AdjustedBias.BULLISH
        assert adj.rule_applied == "tiebreak_bullish"

    def test_neutral_skew_edge_bearish(self):
        """Neutral skew + sentiment at edge (-0.2) → BEARISH (boundary is inclusive, symmetric with bullish >= 0.2)."""
        adj = adjust_direction("NEUTRAL", -0.2)
        assert adj.adjusted_bias == AdjustedBias.BEARISH
        assert adj.rule_applied == "tiebreak_bearish"


class TestRule2ConflictHedge:
    """Rule 2: Conflict (opposite directions) → go neutral."""

    def test_bullish_skew_bearish_sentiment(self):
        """Bullish skew + bearish sentiment → NEUTRAL (conflict)."""
        adj = adjust_direction("BULLISH", -0.3)
        assert adj.adjusted_bias == AdjustedBias.NEUTRAL
        assert adj.rule_applied == "conflict_hedge"
        assert adj.changed is True

    def test_strong_bullish_skew_bearish_sentiment(self):
        """Strong bullish skew + bearish sentiment → NEUTRAL (conflict)."""
        adj = adjust_direction("STRONG_BULLISH", -0.4)
        assert adj.adjusted_bias == AdjustedBias.NEUTRAL
        assert adj.rule_applied == "conflict_hedge"

    def test_bearish_skew_bullish_sentiment(self):
        """Bearish skew + bullish sentiment → NEUTRAL (conflict)."""
        adj = adjust_direction("BEARISH", 0.5)
        assert adj.adjusted_bias == AdjustedBias.NEUTRAL
        assert adj.rule_applied == "conflict_hedge"
        assert adj.changed is True

    def test_strong_bearish_skew_bullish_sentiment(self):
        """Strong bearish skew + bullish sentiment → NEUTRAL (conflict)."""
        adj = adjust_direction("STRONG_BEARISH", 0.6)
        assert adj.adjusted_bias == AdjustedBias.NEUTRAL
        assert adj.rule_applied == "conflict_hedge"

    def test_weak_bullish_skew_bearish_sentiment(self):
        """Weak bullish skew + bearish sentiment → NEUTRAL (conflict)."""
        # WEAK_BULLISH normalizes to bullish
        adj = adjust_direction("WEAK_BULLISH", -0.3)
        assert adj.adjusted_bias == AdjustedBias.NEUTRAL
        assert adj.rule_applied == "conflict_hedge"


class TestRule3SkewDominates:
    """Rule 3: Aligned or no conflict → keep skew bias."""

    def test_bullish_skew_bullish_sentiment(self):
        """Bullish skew + bullish sentiment → BULLISH (aligned)."""
        adj = adjust_direction("BULLISH", 0.4)
        assert adj.adjusted_bias == AdjustedBias.BULLISH
        assert adj.rule_applied == "skew_dominates"
        assert adj.changed is False

    def test_strong_bullish_skew_bullish_sentiment(self):
        """Strong bullish skew + bullish sentiment → STRONG_BULLISH (aligned)."""
        adj = adjust_direction("STRONG_BULLISH", 0.5)
        assert adj.adjusted_bias == AdjustedBias.STRONG_BULLISH
        assert adj.rule_applied == "skew_dominates"

    def test_bearish_skew_bearish_sentiment(self):
        """Bearish skew + bearish sentiment → BEARISH (aligned)."""
        adj = adjust_direction("BEARISH", -0.4)
        assert adj.adjusted_bias == AdjustedBias.BEARISH
        assert adj.rule_applied == "skew_dominates"
        assert adj.changed is False

    def test_bullish_skew_neutral_sentiment(self):
        """Bullish skew + neutral sentiment → BULLISH (skew dominates)."""
        adj = adjust_direction("BULLISH", 0.1)
        assert adj.adjusted_bias == AdjustedBias.BULLISH
        assert adj.rule_applied == "skew_dominates"

    def test_bearish_skew_neutral_sentiment(self):
        """Bearish skew + neutral sentiment → BEARISH (skew dominates)."""
        adj = adjust_direction("BEARISH", -0.1)
        assert adj.adjusted_bias == AdjustedBias.BEARISH
        assert adj.rule_applied == "skew_dominates"


class TestConfidenceScoring:
    """Tests for confidence calculation."""

    def test_strong_sentiment_high_confidence(self):
        """Strong sentiment (±0.6+) should have high confidence."""
        adj = adjust_direction("NEUTRAL", 0.6)
        assert adj.confidence >= 1.0

    def test_moderate_sentiment_moderate_confidence(self):
        """Moderate sentiment should have moderate confidence."""
        adj = adjust_direction("NEUTRAL", 0.3)
        assert 0.4 <= adj.confidence <= 0.6

    def test_weak_sentiment_low_confidence(self):
        """Weak sentiment should have low confidence."""
        adj = adjust_direction("NEUTRAL", 0.1)
        # both_neutral rule: base 0.3 + small boost from weak signal
        assert 0.3 <= adj.confidence <= 0.4


class TestChangedProperty:
    """Tests for the changed property detection."""

    def test_changed_when_direction_differs(self):
        """changed=True when direction actually changes."""
        adj = adjust_direction("NEUTRAL", 0.5)
        assert adj.changed is True

    def test_not_changed_when_same(self):
        """changed=False when direction stays the same."""
        adj = adjust_direction("BULLISH", 0.5)
        assert adj.changed is False

    def test_changed_on_conflict(self):
        """changed=True on conflict (BULLISH → NEUTRAL)."""
        adj = adjust_direction("BULLISH", -0.3)
        assert adj.changed is True

    def test_not_changed_directional_bias_format(self):
        """Handles DirectionalBias. prefix correctly for changed detection."""
        adj = adjust_direction("DirectionalBias.BULLISH", 0.5)
        assert adj.changed is False  # Still bullish


class TestQuickAdjust:
    """Tests for quick_adjust convenience function."""

    def test_quick_adjust_returns_uppercase(self):
        """quick_adjust returns uppercase bias string."""
        result = quick_adjust("NEUTRAL", 0.5)
        assert result == "BULLISH"

    def test_quick_adjust_conflict(self):
        """quick_adjust handles conflict correctly."""
        result = quick_adjust("BULLISH", -0.3)
        assert result == "NEUTRAL"


class TestFormatAdjustment:
    """Tests for format_adjustment display function."""

    def test_format_shows_change(self):
        """Format shows (CHANGED) when direction changed."""
        adj = adjust_direction("NEUTRAL", 0.5)
        formatted = format_adjustment(adj)
        assert "(CHANGED)" in formatted
        assert "→" in formatted

    def test_format_shows_equals(self):
        """Format shows = when direction unchanged."""
        adj = adjust_direction("BULLISH", 0.5)
        formatted = format_adjustment(adj)
        assert "(CHANGED)" not in formatted
        assert "=" in formatted


class TestRealWorldScenarios:
    """Tests based on real production scenarios."""

    def test_orcl_scenario(self):
        """ORCL: NEUTRAL skew + Bullish (+0.4) → BULLISH."""
        adj = adjust_direction("NEUTRAL", 0.4)
        assert adj.adjusted_bias == AdjustedBias.BULLISH
        assert adj.rule_applied == "tiebreak_bullish"

    def test_avgo_scenario(self):
        """AVGO: NEUTRAL skew + Strong Bullish (+0.6) → BULLISH."""
        adj = adjust_direction("DirectionalBias.NEUTRAL", 0.6)
        assert adj.adjusted_bias == AdjustedBias.BULLISH
        assert adj.confidence >= 1.0

    def test_pl_scenario(self):
        """PL: STRONG_BULLISH skew + Bullish (+0.3) → STRONG_BULLISH (aligned)."""
        adj = adjust_direction("STRONG_BULLISH", 0.3)
        assert adj.adjusted_bias == AdjustedBias.STRONG_BULLISH
        assert adj.rule_applied == "skew_dominates"

    def test_lulu_scenario(self):
        """LULU: BULLISH skew + Bearish (-0.21) → NEUTRAL (conflict)."""
        adj = adjust_direction("BULLISH", -0.21)
        assert adj.adjusted_bias == AdjustedBias.NEUTRAL
        assert adj.rule_applied == "conflict_hedge"

    def test_cost_scenario(self):
        """COST: BULLISH skew + Bullish (+0.3) → BULLISH (aligned)."""
        adj = adjust_direction("BULLISH", 0.3)
        assert adj.adjusted_bias == AdjustedBias.BULLISH
        assert adj.rule_applied == "skew_dominates"


class TestAdjustedBiasEnum:
    """Tests for AdjustedBias enum methods."""

    def test_is_bullish(self):
        """is_bullish returns True for bullish biases."""
        assert AdjustedBias.BULLISH.is_bullish() is True
        assert AdjustedBias.STRONG_BULLISH.is_bullish() is True
        assert AdjustedBias.NEUTRAL.is_bullish() is False
        assert AdjustedBias.BEARISH.is_bullish() is False

    def test_is_bearish(self):
        """is_bearish returns True for bearish biases."""
        assert AdjustedBias.BEARISH.is_bearish() is True
        assert AdjustedBias.STRONG_BEARISH.is_bearish() is True
        assert AdjustedBias.NEUTRAL.is_bearish() is False
        assert AdjustedBias.BULLISH.is_bearish() is False
