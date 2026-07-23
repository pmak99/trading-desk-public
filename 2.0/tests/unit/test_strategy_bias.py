"""
Tests for strategy/bias.py — determine_bias and select_strategy_types.
"""
import pytest
from unittest.mock import MagicMock
from src.application.services.strategy.bias import determine_bias, select_strategy_types
from src.domain.enums import DirectionalBias, StrategyType
from src.domain.types import VRPResult, SkewResult
from src.domain.enums import Recommendation
from datetime import date
from src.domain.types import Percentage


def make_vrp(ratio: float) -> VRPResult:
    return VRPResult(
        ticker="TEST",
        expiration=date(2026, 7, 18),
        implied_move_pct=Percentage(5.0),
        historical_mean_move_pct=Percentage(5.0 / ratio),
        vrp_ratio=ratio,
        edge_score=0.5,
        recommendation=Recommendation.EXCELLENT if ratio >= 1.8 else Recommendation.GOOD,
    )


class TestDetermineBias:
    def test_none_skew_returns_neutral(self):
        assert determine_bias(None) == DirectionalBias.NEUTRAL

    def test_old_skewresult_bearish(self):
        skew = SkewResult(ticker="T", expiration=date.today(), skew_atm=0.1,
                          skew_strength="strong", direction="bearish")
        assert determine_bias(skew) == DirectionalBias.BEARISH

    def test_old_skewresult_bullish(self):
        skew = SkewResult(ticker="T", expiration=date.today(), skew_atm=-0.1,
                          skew_strength="moderate", direction="bullish")
        assert determine_bias(skew) == DirectionalBias.BULLISH

    def test_old_skewresult_neutral(self):
        skew = SkewResult(ticker="T", expiration=date.today(), skew_atm=0.0,
                          skew_strength="weak", direction="neutral")
        assert determine_bias(skew) == DirectionalBias.NEUTRAL

    def test_new_format_directional_bias_enum(self):
        skew = MagicMock()
        skew.directional_bias = DirectionalBias.STRONG_BULLISH
        assert determine_bias(skew) == DirectionalBias.STRONG_BULLISH

    def test_new_format_legacy_string(self):
        skew = MagicMock()
        skew.directional_bias = "strong_bearish"
        assert determine_bias(skew) == DirectionalBias.STRONG_BEARISH

    def test_new_format_put_bias_maps_to_bearish(self):
        skew = MagicMock()
        skew.directional_bias = "put_bias"
        assert determine_bias(skew) == DirectionalBias.BEARISH

    def test_new_format_unknown_string_returns_neutral(self):
        skew = MagicMock()
        skew.directional_bias = "something_unknown"
        assert determine_bias(skew) == DirectionalBias.NEUTRAL


class TestSelectStrategyTypes:
    def test_excellent_vrp_neutral_bias_returns_both(self):
        vrp = make_vrp(2.0)
        result = select_strategy_types(vrp, DirectionalBias.NEUTRAL)
        assert StrategyType.BULL_PUT_SPREAD in result
        assert StrategyType.BEAR_CALL_SPREAD in result

    def test_excellent_vrp_strong_bullish_returns_only_put_spread(self):
        vrp = make_vrp(2.1)
        result = select_strategy_types(vrp, DirectionalBias.STRONG_BULLISH)
        assert result == [StrategyType.BULL_PUT_SPREAD]

    def test_excellent_vrp_strong_bearish_returns_only_call_spread(self):
        vrp = make_vrp(2.0)
        result = select_strategy_types(vrp, DirectionalBias.STRONG_BEARISH)
        assert result == [StrategyType.BEAR_CALL_SPREAD]

    def test_good_vrp_bullish_bias_returns_only_put_spread(self):
        vrp = make_vrp(1.6)
        result = select_strategy_types(vrp, DirectionalBias.BULLISH)
        assert result == [StrategyType.BULL_PUT_SPREAD]

    def test_good_vrp_bearish_bias_returns_only_call_spread(self):
        vrp = make_vrp(1.5)
        result = select_strategy_types(vrp, DirectionalBias.BEARISH)
        assert result == [StrategyType.BEAR_CALL_SPREAD]

    def test_marginal_vrp_neutral_returns_put_spread(self):
        vrp = make_vrp(1.2)
        result = select_strategy_types(vrp, DirectionalBias.NEUTRAL)
        assert result == [StrategyType.BULL_PUT_SPREAD]

    def test_marginal_vrp_bearish_returns_call_spread(self):
        vrp = make_vrp(1.3)
        result = select_strategy_types(vrp, DirectionalBias.BEARISH)
        assert result == [StrategyType.BEAR_CALL_SPREAD]

    def test_high_trr_truncates_to_one(self):
        vrp = make_vrp(2.0)
        result = select_strategy_types(vrp, DirectionalBias.NEUTRAL, tail_risk_level="HIGH")
        assert len(result) == 1

    def test_normal_trr_does_not_truncate(self):
        vrp = make_vrp(2.0)
        result = select_strategy_types(vrp, DirectionalBias.NEUTRAL, tail_risk_level="NORMAL")
        assert len(result) == 2

    def test_iron_condor_never_in_output(self):
        for ratio in [1.2, 1.5, 2.0, 2.5]:
            vrp = make_vrp(ratio)
            for bias in DirectionalBias:
                result = select_strategy_types(vrp, bias)
                assert StrategyType.IRON_CONDOR not in result
                assert StrategyType.IRON_BUTTERFLY not in result
