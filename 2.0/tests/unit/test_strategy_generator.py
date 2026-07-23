"""Integration tests for the thin StrategyGenerator coordinator."""
import pytest
from decimal import Decimal
from datetime import date
from unittest.mock import MagicMock

from src.application.services.strategy.generator import StrategyGenerator
from src.config.config import StrategyConfig
from src.domain.types import (
    Money, Strike, OptionQuote, OptionChain, VRPResult, Percentage, StrategyRecommendation
)
from src.domain.enums import DirectionalBias, Recommendation


def make_strike(price: float) -> Strike:
    return Strike(price=Decimal(str(price)))


def make_quote(bid: float, ask: float, delta: float = None, oi: int = 200) -> OptionQuote:
    return OptionQuote(
        bid=Money(bid), ask=Money(ask), open_interest=oi, delta=delta,
        gamma=0.01 if delta else None,
        theta=-0.05 if delta else None,
        vega=0.10 if delta else None,
    )


def make_vrp(ratio: float = 2.0) -> VRPResult:
    return VRPResult(
        ticker="TEST",
        expiration=date(2026, 7, 18),
        implied_move_pct=Percentage(5.0),
        historical_mean_move_pct=Percentage(5.0 / ratio),
        vrp_ratio=ratio,
        edge_score=0.5,
        recommendation=Recommendation.EXCELLENT,
    )


def make_realistic_chain() -> OptionChain:
    """Put + call chain with tiered premiums for a $100 stock."""
    puts = {}
    for price, delta, bid, ask in [
        (75.0,  -0.05, 0.10, 0.20),
        (80.0,  -0.08, 0.20, 0.40),
        (85.0,  -0.12, 0.40, 0.70),
        (90.0,  -0.15, 0.80, 1.20),  # long (mid=1.0)
        (93.0,  -0.20, 1.20, 1.80),
        (95.0,  -0.25, 1.80, 2.20),  # short (mid=2.0)
        (97.0,  -0.30, 2.50, 3.00),
        (100.0, -0.50, 4.50, 5.00),
    ]:
        puts[make_strike(price)] = make_quote(bid=bid, ask=ask, delta=delta)
    calls = {}
    for price, delta, bid, ask in [
        (100.0, 0.50, 4.50, 5.00),
        (103.0, 0.30, 2.50, 3.00),
        (105.0, 0.25, 1.80, 2.20),  # short (mid=2.0)
        (107.0, 0.20, 1.20, 1.80),
        (110.0, 0.15, 0.80, 1.20),  # long (mid=1.0)
        (115.0, 0.12, 0.40, 0.70),
        (120.0, 0.08, 0.20, 0.40),
    ]:
        calls[make_strike(price)] = make_quote(bid=bid, ask=ask, delta=delta)
    return OptionChain(
        ticker="TEST", expiration=date(2026, 7, 18),
        stock_price=Money(100.0), calls=calls, puts=puts,
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


@pytest.fixture
def generator(config, liquidity_scorer):
    return StrategyGenerator(config=config, liquidity_scorer=liquidity_scorer)


class TestStrategyGenerator:
    def test_generate_strategies_returns_recommendation(self, generator):
        chain = make_realistic_chain()
        result = generator.generate_strategies("TEST", chain, make_vrp())
        assert isinstance(result, StrategyRecommendation)
        assert result.ticker == "TEST"
        assert len(result.strategies) >= 1

    def test_excellent_vrp_neutral_generates_two_strategies(self, generator):
        chain = make_realistic_chain()
        result = generator.generate_strategies("TEST", chain, make_vrp(ratio=2.0))
        assert len(result.strategies) == 2

    def test_raises_when_no_strategies_built(self, config, liquidity_scorer):
        empty_chain = OptionChain(
            ticker="TEST", expiration=date(2026, 7, 18),
            stock_price=Money(100.0), calls={}, puts={},
        )
        gen = StrategyGenerator(config=config, liquidity_scorer=liquidity_scorer)
        with pytest.raises(ValueError, match="Could not generate any valid strategies"):
            gen.generate_strategies("TEST", empty_chain, make_vrp())

    def test_recommended_index_is_zero(self, generator):
        result = generator.generate_strategies("TEST", make_realistic_chain(), make_vrp())
        assert result.recommended_index == 0

    def test_strategies_sorted_by_score_descending(self, generator):
        result = generator.generate_strategies("TEST", make_realistic_chain(), make_vrp())
        scores = [s.overall_score for s in result.strategies]
        assert scores == sorted(scores, reverse=True)
