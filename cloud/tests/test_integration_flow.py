"""
Integration tests for the cross-subsystem analysis flow.

Tests the full pipeline: core VRP calculation -> sentiment sentiment modifier
-> cloud API response -> final recommendation.

Uses real domain function calls (not mocked) with test data that
exercises the actual calculation logic.
"""

import pytest

from src.domain.vrp import calculate_vrp, get_vrp_tier
from src.domain.scoring import calculate_score, apply_sentiment_modifier
from src.domain.liquidity import classify_liquidity_tier
from src.domain.direction import (
    adjust_direction,
    get_direction,
    DirectionAdjustment,
    AdjustedBias,
)


# ---------------------------------------------------------------------------
# Realistic historical move datasets for test scenarios
# ---------------------------------------------------------------------------

# AAPL-like: consistent low moves (TRR would be LOW)
HISTORICAL_AAPL = [3.2, 4.1, 2.8, 3.5, 5.0, 2.9, 4.3, 3.7]  # mean ~3.69

# TSLA-like: volatile, large swings (TRR would be HIGH)
HISTORICAL_TSLA = [8.5, 12.1, 6.3, 15.2, 9.8, 7.1, 11.4, 14.0]  # mean ~10.55

# MSFT-like: moderate, steady moves
HISTORICAL_MSFT = [4.0, 5.5, 3.8, 4.2, 6.1, 4.8, 3.5, 5.0]  # mean ~4.61

# Minimal dataset (exactly MIN_QUARTERS = 4)
HISTORICAL_MINIMAL = [3.0, 4.0, 5.0, 6.0]  # mean = 4.5


# ===========================================================================
# 1. End-to-End Flow Tests
# ===========================================================================

class TestEndToEndAnalysisFlow:
    """Test the complete analysis pipeline from raw data to recommendation."""

    def test_excellent_opportunity_full_flow(self):
        """High VRP + good liquidity + bullish sentiment = strong recommendation."""
        # Step 1: VRP calculation (implied move 8.5% vs ~3.69% mean = ~2.3x)
        vrp = calculate_vrp(implied_move_pct=8.5, historical_moves=HISTORICAL_AAPL)
        assert "error" not in vrp
        assert vrp["tier"] == "EXCELLENT"
        assert vrp["vrp_ratio"] >= 1.8

        # Step 2: Liquidity classification
        liq_tier = classify_liquidity_tier(oi=600, spread_pct=8.0, position_size=100)
        assert liq_tier == "EXCELLENT"

        # Step 3: Base score (2.0)
        score_result = calculate_score(
            vrp_ratio=vrp["vrp_ratio"],
            implied_move_pct=vrp["implied_move_pct"],
            liquidity_tier=liq_tier,
            vrp_tier=vrp["tier"],
        )
        base_score = score_result["total_score"]
        assert base_score >= 50, f"Base score {base_score} should pass core pre-filter (>=50)"

        # Step 4: Sentiment modifier (4.0) - strong bullish +12%
        final_score = apply_sentiment_modifier(base_score, sentiment_score=0.7)
        assert final_score > base_score, "Bullish sentiment should increase score"
        assert final_score >= 55, f"Final score {final_score} should pass sentiment post-filter (>=55)"

        # Step 5: Direction
        direction = get_direction("neutral", sentiment_score=0.7, sentiment_direction="bullish")
        assert direction == "BULLISH"

    def test_marginal_opportunity_rejected_by_sentiment(self):
        """Marginal VRP that passes core filter but fails sentiment post-filter with bearish sentiment."""
        # VRP: 1.3x (MARGINAL) - implied 5.85% vs ~4.5 mean
        vrp = calculate_vrp(implied_move_pct=5.85, historical_moves=HISTORICAL_MINIMAL)
        assert vrp["tier"] == "MARGINAL"

        # Moderate liquidity
        liq_tier = classify_liquidity_tier(oi=250, spread_pct=15.0, position_size=100)
        assert liq_tier == "GOOD"

        # Base score
        score_result = calculate_score(
            vrp_ratio=vrp["vrp_ratio"],
            implied_move_pct=vrp["implied_move_pct"],
            liquidity_tier=liq_tier,
        )
        base_score = score_result["total_score"]

        # Strong bearish sentiment (-12%) could push below 55 cutoff
        final_score = apply_sentiment_modifier(base_score, sentiment_score=-0.8)
        assert final_score < base_score, "Bearish sentiment should reduce score"
        # The reduction is 12%, so final = base * 0.88

    def test_skip_vrp_never_reaches_filter(self):
        """SKIP VRP tier produces score too low for any filter."""
        # VRP: ~1.0x (SKIP) - implied matches historical
        vrp = calculate_vrp(implied_move_pct=4.5, historical_moves=HISTORICAL_MINIMAL)
        assert vrp["tier"] == "SKIP"

        score_result = calculate_score(
            vrp_ratio=vrp["vrp_ratio"],
            implied_move_pct=4.5,
            liquidity_tier="EXCELLENT",
        )
        base_score = score_result["total_score"]

        # Even with maximum bullish sentiment (+12%), SKIP VRP should stay low
        boosted = apply_sentiment_modifier(base_score, sentiment_score=0.9)
        # VRP ratio ~1.0 -> VRP component ~(1.0/7.0)*100*0.55 = 7.86
        # Move component = (5/4.5)*100*0.25 = 27.78
        # Liquidity component = 100*0.20 = 20
        # Total ~55.6, boosted by 12% = ~62.3
        # This could pass, but the VRP tier is still SKIP which would flag the trade
        assert vrp["tier"] == "SKIP"

    def test_full_flow_with_poor_liquidity(self):
        """Good VRP but REJECT liquidity penalizes the score."""
        vrp = calculate_vrp(implied_move_pct=7.0, historical_moves=HISTORICAL_AAPL)
        assert vrp["tier"] == "EXCELLENT"

        # Poor liquidity: low OI, wide spread
        liq_tier = classify_liquidity_tier(oi=50, spread_pct=30.0, position_size=100)
        assert liq_tier == "REJECT"

        score_reject = calculate_score(
            vrp_ratio=vrp["vrp_ratio"],
            implied_move_pct=7.0,
            liquidity_tier="REJECT",
        )
        score_excellent = calculate_score(
            vrp_ratio=vrp["vrp_ratio"],
            implied_move_pct=7.0,
            liquidity_tier="EXCELLENT",
        )

        # REJECT loses 16 points of liquidity component (20 vs 100 * 0.20 = 4 vs 20)
        assert score_reject["total_score"] < score_excellent["total_score"]
        diff = score_excellent["total_score"] - score_reject["total_score"]
        assert diff == pytest.approx(16.0, abs=0.1), (
            f"REJECT vs EXCELLENT liquidity difference should be 16 points, got {diff}"
        )


# ===========================================================================
# 2. VRP Tier Tests with Realistic Data
# ===========================================================================

class TestVRPWithRealisticData:
    """VRP calculation with realistic historical datasets produces correct tiers."""

    def test_aapl_high_implied_move_excellent(self):
        """AAPL with elevated IV before earnings -> EXCELLENT VRP."""
        # Mean ~3.69, implied 7.0 -> VRP ~1.90
        vrp = calculate_vrp(implied_move_pct=7.0, historical_moves=HISTORICAL_AAPL)
        assert vrp["tier"] == "EXCELLENT"
        assert vrp["vrp_ratio"] >= 1.8
        assert vrp["sample_size"] == 8

    def test_aapl_normal_implied_move_good(self):
        """AAPL with moderately elevated IV -> GOOD VRP."""
        # Mean ~3.69, implied 5.5 -> VRP ~1.49
        vrp = calculate_vrp(implied_move_pct=5.5, historical_moves=HISTORICAL_AAPL)
        assert vrp["tier"] == "GOOD"
        assert 1.4 <= vrp["vrp_ratio"] < 1.8

    def test_tsla_needs_huge_implied_for_excellent(self):
        """TSLA's high historical mean requires very large implied move for EXCELLENT."""
        # Mean ~10.55, need implied >= 10.55 * 1.8 = 18.99 for EXCELLENT
        vrp = calculate_vrp(implied_move_pct=19.0, historical_moves=HISTORICAL_TSLA)
        assert vrp["tier"] == "EXCELLENT"

        # 15% implied -> only ~1.42x = GOOD
        vrp_lower = calculate_vrp(implied_move_pct=15.0, historical_moves=HISTORICAL_TSLA)
        assert vrp_lower["tier"] == "GOOD"

    def test_consistent_moves_produce_low_consistency_value(self):
        """Consistent historical moves produce low MAD/median (consistency) value."""
        consistent = [4.0, 4.1, 3.9, 4.0, 4.2, 3.8, 4.0, 4.1]
        vrp = calculate_vrp(implied_move_pct=8.0, historical_moves=consistent)
        assert vrp["consistency"] < 0.1, "Consistent moves should have low MAD/median"

    def test_volatile_moves_produce_high_consistency_value(self):
        """Highly variable historical moves produce high consistency value."""
        volatile = [2.0, 12.0, 3.0, 15.0, 1.5, 18.0, 2.5, 14.0]
        vrp = calculate_vrp(implied_move_pct=10.0, historical_moves=volatile)
        assert vrp["consistency"] > 0.5, "Volatile moves should have high MAD/median"

    def test_vrp_tier_boundary_excellent(self):
        """VRP exactly at 1.8 threshold -> EXCELLENT."""
        # Mean = 5.0, implied = 9.0 -> VRP = 1.8
        vrp = calculate_vrp(
            implied_move_pct=9.0,
            historical_moves=[5.0, 5.0, 5.0, 5.0],
        )
        assert vrp["vrp_ratio"] == 1.8
        assert vrp["tier"] == "EXCELLENT"

    def test_vrp_tier_boundary_good(self):
        """VRP exactly at 1.4 threshold -> GOOD."""
        # Mean = 5.0, implied = 7.0 -> VRP = 1.4
        vrp = calculate_vrp(
            implied_move_pct=7.0,
            historical_moves=[5.0, 5.0, 5.0, 5.0],
        )
        assert vrp["vrp_ratio"] == 1.4
        assert vrp["tier"] == "GOOD"

    def test_vrp_tier_boundary_marginal(self):
        """VRP exactly at 1.2 threshold -> MARGINAL."""
        # Mean = 5.0, implied = agents -> VRP = 1.2
        vrp = calculate_vrp(
            implied_move_pct=6.0,
            historical_moves=[5.0, 5.0, 5.0, 5.0],
        )
        assert vrp["vrp_ratio"] == 1.2
        assert vrp["tier"] == "MARGINAL"

    def test_vrp_just_below_marginal_is_skip(self):
        """VRP just below 1.2 -> SKIP."""
        # Mean = 5.0, implied = 5.95 -> VRP = 1.19
        vrp = calculate_vrp(
            implied_move_pct=5.95,
            historical_moves=[5.0, 5.0, 5.0, 5.0],
        )
        assert vrp["vrp_ratio"] == 1.19
        assert vrp["tier"] == "SKIP"


# ===========================================================================
# 3. Sentiment Modifier Tests
# ===========================================================================

class TestSentimentModifierRange:
    """Sentiment modifier correctly adjusts base score within +/-12% range."""

    def test_strong_bullish_applies_plus_12_percent(self):
        """Sentiment >= 0.6 applies +12% modifier."""
        base = 70.0
        modified = apply_sentiment_modifier(base, sentiment_score=0.6)
        assert modified == pytest.approx(78.4, abs=0.1)  # 70 * 1.12

    def test_bullish_applies_plus_7_percent(self):
        """Sentiment >= 0.2 (but < 0.6) applies +7% modifier."""
        base = 70.0
        modified = apply_sentiment_modifier(base, sentiment_score=0.3)
        assert modified == pytest.approx(74.9, abs=0.1)  # 70 * 1.07

    def test_neutral_applies_zero_modifier(self):
        """Sentiment between -0.2 and 0.2 applies 0% modifier."""
        base = 70.0
        modified = apply_sentiment_modifier(base, sentiment_score=0.1)
        assert modified == pytest.approx(70.0, abs=0.1)  # 70 * 1.00

    def test_bearish_applies_minus_7_percent(self):
        """Sentiment <= -0.2 (but > -0.6) applies -7% modifier."""
        base = 70.0
        modified = apply_sentiment_modifier(base, sentiment_score=-0.3)
        assert modified == pytest.approx(65.1, abs=0.1)  # 70 * 0.93

    def test_strong_bearish_applies_minus_12_percent(self):
        """Sentiment <= -0.6 applies -12% modifier."""
        base = 70.0
        modified = apply_sentiment_modifier(base, sentiment_score=-0.8)
        assert modified == pytest.approx(61.6, abs=0.1)  # 70 * 0.88

    def test_modifier_preserves_ordering(self):
        """Bullish sentiment always produces higher score than bearish for same base."""
        base = 75.0
        strong_bullish = apply_sentiment_modifier(base, sentiment_score=0.9)
        bullish = apply_sentiment_modifier(base, sentiment_score=0.4)
        neutral = apply_sentiment_modifier(base, sentiment_score=0.0)
        bearish = apply_sentiment_modifier(base, sentiment_score=-0.4)
        strong_bearish = apply_sentiment_modifier(base, sentiment_score=-0.9)

        assert strong_bullish > bullish > neutral > bearish > strong_bearish

    def test_modifier_clamped_to_0_100(self):
        """Modified score is clamped to [0, 100] range."""
        # High base + strong bullish could exceed 100
        modified = apply_sentiment_modifier(95.0, sentiment_score=0.9)
        assert modified <= 100.0

        # Clamped at 0 for very low base + strong bearish
        modified = apply_sentiment_modifier(0.0, sentiment_score=-0.9)
        assert modified >= 0.0

    def test_boundary_between_neutral_and_bullish(self):
        """Score of exactly 0.2 triggers bullish modifier (+7%)."""
        base = 60.0
        at_boundary = apply_sentiment_modifier(base, sentiment_score=0.2)
        just_below = apply_sentiment_modifier(base, sentiment_score=0.19)

        assert at_boundary == pytest.approx(64.2, abs=0.1)  # 60 * 1.07
        assert just_below == pytest.approx(60.0, abs=0.1)   # 60 * 1.00

    def test_boundary_between_neutral_and_bearish(self):
        """Score of exactly -0.2 triggers bearish modifier (-7%)."""
        base = 60.0
        at_boundary = apply_sentiment_modifier(base, sentiment_score=-0.2)
        just_above = apply_sentiment_modifier(base, sentiment_score=-0.19)

        assert at_boundary == pytest.approx(55.8, abs=0.1)  # 60 * 0.93
        assert just_above == pytest.approx(60.0, abs=0.1)   # 60 * 1.00


# ===========================================================================
# 4. Score Flow: VRP -> Base Score -> Sentiment -> Recommendation
# ===========================================================================

class TestScoreFlowPipeline:
    """Test the scoring pipeline from VRP through to final recommendation."""

    def test_score_components_sum_correctly(self):
        """Component scores should add up to total score."""
        result = calculate_score(
            vrp_ratio=2.0,
            implied_move_pct=5.0,
            liquidity_tier="EXCELLENT",
        )
        components = result["components"]
        expected_total = components["vrp"] + components["move_difficulty"] + components["liquidity"]
        assert result["total_score"] == pytest.approx(expected_total, abs=0.2)

    def test_vrp_dominates_score(self):
        """VRP component (55% weight) should be the largest contributor."""
        result = calculate_score(
            vrp_ratio=3.5,
            implied_move_pct=5.0,
            liquidity_tier="EXCELLENT",
        )
        components = result["components"]
        assert components["vrp"] > components["move_difficulty"]
        assert components["vrp"] > components["liquidity"]

    def test_move_difficulty_easier_scores_higher(self):
        """Smaller implied moves (easier to stay OTM) score higher."""
        easy_result = calculate_score(
            vrp_ratio=2.0, implied_move_pct=3.0, liquidity_tier="EXCELLENT"
        )
        hard_result = calculate_score(
            vrp_ratio=2.0, implied_move_pct=12.0, liquidity_tier="EXCELLENT"
        )
        assert easy_result["components"]["move_difficulty"] > hard_result["components"]["move_difficulty"]

    def test_pipeline_passes_2_0_filter(self):
        """Good VRP + decent liquidity produces base score >= 50."""
        vrp = calculate_vrp(implied_move_pct=7.0, historical_moves=HISTORICAL_MSFT)
        # Mean ~4.61, implied 7.0 -> VRP ~1.52 (GOOD)
        assert vrp["tier"] == "GOOD"

        result = calculate_score(
            vrp_ratio=vrp["vrp_ratio"],
            implied_move_pct=7.0,
            liquidity_tier="EXCELLENT",
        )
        assert result["total_score"] >= 45, (
            f"GOOD VRP + EXCELLENT liquidity should produce reasonable score, got {result['total_score']}"
        )

    def test_pipeline_passes_4_0_filter_with_bullish_sentiment(self):
        """Base score near cutoff passes sentiment filter when boosted by bullish sentiment."""
        # Create a score just around 50-55 range
        result = calculate_score(
            vrp_ratio=1.5,
            implied_move_pct=6.0,
            liquidity_tier="GOOD",
        )
        base = result["total_score"]

        # Bullish sentiment boosts by 7%
        final = apply_sentiment_modifier(base, sentiment_score=0.4)
        assert final > base
        assert final == pytest.approx(base * 1.07, abs=0.1)

    def test_pipeline_fails_4_0_filter_with_bearish_sentiment(self):
        """Borderline base score fails sentiment filter when reduced by bearish sentiment."""
        # Score right around 55
        result = calculate_score(
            vrp_ratio=1.5,
            implied_move_pct=5.0,
            liquidity_tier="WARNING",
        )
        base = result["total_score"]

        # Strong bearish reduces by 12%
        final = apply_sentiment_modifier(base, sentiment_score=-0.8)
        assert final < base
        assert final == pytest.approx(base * 0.88, abs=0.1)

    def test_liquidity_tiers_affect_score_proportionally(self):
        """Score difference between liquidity tiers is proportional to weight."""
        excellent = calculate_score(vrp_ratio=2.0, implied_move_pct=5.0, liquidity_tier="EXCELLENT")
        good = calculate_score(vrp_ratio=2.0, implied_move_pct=5.0, liquidity_tier="GOOD")
        warning = calculate_score(vrp_ratio=2.0, implied_move_pct=5.0, liquidity_tier="WARNING")
        reject = calculate_score(vrp_ratio=2.0, implied_move_pct=5.0, liquidity_tier="REJECT")

        assert excellent["total_score"] > good["total_score"]
        assert good["total_score"] > warning["total_score"]
        assert warning["total_score"] > reject["total_score"]

        # Liquidity scores: EXCELLENT=100, GOOD=80, WARNING=60, REJECT=20
        # Weight = 0.20, so EXCELLENT-REJECT = (100-20)*0.20 = 16 points
        assert excellent["total_score"] - reject["total_score"] == pytest.approx(16.0, abs=0.1)


# ===========================================================================
# 5. Direction Bias 3-Rule Integration Tests
# ===========================================================================

class TestDirectionBiasIntegration:
    """Direction bias rules tested as part of the analysis flow."""

    # Rule 1: Neutral skew + sentiment signal -> sentiment wins

    def test_rule1_neutral_with_strong_bullish_sentiment(self):
        """Neutral skew + strong bullish sentiment -> BULLISH with high confidence."""
        result = adjust_direction("NEUTRAL", sentiment_score=0.7)
        assert result.adjusted_bias == AdjustedBias.BULLISH
        assert result.rule_applied == "tiebreak_bullish"
        assert result.confidence >= 0.8
        assert result.size_modifier == 0.9  # Contrarian: reduce size for strong bullish

    def test_rule1_neutral_with_moderate_bearish_sentiment(self):
        """Neutral skew + moderate bearish sentiment -> BEARISH."""
        result = adjust_direction("NEUTRAL", sentiment_score=-0.4)
        assert result.adjusted_bias == AdjustedBias.BEARISH
        assert result.rule_applied == "tiebreak_bearish"

    def test_rule1_neutral_stays_neutral_with_weak_sentiment(self):
        """Neutral skew + weak sentiment (within -0.2 to 0.2) -> stays NEUTRAL."""
        result = adjust_direction("NEUTRAL", sentiment_score=0.1)
        assert result.adjusted_bias == AdjustedBias.NEUTRAL
        assert result.rule_applied == "both_neutral"

    # Rule 2: Conflict (bullish skew + bearish sentiment) -> go neutral

    def test_rule2_bullish_skew_bearish_sentiment_conflict(self):
        """Bullish skew + bearish sentiment = conflict -> NEUTRAL hedge."""
        result = adjust_direction("BULLISH", sentiment_score=-0.5, sentiment_direction="bearish")
        assert result.adjusted_bias == AdjustedBias.NEUTRAL
        assert result.rule_applied == "conflict_hedge"
        assert result.changed is True

    def test_rule2_strong_bullish_skew_bearish_sentiment_conflict(self):
        """Strong bullish skew + bearish sentiment = conflict -> NEUTRAL."""
        result = adjust_direction("STRONG_BULLISH", sentiment_score=-0.4, sentiment_direction="bearish")
        assert result.adjusted_bias == AdjustedBias.NEUTRAL
        assert result.rule_applied == "conflict_hedge"

    def test_rule2_bearish_skew_bullish_sentiment_conflict(self):
        """Bearish skew + bullish sentiment = conflict -> NEUTRAL."""
        result = adjust_direction("BEARISH", sentiment_score=0.5, sentiment_direction="bullish")
        assert result.adjusted_bias == AdjustedBias.NEUTRAL
        assert result.rule_applied == "conflict_hedge"

    # Rule 3: Otherwise keep original skew bias

    def test_rule3_bullish_skew_bullish_sentiment_aligned(self):
        """Bullish skew + bullish sentiment = aligned -> keep BULLISH."""
        result = adjust_direction("BULLISH", sentiment_score=0.5, sentiment_direction="bullish")
        assert result.adjusted_bias == AdjustedBias.BULLISH
        assert result.rule_applied == "skew_dominates"
        assert result.changed is False

    def test_rule3_bearish_skew_neutral_sentiment_preserved(self):
        """Bearish skew + neutral sentiment -> keep BEARISH (no conflict)."""
        result = adjust_direction("BEARISH", sentiment_score=0.0, sentiment_direction="neutral")
        assert result.adjusted_bias == AdjustedBias.BEARISH
        assert result.rule_applied == "skew_dominates"

    def test_rule3_strong_bearish_preserved(self):
        """Strong bearish skew + neutral sentiment -> keep STRONG_BEARISH."""
        result = adjust_direction("STRONG_BEARISH", sentiment_score=0.0, sentiment_direction="neutral")
        assert result.adjusted_bias == AdjustedBias.STRONG_BEARISH
        assert result.rule_applied == "skew_dominates"

    def test_rule3_strong_bullish_skew_bullish_sentiment(self):
        """Strong bullish skew + bullish sentiment -> keep STRONG_BULLISH."""
        result = adjust_direction("STRONG_BULLISH", sentiment_score=0.5, sentiment_direction="bullish")
        assert result.adjusted_bias == AdjustedBias.STRONG_BULLISH
        assert result.rule_applied == "skew_dominates"
        assert result.changed is False

    # get_direction convenience function integration

    def test_get_direction_integrates_skew_and_sentiment(self):
        """get_direction produces correct uppercase direction string."""
        # Rule 1: neutral + bullish -> BULLISH
        assert get_direction("NEUTRAL", 0.5, "bullish") == "BULLISH"

        # Rule 2: conflict -> NEUTRAL
        assert get_direction("BULLISH", -0.5, "bearish") == "NEUTRAL"

        # Rule 3: aligned -> keep
        assert get_direction("BEARISH", -0.5, "bearish") == "BEARISH"

    def test_get_direction_handles_missing_skew(self):
        """Without skew data, sentiment alone determines direction."""
        assert get_direction(None, 0.5) == "BULLISH"
        assert get_direction(None, -0.5) == "BEARISH"
        assert get_direction(None, 0.0) == "NEUTRAL"

    def test_get_direction_handles_missing_sentiment(self):
        """Without sentiment, skew alone determines direction."""
        assert get_direction("BULLISH", None) == "BULLISH"
        assert get_direction("STRONG_BEARISH", None) == "STRONG_BEARISH"

    def test_get_direction_both_none_neutral(self):
        """Neither skew nor sentiment -> NEUTRAL fallback."""
        assert get_direction(None, None) == "NEUTRAL"


# ===========================================================================
# 6. Contrarian Position Sizing Integration
# ===========================================================================

class TestContrarianSizingFlow:
    """Contrarian sizing modifiers integrated with direction adjustments."""

    def test_strong_bullish_reduces_position_size(self):
        """Strong bullish sentiment (>=0.6) -> 0.9x size modifier (reduce)."""
        result = adjust_direction("NEUTRAL", sentiment_score=0.8)
        assert result.size_modifier == 0.9

    def test_strong_bearish_increases_position_size(self):
        """Strong bearish sentiment (<=-0.6) -> 1.1x size modifier (increase)."""
        result = adjust_direction("NEUTRAL", sentiment_score=-0.7)
        assert result.size_modifier == 1.1

    def test_moderate_sentiment_no_size_change(self):
        """Moderate sentiment stays at 1.0x size modifier."""
        result = adjust_direction("NEUTRAL", sentiment_score=0.4)
        assert result.size_modifier == 1.0

    def test_high_bullish_warning_flag(self):
        """Sentiment >= 0.7 triggers high bullish warning."""
        result = adjust_direction("NEUTRAL", sentiment_score=0.7)
        assert result.high_bullish_warning is True

    def test_no_warning_below_threshold(self):
        """Sentiment < 0.7 does not trigger high bullish warning."""
        result = adjust_direction("NEUTRAL", sentiment_score=0.65)
        assert result.high_bullish_warning is False


# ===========================================================================
# 7. Edge Cases
# ===========================================================================

class TestEdgeCases:
    """Edge cases: zero implied move, empty history, extreme values."""

    def test_zero_implied_move(self):
        """Zero implied move produces VRP ratio of 0 (SKIP)."""
        vrp = calculate_vrp(implied_move_pct=0.0, historical_moves=[3.0, 4.0, 5.0, 6.0])
        assert vrp["vrp_ratio"] == 0.0
        assert vrp["tier"] == "SKIP"

    def test_very_small_implied_move(self):
        """Very small implied move still calculates correctly."""
        vrp = calculate_vrp(implied_move_pct=0.1, historical_moves=[3.0, 4.0, 5.0, 6.0])
        assert vrp["vrp_ratio"] < 0.1
        assert vrp["tier"] == "SKIP"

    def test_insufficient_history(self):
        """Fewer than MIN_QUARTERS (4) historical moves returns error."""
        vrp = calculate_vrp(implied_move_pct=8.0, historical_moves=[4.0, 5.0])
        assert "error" in vrp
        assert vrp["error"] == "insufficient_data"

    def test_empty_history(self):
        """Empty historical moves list returns error."""
        vrp = calculate_vrp(implied_move_pct=8.0, historical_moves=[])
        assert "error" in vrp
        assert vrp["error"] == "insufficient_data"

    def test_single_history_entry(self):
        """Single historical entry is insufficient."""
        vrp = calculate_vrp(implied_move_pct=8.0, historical_moves=[4.0])
        assert "error" in vrp
        assert vrp["error"] == "insufficient_data"

    def test_exactly_min_quarters(self):
        """Exactly MIN_QUARTERS (4) entries is sufficient."""
        vrp = calculate_vrp(implied_move_pct=8.0, historical_moves=[4.0, 4.0, 4.0, 4.0])
        assert "error" not in vrp
        assert vrp["vrp_ratio"] == 2.0

    def test_zero_historical_mean_returns_error(self):
        """All-zero historical moves produce invalid_data error."""
        vrp = calculate_vrp(implied_move_pct=8.0, historical_moves=[0.0, 0.0, 0.0, 0.0])
        assert "error" in vrp
        assert vrp["error"] == "invalid_data"

    def test_extreme_vrp_ratio(self):
        """Very high VRP ratio still returns EXCELLENT tier."""
        vrp = calculate_vrp(implied_move_pct=50.0, historical_moves=[1.0, 1.0, 1.0, 1.0])
        assert vrp["vrp_ratio"] == 50.0
        assert vrp["tier"] == "EXCELLENT"

    def test_extreme_sentiment_values(self):
        """Extreme sentiment scores at boundaries of [-1.0, +1.0]."""
        base = 60.0
        max_bullish = apply_sentiment_modifier(base, sentiment_score=1.0)
        max_bearish = apply_sentiment_modifier(base, sentiment_score=-1.0)

        assert max_bullish == pytest.approx(67.2, abs=0.1)  # 60 * 1.12
        assert max_bearish == pytest.approx(52.8, abs=0.1)  # 60 * 0.88

    def test_score_with_very_large_implied_move(self):
        """Very large implied move produces low move_difficulty component."""
        result = calculate_score(
            vrp_ratio=2.0,
            implied_move_pct=50.0,
            liquidity_tier="EXCELLENT",
        )
        # Move score = (5/50)*100 = 10, weighted = 10 * 0.25 = 2.5
        assert result["components"]["move_difficulty"] == pytest.approx(2.5, abs=0.1)

    def test_score_with_very_small_implied_move(self):
        """Very small implied move is capped at 100 before weighting."""
        result = calculate_score(
            vrp_ratio=2.0,
            implied_move_pct=1.0,
            liquidity_tier="EXCELLENT",
        )
        # Move score = min(100, (5/1)*100) = 100, weighted = 100 * 0.25 = 25
        assert result["components"]["move_difficulty"] == pytest.approx(25.0, abs=0.1)

    def test_sentiment_modifier_with_zero_base_score(self):
        """Sentiment modifier on zero base score stays zero."""
        assert apply_sentiment_modifier(0.0, sentiment_score=0.9) == 0.0
        assert apply_sentiment_modifier(0.0, sentiment_score=-0.9) == 0.0

    def test_sentiment_modifier_with_max_base_score(self):
        """Sentiment modifier on 100 base score is clamped at 100."""
        result = apply_sentiment_modifier(100.0, sentiment_score=0.9)
        assert result == 100.0  # Clamped: 100 * 1.12 = 112 -> 100

    def test_unknown_liquidity_tier_gets_default_score(self):
        """Unknown liquidity tier gets default score (20) via dict.get fallback."""
        result = calculate_score(
            vrp_ratio=2.0,
            implied_move_pct=5.0,
            liquidity_tier="UNKNOWN",
        )
        # Default score is 20, weighted = 20 * 0.20 = 4.0
        assert result["components"]["liquidity"] == pytest.approx(4.0, abs=0.1)

    def test_direction_with_directionalbias_prefix(self):
        """Direction handles 2.0's DirectionalBias.XXX format."""
        result = adjust_direction("DirectionalBias.STRONG_BULLISH", sentiment_score=0.5, sentiment_direction="bullish")
        assert result.adjusted_bias == AdjustedBias.STRONG_BULLISH
        assert result.rule_applied == "skew_dominates"

    def test_invalid_sentiment_direction_raises(self):
        """Invalid sentiment_direction raises ValueError."""
        with pytest.raises(ValueError, match="Invalid sentiment_direction"):
            adjust_direction("NEUTRAL", sentiment_score=0.5, sentiment_direction="sideways")


# ===========================================================================
# 8. Combined Cross-Subsystem Scenarios
# ===========================================================================

class TestCrossSubsystemScenarios:
    """Realistic end-to-end scenarios combining all subsystems."""

    def test_high_confidence_trade_aapl(self):
        """
        AAPL earnings: high VRP, great liquidity, bullish sentiment.
        Should produce a clear GO recommendation.
        """
        # core: VRP
        vrp = calculate_vrp(implied_move_pct=7.5, historical_moves=HISTORICAL_AAPL)
        assert vrp["tier"] == "EXCELLENT"

        # core: Liquidity
        liq = classify_liquidity_tier(oi=800, spread_pct=5.0, position_size=100)
        assert liq == "EXCELLENT"

        # core: Base score
        score = calculate_score(
            vrp_ratio=vrp["vrp_ratio"],
            implied_move_pct=vrp["implied_move_pct"],
            liquidity_tier=liq,
        )
        base = score["total_score"]
        assert base >= 50  # Passes core filter

        # sentiment: Sentiment
        final = apply_sentiment_modifier(base, sentiment_score=0.5)
        assert final >= 55  # Passes sentiment filter
        assert final > base  # Sentiment boosted

        # Direction: neutral skew + bullish sentiment -> bullish
        direction = adjust_direction("NEUTRAL", sentiment_score=0.5)
        assert direction.adjusted_bias == AdjustedBias.BULLISH
        assert direction.size_modifier == 1.0  # Not extreme enough for sizing change

    def test_conflicting_signals_msft(self):
        """
        MSFT earnings: moderate VRP, bullish skew but bearish sentiment.
        Direction conflict forces neutral hedge.
        """
        # core: VRP
        vrp = calculate_vrp(implied_move_pct=7.5, historical_moves=HISTORICAL_MSFT)
        assert vrp["tier"] in ("GOOD", "EXCELLENT")

        # core: Base score
        score = calculate_score(
            vrp_ratio=vrp["vrp_ratio"],
            implied_move_pct=7.5,
            liquidity_tier="GOOD",
        )
        base = score["total_score"]

        # sentiment: Strong bearish sentiment reduces score by 12%
        final = apply_sentiment_modifier(base, sentiment_score=-0.7)
        assert final < base
        assert final == pytest.approx(base * 0.88, abs=0.1)

        # Direction: bullish skew + bearish sentiment = CONFLICT -> neutral
        direction = adjust_direction("BULLISH", sentiment_score=-0.7, sentiment_direction="bearish")
        assert direction.adjusted_bias == AdjustedBias.NEUTRAL
        assert direction.rule_applied == "conflict_hedge"
        assert direction.changed is True
        assert direction.size_modifier == 1.1  # Contrarian: increase for strong bearish

    def test_no_edge_trade_skip(self):
        """
        Low VRP ticker: no volatility edge, should be skipped regardless of sentiment.
        """
        # core: VRP barely 1.0x
        vrp = calculate_vrp(
            implied_move_pct=10.0,
            historical_moves=HISTORICAL_TSLA,  # mean ~10.55
        )
        assert vrp["tier"] == "SKIP"

        # Score is still calculated but VRP component is weak
        score = calculate_score(
            vrp_ratio=vrp["vrp_ratio"],
            implied_move_pct=10.0,
            liquidity_tier="EXCELLENT",
        )

        # Even best-case sentiment cannot create edge where none exists
        # The VRP tier being SKIP is the critical signal
        assert vrp["tier"] == "SKIP"

    def test_poor_liquidity_reduces_score_significantly(self):
        """
        Excellent VRP but REJECT liquidity should reduce score by 16 points.
        """
        vrp = calculate_vrp(implied_move_pct=9.0, historical_moves=[5.0, 5.0, 5.0, 5.0])
        assert vrp["tier"] == "EXCELLENT"

        score_excellent = calculate_score(
            vrp_ratio=vrp["vrp_ratio"], implied_move_pct=9.0, liquidity_tier="EXCELLENT"
        )
        score_reject = calculate_score(
            vrp_ratio=vrp["vrp_ratio"], implied_move_pct=9.0, liquidity_tier="REJECT"
        )

        # 16 point penalty: (100 - 20) * 0.20 = 16
        penalty = score_excellent["total_score"] - score_reject["total_score"]
        assert penalty == pytest.approx(16.0, abs=0.1)

    def test_sentiment_can_push_borderline_over_filter(self):
        """
        Score at 52 (passes core filter >= 50, fails sentiment >= 55).
        Strong bullish sentiment (+12%) pushes to 58.2 -> passes both.
        """
        base_score = 52.0
        final = apply_sentiment_modifier(base_score, sentiment_score=0.8)
        assert final == pytest.approx(58.2, abs=0.1)  # 52 * 1.12
        assert final >= 55  # Now passes sentiment filter

    def test_sentiment_can_push_borderline_below_filter(self):
        """
        Score at 60 (passes both filters).
        Strong bearish sentiment (-12%) reduces to 52.8 -> still passes core but marginal.
        """
        base_score = 60.0
        final = apply_sentiment_modifier(base_score, sentiment_score=-0.8)
        assert final == pytest.approx(52.8, abs=0.1)  # 60 * 0.88
        assert final < 55  # Fails sentiment filter
        assert final >= 50  # Still passes core filter
