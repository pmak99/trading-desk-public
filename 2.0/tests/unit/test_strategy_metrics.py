"""
Tests for strategy/metrics.py — spread metrics, Greeks, and strategy liquidity.
"""
import pytest
from unittest.mock import MagicMock
from datetime import date
from decimal import Decimal

from src.application.services.strategy.metrics import (
    calculate_spread_metrics,
    calculate_position_greeks,
    combine_greeks,
    calculate_strategy_liquidity,
)
from src.domain.types import Money, Strike, OptionQuote, StrategyLeg, Strategy
from src.domain.enums import OptionType, StrategyType, DirectionalBias


def make_quote(bid: float, ask: float, delta: float = None, open_interest: int = 100) -> OptionQuote:
    return OptionQuote(
        bid=Money(bid),
        ask=Money(ask),
        open_interest=open_interest,
        delta=delta,
        gamma=0.01 if delta else None,
        theta=-0.05 if delta else None,
        vega=0.10 if delta else None,
    )


def make_strike(price: float) -> Strike:
    return Strike(price=Decimal(str(price)))


class TestCalculateSpreadMetrics:
    def test_basic_put_spread_metrics(self):
        short_strike = make_strike(95.0)  # higher strike (closer to ATM)
        long_strike = make_strike(90.0)   # lower strike (further OTM)
        short_quote = make_quote(bid=1.80, ask=2.20, delta=-0.25)
        long_quote = make_quote(bid=0.80, ask=1.20, delta=-0.15)

        result = calculate_spread_metrics(short_quote, long_quote, short_strike, long_strike)

        # net_credit = short_mid - long_mid = 2.0 - 1.0 = 1.0
        assert abs(float(result['net_credit'].amount) - 1.0) < 0.01
        # max_profit = net_credit * 100 = $100
        assert abs(float(result['max_profit'].amount) - 100.0) < 0.01
        # spread_width = 5.0, max_loss = (5.0 - 1.0) * 100 = $400
        assert abs(float(result['max_loss'].amount) - 400.0) < 0.01
        # breakeven = 95 - 1 = 94
        assert abs(float(result['breakeven'].amount) - 94.0) < 0.01
        # POP = 1 - |delta| = 1 - 0.25 = 0.75
        assert abs(result['pop'] - 0.75) < 0.01
        # reward/risk = 100 / 400 = 0.25
        assert abs(result['reward_risk'] - 0.25) < 0.01

    def test_call_spread_breakeven(self):
        short_strike = make_strike(105.0)  # lower strike (closer to ATM) for call
        long_strike = make_strike(110.0)   # higher strike (further OTM)
        short_quote = make_quote(bid=1.80, ask=2.20, delta=0.25)
        long_quote = make_quote(bid=0.80, ask=1.20, delta=0.15)

        result = calculate_spread_metrics(short_quote, long_quote, short_strike, long_strike)

        # For call spread: breakeven = short_strike + net_credit
        assert abs(float(result['breakeven'].amount) - 106.0) < 0.01

    def test_no_delta_uses_default_pop(self):
        short_strike = make_strike(95.0)
        long_strike = make_strike(90.0)
        short_quote = make_quote(bid=1.80, ask=2.20)  # no delta
        long_quote = make_quote(bid=0.80, ask=1.20)

        result = calculate_spread_metrics(short_quote, long_quote, short_strike, long_strike)
        assert result['pop'] == 0.75  # default when no delta


class TestCalculatePositionGreeks:
    def test_returns_none_when_no_greeks(self):
        short_strike = make_strike(95.0)
        short_quote = make_quote(bid=2.0, ask=2.5)  # no greeks

        result = calculate_position_greeks(
            [(short_strike, short_quote, -1)],
            contracts=10,
        )
        assert result['delta'] is None
        assert result['gamma'] is None

    def test_aggregates_greeks_across_legs(self):
        short_strike = make_strike(95.0)
        long_strike = make_strike(90.0)
        short_quote = make_quote(bid=2.0, ask=2.5, delta=-0.25)
        long_quote = make_quote(bid=1.0, ask=1.5, delta=-0.15)

        result = calculate_position_greeks(
            [(short_strike, short_quote, -1), (long_strike, long_quote, 1)],
            contracts=10,
        )
        # net delta: (-0.25 * -1 * 10 * 100) + (-0.15 * 1 * 10 * 100)
        # = 250 + (-150) = 100
        assert abs(result['delta'] - 100.0) < 0.01


class TestCombineGreeks:
    def test_combines_both_none(self):
        s1 = MagicMock()
        s1.position_delta = None
        s1.position_gamma = None
        s1.position_theta = None
        s1.position_vega = None
        s2 = MagicMock()
        s2.position_delta = None
        s2.position_gamma = None
        s2.position_theta = None
        s2.position_vega = None

        result = combine_greeks(s1, s2)
        assert result['delta'] is None

    def test_combines_one_none(self):
        s1 = MagicMock()
        s1.position_delta = 100.0
        s1.position_gamma = 0.5
        s1.position_theta = -5.0
        s1.position_vega = 10.0
        s2 = MagicMock()
        s2.position_delta = None
        s2.position_gamma = None
        s2.position_theta = None
        s2.position_vega = None

        result = combine_greeks(s1, s2)
        assert result['delta'] == 100.0

    def test_combines_both_present(self):
        s1 = MagicMock()
        s1.position_delta = 100.0
        s1.position_gamma = 0.5
        s1.position_theta = -5.0
        s1.position_vega = 10.0
        s2 = MagicMock()
        s2.position_delta = -80.0
        s2.position_gamma = 0.3
        s2.position_theta = -3.0
        s2.position_vega = 8.0

        result = combine_greeks(s1, s2)
        assert result['delta'] == 20.0
        assert abs(result['gamma'] - 0.8) < 0.01


class TestCalculateStrategyLiquidity:
    def test_no_legs_returns_reject(self):
        from src.domain.types import OptionChain
        scorer = MagicMock()
        chain = OptionChain(
            ticker="TEST",
            expiration=date(2026, 7, 18),
            stock_price=Money(100.0),
            calls={},
            puts={},
        )
        result = calculate_strategy_liquidity(scorer, "TEST", chain, [])
        assert result['liquidity_tier'] == "REJECT"

    def test_short_leg_tier_drives_overall(self):
        from src.domain.types import OptionChain
        scorer = MagicMock()
        scorer.classify_option_tier.return_value = "EXCELLENT"
        scorer.calculate_spread_pct.return_value = 10.0

        short_strike = make_strike(95.0)
        long_strike = make_strike(90.0)
        short_quote = make_quote(bid=1.8, ask=2.2, open_interest=200)
        long_quote = make_quote(bid=0.8, ask=1.2, open_interest=50)
        real_chain = OptionChain(
            ticker="TEST",
            expiration=date(2026, 7, 18),
            stock_price=Money(100.0),
            calls={},
            puts={short_strike: short_quote, long_strike: long_quote},
        )

        leg = StrategyLeg(
            strike=short_strike,
            option_type=OptionType.PUT,
            action="SELL",
            contracts=1,
            premium=Money(2.0),
        )
        long_leg = StrategyLeg(
            strike=long_strike,
            option_type=OptionType.PUT,
            action="BUY",
            contracts=1,
            premium=Money(1.0),
        )

        result = calculate_strategy_liquidity(scorer, "TEST", real_chain, [leg, long_leg])
        assert result['liquidity_tier'] == "EXCELLENT"
        scorer.classify_option_tier.assert_called_once()

    def test_reject_tier_propagates(self):
        from src.domain.types import OptionChain
        scorer = MagicMock()
        scorer.classify_option_tier.return_value = "REJECT"
        scorer.calculate_spread_pct.return_value = 80.0

        short_strike = make_strike(95.0)
        short_quote = make_quote(bid=1.8, ask=2.2, open_interest=5)
        real_chain = OptionChain(
            ticker="TEST",
            expiration=date(2026, 7, 18),
            stock_price=Money(100.0),
            calls={},
            puts={short_strike: short_quote},
        )
        leg = StrategyLeg(
            strike=short_strike,
            option_type=OptionType.PUT,
            action="SELL",
            contracts=1,
            premium=Money(2.0),
        )

        result = calculate_strategy_liquidity(scorer, "TEST", real_chain, [leg])
        assert result['liquidity_tier'] == "REJECT"
