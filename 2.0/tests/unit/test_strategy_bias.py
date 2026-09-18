"""
Tests for strategy/bias.py — determine_bias.

Which spreads are offered no longer depends on the bias: see
test_strategy_generator.TestOfferingIgnoresBias.
"""
from datetime import date
from unittest.mock import MagicMock

from src.application.services.strategy.bias import determine_bias
from src.domain.enums import DirectionalBias
from src.domain.types import SkewResult


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
