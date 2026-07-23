"""
Smoke tests for build_vertical_spread — confirms it returns correct types.
Deep logic is covered by the unit tests for its dependencies.
"""
import pytest
from decimal import Decimal
from datetime import date
from unittest.mock import MagicMock

from src.application.services.strategy.spread_builder import build_vertical_spread
from src.config.config import StrategyConfig
from src.domain.types import Money, Strike, OptionQuote, OptionChain, VRPResult, Percentage, Strategy
from src.domain.enums import OptionType, StrategyType, DirectionalBias, Recommendation


def make_strike(price: float) -> Strike:
    return Strike(price=Decimal(str(price)))


def make_quote(bid: float, ask: float, delta: float = None, oi: int = 200) -> OptionQuote:
    return OptionQuote(
        bid=Money(bid), ask=Money(ask), open_interest=oi, delta=delta,
        gamma=0.01 if delta else None,
        theta=-0.05 if delta else None,
        vega=0.10 if delta else None,
    )


def make_vrp(ratio: float = 2.0, implied_move_pct: float = 5.0) -> VRPResult:
    return VRPResult(
        ticker="TEST",
        expiration=date(2026, 7, 18),
        implied_move_pct=Percentage(implied_move_pct),
        historical_mean_move_pct=Percentage(implied_move_pct / ratio),
        vrp_ratio=ratio,
        edge_score=0.5,
        recommendation=Recommendation.EXCELLENT,
    )


def make_put_chain(stock_price: float = 100.0) -> OptionChain:
    """Put chain with tiered premiums — closer-to-ATM strikes have higher bids."""
    puts = {}
    for price, delta, bid, ask in [
        (75.0,  -0.05, 0.10, 0.20),
        (80.0,  -0.08, 0.20, 0.40),
        (85.0,  -0.12, 0.40, 0.70),
        (90.0,  -0.15, 0.80, 1.20),  # long strike (distance-based: $90)
        (93.0,  -0.20, 1.20, 1.80),
        (95.0,  -0.25, 1.80, 2.20),  # short strike (distance-based: $95, mid=2.0)
        (97.0,  -0.30, 2.50, 3.00),
        (100.0, -0.50, 4.50, 5.00),
    ]:
        puts[make_strike(price)] = make_quote(bid=bid, ask=ask, delta=delta)
    return OptionChain(
        ticker="TEST", expiration=date(2026, 7, 18),
        stock_price=Money(stock_price), calls={}, puts=puts,
    )


def make_call_chain(stock_price: float = 100.0) -> OptionChain:
    """Call chain with tiered premiums — closer-to-ATM strikes have higher bids."""
    calls = {}
    for price, delta, bid, ask in [
        (100.0, 0.50, 4.50, 5.00),
        (103.0, 0.30, 2.50, 3.00),
        (105.0, 0.25, 1.80, 2.20),  # short strike (distance-based: $105, mid=2.0)
        (107.0, 0.20, 1.20, 1.80),
        (110.0, 0.15, 0.80, 1.20),  # long strike (distance-based: $110, mid=1.0)
        (115.0, 0.12, 0.40, 0.70),
        (120.0, 0.08, 0.20, 0.40),
    ]:
        calls[make_strike(price)] = make_quote(bid=bid, ask=ask, delta=delta)
    return OptionChain(
        ticker="TEST", expiration=date(2026, 7, 18),
        stock_price=Money(stock_price), calls=calls, puts={},
    )


@pytest.fixture
def config():
    return StrategyConfig()


@pytest.fixture
def liquidity_scorer():
    scorer = MagicMock()
    scorer.classify_option_tier.return_value = "EXCELLENT"
    scorer.calculate_spread_pct.return_value = 15.0
    return scorer


class TestBuildVerticalSpread:
    def test_bull_put_spread_returns_strategy(self, config, liquidity_scorer):
        chain = make_put_chain()
        vrp = make_vrp()
        result = build_vertical_spread(
            config, liquidity_scorer, "TEST", chain, vrp,
            OptionType.PUT, StrategyType.BULL_PUT_SPREAD,
            below=True, bias=DirectionalBias.NEUTRAL,
        )
        assert result is not None
        assert isinstance(result, Strategy)
        assert result.strategy_type == StrategyType.BULL_PUT_SPREAD
        assert result.contracts >= 1
        assert float(result.net_credit.amount) > 0

    def test_bear_call_spread_returns_strategy(self, config, liquidity_scorer):
        chain = make_call_chain()
        vrp = make_vrp()
        result = build_vertical_spread(
            config, liquidity_scorer, "TEST", chain, vrp,
            OptionType.CALL, StrategyType.BEAR_CALL_SPREAD,
            below=False, bias=DirectionalBias.NEUTRAL,
        )
        assert result is not None
        assert isinstance(result, Strategy)
        assert result.strategy_type == StrategyType.BEAR_CALL_SPREAD

    def test_no_strikes_in_chain_returns_none(self, config, liquidity_scorer):
        empty_chain = OptionChain(
            ticker="TEST", expiration=date(2026, 7, 18),
            stock_price=Money(100.0), calls={}, puts={},
        )
        result = build_vertical_spread(
            config, liquidity_scorer, "TEST", empty_chain, make_vrp(),
            OptionType.PUT, StrategyType.BULL_PUT_SPREAD,
            below=True, bias=DirectionalBias.NEUTRAL,
        )
        assert result is None

    def test_net_credit_above_minimum(self, config, liquidity_scorer):
        chain = make_put_chain()
        vrp = make_vrp()
        result = build_vertical_spread(
            config, liquidity_scorer, "TEST", chain, vrp,
            OptionType.PUT, StrategyType.BULL_PUT_SPREAD,
            below=True, bias=DirectionalBias.NEUTRAL,
        )
        assert result is not None
        assert float(result.net_credit.amount) >= config.min_credit_per_spread

    def test_strategy_has_two_legs(self, config, liquidity_scorer):
        chain = make_put_chain()
        result = build_vertical_spread(
            config, liquidity_scorer, "TEST", chain, make_vrp(),
            OptionType.PUT, StrategyType.BULL_PUT_SPREAD,
            below=True, bias=DirectionalBias.NEUTRAL,
        )
        assert result is not None
        assert len(result.legs) == 2
        sell_legs = [l for l in result.legs if l.action == "SELL"]
        buy_legs = [l for l in result.legs if l.action == "BUY"]
        assert len(sell_legs) == 1
        assert len(buy_legs) == 1
