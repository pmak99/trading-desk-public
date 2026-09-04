"""Tests for liquidity.hybrid module functions."""
import pytest
from decimal import Decimal
from datetime import date

from src.application.metrics.liquidity.hybrid import (
    calculate_dynamic_thresholds,
    find_strike_outside_move,
    find_delta_strike,
    classify_hybrid_tier,
    classify_hybrid_tier_market_aware,
)
from src.application.metrics.liquidity.score import LiquidityThresholds
from src.domain.types import Money, Percentage, Strike, OptionQuote, OptionChain


def make_option(bid=2.00, ask=2.10, oi=500, volume=100, delta=None) -> OptionQuote:
    return OptionQuote(
        bid=Money(bid), ask=Money(ask),
        implied_volatility=Percentage(30.0),
        open_interest=oi, volume=volume, delta=delta,
    )


def make_chain() -> OptionChain:
    calls = {
        Strike(90):  make_option(bid=11.00, ask=11.20, oi=2000, volume=300, delta=0.90),
        Strike(95):  make_option(bid=6.50,  ask=6.70,  oi=1500, volume=200, delta=0.70),
        Strike(100): make_option(bid=3.00,  ask=3.15,  oi=1000, volume=150, delta=0.50),
        Strike(105): make_option(bid=1.00,  ask=1.10,  oi=800,  volume=100, delta=0.20),
        Strike(110): make_option(bid=0.30,  ask=0.40,  oi=500,  volume=50,  delta=0.10),
    }
    puts = {
        Strike(90):  make_option(bid=0.30, ask=0.40, oi=500,  volume=50,  delta=-0.10),
        Strike(95):  make_option(bid=1.00, ask=1.10, oi=800,  volume=100, delta=-0.20),
        Strike(100): make_option(bid=3.00, ask=3.15, oi=1000, volume=150, delta=-0.50),
        Strike(105): make_option(bid=6.50, ask=6.70, oi=1500, volume=200, delta=-0.70),
        Strike(110): make_option(bid=11.00, ask=11.20, oi=2000, volume=300, delta=-0.90),
    }
    return OptionChain(
        ticker="TEST", expiration=date(2026, 7, 18),
        stock_price=Money(100.0), calls=calls, puts=puts,
    )


class TestCalculateDynamicThresholds:
    def test_low_price(self):
        result = calculate_dynamic_thresholds(15.0)
        assert result['spread_width'] == 2.50
        assert result['price_tier'] == "<$20"

    def test_mid_price_20_to_100(self):
        result = calculate_dynamic_thresholds(50.0)
        assert result['spread_width'] == 5.0
        assert result['price_tier'] == "$20-100"

    def test_price_100_to_200(self):
        result = calculate_dynamic_thresholds(150.0)
        assert result['spread_width'] == 10.0
        assert result['price_tier'] == "$100-200"

    def test_price_200_to_500(self):
        result = calculate_dynamic_thresholds(300.0)
        assert result['spread_width'] == 20.0
        assert result['price_tier'] == "$200-500"

    def test_price_500_to_1000(self):
        result = calculate_dynamic_thresholds(700.0)
        assert result['spread_width'] == 50.0
        assert result['price_tier'] == "$500-1000"

    def test_price_1000_plus(self):
        result = calculate_dynamic_thresholds(1500.0)
        assert result['spread_width'] == 100.0
        assert result['price_tier'] == "$1000+"

    def test_returns_required_keys(self):
        result = calculate_dynamic_thresholds(100.0)
        for key in ('spread_width', 'contracts', 'min_oi', 'warning_oi', 'good_oi', 'price_tier'):
            assert key in result

    def test_threshold_ordering(self):
        result = calculate_dynamic_thresholds(100.0)
        assert result['min_oi'] < result['warning_oi'] < result['good_oi']


class TestFindStrikeOutsideMove:
    def test_find_call_outside_5pct_move(self):
        chain = make_chain()
        result = find_strike_outside_move(chain, 5.0, is_call=True)
        assert result is not None
        strike, option = result
        assert float(strike.price) >= 105.0

    def test_find_put_outside_5pct_move(self):
        chain = make_chain()
        result = find_strike_outside_move(chain, 5.0, is_call=False)
        assert result is not None
        strike, option = result
        assert float(strike.price) <= 95.0

    def test_returns_none_for_empty_chain(self):
        empty = OptionChain(ticker="X", expiration=date(2026, 7, 18),
                            stock_price=Money(100.0), calls={}, puts={})
        result = find_strike_outside_move(empty, 5.0, is_call=True)
        assert result is None


class TestFindDeltaStrike:
    def test_finds_20_delta_call(self):
        chain = make_chain()
        result = find_delta_strike(chain, 0.20, is_call=True)
        assert result is not None
        strike, option = result
        assert abs(float(option.delta) - 0.20) < 0.05

    def test_finds_20_delta_put(self):
        chain = make_chain()
        result = find_delta_strike(chain, 0.20, is_call=False)
        assert result is not None
        strike, option = result
        assert abs(abs(float(option.delta)) - 0.20) < 0.05

    def test_returns_none_for_empty_chain(self):
        empty = OptionChain(ticker="X", expiration=date(2026, 7, 18),
                            stock_price=Money(100.0), calls={}, puts={})
        result = find_delta_strike(empty, 0.20, is_call=True)
        assert result is None


class TestClassifyHybridTier:
    def test_returns_tier_and_details(self):
        chain = make_chain()
        t = LiquidityThresholds(min_oi=50, warning_oi=100, good_oi=500, excellent_oi=1000,
                                min_volume=0, good_volume=100, excellent_volume=250,
                                max_spread_pct=15.0, warning_spread_pct=12.0,
                                good_spread_pct=8.0, excellent_spread_pct=5.0)
        tier, details = classify_hybrid_tier(chain, 5.0, t, use_dynamic_thresholds=False)
        assert tier in ("EXCELLENT", "GOOD", "WARNING", "REJECT")
        assert 'method' in details
        assert details['method'] is not None

    def test_empty_chain_returns_reject(self):
        empty = OptionChain(ticker="X", expiration=date(2026, 7, 18),
                            stock_price=Money(100.0), calls={}, puts={})
        t = LiquidityThresholds()
        tier, details = classify_hybrid_tier(empty, 5.0, t, use_dynamic_thresholds=False)
        assert tier == "REJECT"
        assert details['method'] == "FAILED"

    def test_market_aware_returns_four_tuple(self):
        chain = make_chain()
        t = LiquidityThresholds()
        result = classify_hybrid_tier_market_aware(chain, 5.0, t)
        assert len(result) == 4
        tier, is_open, reason, details = result
        assert tier in ("EXCELLENT", "GOOD", "WARNING", "REJECT")
        assert isinstance(is_open, bool)
