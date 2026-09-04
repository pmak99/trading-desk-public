"""Tests for liquidity.scoring and liquidity.score module functions."""
import pytest
from src.application.metrics.liquidity.score import LiquidityScore, LiquidityThresholds


class TestLiquidityThresholds:
    def test_default_oi_thresholds(self):
        t = LiquidityThresholds()
        assert t.min_oi == 10
        assert t.warning_oi == 50
        assert t.good_oi == 100
        assert t.excellent_oi == 200

    def test_default_volume_thresholds(self):
        t = LiquidityThresholds()
        assert t.min_volume == 0
        assert t.good_volume == 100
        assert t.excellent_volume == 250

    def test_default_spread_thresholds(self):
        t = LiquidityThresholds()
        assert t.max_spread_pct == 25.0
        assert t.warning_spread_pct == 18.0
        assert t.good_spread_pct == 12.0
        assert t.excellent_spread_pct == 12.0

    def test_default_weights(self):
        t = LiquidityThresholds()
        assert t.oi_weight == 0.40
        assert t.volume_weight == 0.30
        assert t.spread_weight == 0.25
        assert t.depth_weight == 0.05

    def test_custom_values(self):
        t = LiquidityThresholds(min_oi=50, excellent_oi=1000)
        assert t.min_oi == 50
        assert t.excellent_oi == 1000
        assert t.warning_oi == 50  # default unchanged

    def test_is_frozen(self):
        t = LiquidityThresholds()
        with pytest.raises((TypeError, AttributeError)):
            t.min_oi = 999


class TestLiquidityScore:
    def test_score_fields_exist(self):
        from src.domain.types import Money
        s = LiquidityScore(
            overall_score=85.0, oi_score=90.0, volume_score=80.0,
            spread_score=88.0, depth_score=None,
            open_interest=500, volume=200, bid_ask_spread_pct=5.0,
            effective_spread=Money(0.10), is_liquid=True, liquidity_tier="GOOD",
        )
        assert s.overall_score == 85.0
        assert s.liquidity_tier == "GOOD"

    def test_is_frozen(self):
        from src.domain.types import Money
        s = LiquidityScore(
            overall_score=85.0, oi_score=90.0, volume_score=80.0,
            spread_score=88.0, depth_score=None,
            open_interest=500, volume=200, bid_ask_spread_pct=5.0,
            effective_spread=Money(0.10), is_liquid=True, liquidity_tier="GOOD",
        )
        with pytest.raises((TypeError, AttributeError)):
            s.overall_score = 0.0


# ============================================================================
# Scoring module tests (added in Task 2)
# ============================================================================

from decimal import Decimal
from src.domain.types import Money, Percentage, Strike, OptionQuote, OptionChain


def make_thresholds() -> LiquidityThresholds:
    """Standard thresholds used across scoring tests."""
    return LiquidityThresholds(
        min_oi=50, warning_oi=100, good_oi=500, excellent_oi=1000,
        min_volume=20, good_volume=100, excellent_volume=250,
        max_spread_pct=15.0, warning_spread_pct=12.0,
        good_spread_pct=8.0, excellent_spread_pct=5.0,
    )


def make_option(bid=2.00, ask=2.10, oi=500, volume=100) -> OptionQuote:
    return OptionQuote(
        bid=Money(bid), ask=Money(ask),
        implied_volatility=Percentage(30.0),
        open_interest=oi, volume=volume,
    )


class TestCalculateSpreadPct:
    def test_normal_spread(self):
        from src.application.metrics.liquidity.scoring import calculate_spread_pct
        option = make_option(bid=2.00, ask=2.10)
        result = calculate_spread_pct(option)
        assert 4.8 < result < 5.0

    def test_no_bid_ask_returns_100(self):
        from src.application.metrics.liquidity.scoring import calculate_spread_pct
        option = OptionQuote(bid=None, ask=None, implied_volatility=Percentage(30.0), open_interest=100, volume=50)
        assert calculate_spread_pct(option) == 100.0

    def test_zero_mid_returns_100(self):
        from src.application.metrics.liquidity.scoring import calculate_spread_pct
        option = make_option(bid=0.0, ask=0.0)
        assert calculate_spread_pct(option) == 100.0


class TestScoreHelpers:
    def test_score_oi_excellent(self):
        from src.application.metrics.liquidity.scoring import score_open_interest
        t = make_thresholds()
        assert score_open_interest(1000, t) == 100.0

    def test_score_oi_at_good_boundary(self):
        from src.application.metrics.liquidity.scoring import score_open_interest
        t = make_thresholds()
        assert score_open_interest(500, t) == 80.0

    def test_score_oi_zero(self):
        from src.application.metrics.liquidity.scoring import score_open_interest
        t = make_thresholds()
        assert score_open_interest(0, t) == 0.0

    def test_score_volume_excellent(self):
        from src.application.metrics.liquidity.scoring import score_volume
        t = make_thresholds()
        assert score_volume(250, t) == 100.0

    def test_score_volume_zero(self):
        from src.application.metrics.liquidity.scoring import score_volume
        t = make_thresholds()
        assert score_volume(0, t) == 0.0

    def test_score_spread_excellent(self):
        from src.application.metrics.liquidity.scoring import score_spread
        t = make_thresholds()
        assert score_spread(3.0, t) == 100.0

    def test_score_spread_above_max(self):
        from src.application.metrics.liquidity.scoring import score_spread
        t = make_thresholds()
        assert score_spread(35.0, t) < 25.0

    def test_score_depth_none_values(self):
        from src.application.metrics.liquidity.scoring import score_depth
        assert score_depth(None, None) == 50.0

    def test_score_depth_excellent(self):
        from src.application.metrics.liquidity.scoring import score_depth
        assert score_depth(50, 60) == 100.0

    def test_redistribute_weights(self):
        from src.application.metrics.liquidity.scoring import redistribute_weights_without_depth
        t = LiquidityThresholds()
        weights = redistribute_weights_without_depth(t)
        assert abs(weights['oi'] + weights['volume'] + weights['spread'] - 1.0) < 1e-9


class TestClassifyTier:
    def test_excellent_all(self):
        from src.application.metrics.liquidity.scoring import classify_tier
        t = make_thresholds()
        assert classify_tier(1000, 250, 3.0, t) == "EXCELLENT"

    def test_reject_low_oi(self):
        from src.application.metrics.liquidity.scoring import classify_tier
        t = make_thresholds()
        assert classify_tier(30, 100, 5.0, t) == "REJECT"

    def test_reject_low_volume(self):
        from src.application.metrics.liquidity.scoring import classify_tier
        t = make_thresholds()
        assert classify_tier(1000, 10, 5.0, t) == "REJECT"

    def test_reject_wide_spread(self):
        from src.application.metrics.liquidity.scoring import classify_tier
        t = make_thresholds()
        assert classify_tier(1000, 250, 20.0, t) == "REJECT"

    def test_warning_oi(self):
        from src.application.metrics.liquidity.scoring import classify_tier
        t = make_thresholds()
        assert classify_tier(200, 50, 5.0, t) == "WARNING"

    def test_good_tier(self):
        from src.application.metrics.liquidity.scoring import classify_tier
        t = make_thresholds()
        assert classify_tier(700, 150, 6.0, t) == "GOOD"

    def test_tier_oi_only_ignores_volume(self):
        from src.application.metrics.liquidity.scoring import classify_tier_oi_only
        t = make_thresholds()
        assert classify_tier_oi_only(1000, 3.0, t) == "EXCELLENT"


class TestScoreOption:
    def test_excellent_option(self):
        from src.application.metrics.liquidity.scoring import score_option
        t = make_thresholds()
        option = make_option(bid=3.00, ask=3.05, oi=1500, volume=300)
        result = score_option(option, t)
        assert result.overall_score >= 90.0
        assert result.liquidity_tier == "EXCELLENT"
        assert result.is_liquid is True

    def test_reject_option(self):
        from src.application.metrics.liquidity.scoring import score_option
        t = make_thresholds()
        option = make_option(bid=1.00, ask=1.30, oi=30, volume=10)
        result = score_option(option, t)
        assert result.liquidity_tier == "REJECT"
        assert result.is_liquid is False

    def test_returns_liquidity_score_type(self):
        from src.application.metrics.liquidity.scoring import score_option
        from src.application.metrics.liquidity.score import LiquidityScore
        t = make_thresholds()
        result = score_option(make_option(), t)
        assert isinstance(result, LiquidityScore)


class TestStraddleTier:
    def test_both_excellent(self):
        from src.application.metrics.liquidity.scoring import classify_straddle_tier
        t = make_thresholds()
        call = make_option(bid=3.00, ask=3.05, oi=1500, volume=300)
        put = make_option(bid=3.00, ask=3.05, oi=1500, volume=300)
        assert classify_straddle_tier(call, put, t) == "EXCELLENT"

    def test_worse_of_two(self):
        from src.application.metrics.liquidity.scoring import classify_straddle_tier
        t = make_thresholds()
        call = make_option(bid=3.00, ask=3.05, oi=1500, volume=300)  # EXCELLENT
        put = make_option(bid=3.00, ask=3.05, oi=30, volume=10)      # REJECT
        assert classify_straddle_tier(call, put, t) == "REJECT"

    def test_market_aware_returns_tuple(self):
        from src.application.metrics.liquidity.scoring import classify_straddle_tier_market_aware
        t = make_thresholds()
        call = make_option(bid=3.00, ask=3.05, oi=1500, volume=300)
        put = make_option(bid=3.00, ask=3.05, oi=1500, volume=300)
        tier, is_open, reason = classify_straddle_tier_market_aware(call, put, t)
        assert tier in ("EXCELLENT", "GOOD", "WARNING", "REJECT")
        assert isinstance(is_open, bool)
        assert isinstance(reason, str)


class TestScoreStrategyLegs:
    def test_empty_legs(self):
        from src.application.metrics.liquidity.scoring import score_strategy_legs
        t = make_thresholds()
        result = score_strategy_legs([], t)
        assert result['min_score'] == 0
        assert result['all_liquid'] is False

    def test_two_legs(self):
        from src.application.metrics.liquidity.scoring import score_strategy_legs
        t = make_thresholds()
        legs = [make_option(bid=3.00, ask=3.05, oi=1500, volume=300),
                make_option(bid=3.00, ask=3.05, oi=1500, volume=300)]
        result = score_strategy_legs(legs, t)
        assert result['min_score'] > 0
        assert result['all_liquid'] is True
        assert len(result['scores']) == 2
