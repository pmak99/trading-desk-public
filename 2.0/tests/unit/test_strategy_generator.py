"""Integration tests for the thin StrategyGenerator coordinator."""
import pytest
from decimal import Decimal
from datetime import date
from unittest.mock import MagicMock

from src.application.services.strategy.generator import StrategyGenerator
from src.config.config import StrategyConfig
from src.domain.types import (
    Money, Strike, OptionQuote, OptionChain, VRPResult, Percentage,
    StrategyRecommendation, SizingContext,
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


class TestVRPLiquidityReductionIntegration:
    """CLAUDE.md sizing Rule 4 (MARGINAL VRP or WARNING liquidity -> 50%
    reduction), verified end-to-end through generate_strategies -- not just
    the SizingContext math in isolation (see test_sizing_context.py), but
    that contracts actually come out halved AND every dollar total derived
    from contracts (max_profit/max_loss/capital_required) stays internally
    consistent with the reduced count, not stale from the first build.
    Added 2026-07-27.
    """

    def _boosted_config(self) -> StrategyConfig:
        # Force the contract CAP to be the binding constraint (not the Kelly
        # EV formula), so halving 100->50 reliably shows up in the result
        # regardless of this fake chain's specific premiums.
        return StrategyConfig(max_contracts=100, risk_budget_per_trade=10_000_000.0)

    def _make_favorable_chain(self) -> OptionChain:
        # make_realistic_chain()'s 25-delta/20-delta strikes (95P/90P) credit
        # $1.00 on a $5 width -> EV = 0.75*100 - 0.25*400 = -25 (negative),
        # so calculate_contracts_kelly bails out to kelly_min_contracts=1
        # before ever reaching apply_kelly_caps -- the code path these tests
        # need to exercise. Same strikes/deltas, wider short premium so EV
        # is comfortably positive and Kelly sizing actually engages the cap.
        puts = {
            make_strike(90.0): make_quote(bid=0.40, ask=0.60, delta=-0.20, oi=200),   # long
            make_strike(95.0): make_quote(bid=2.90, ask=3.10, delta=-0.25, oi=200),   # short
            make_strike(100.0): make_quote(bid=4.50, ask=5.00, delta=-0.50, oi=200),
        }
        calls = {
            make_strike(100.0): make_quote(bid=4.50, ask=5.00, delta=0.50, oi=200),
            make_strike(105.0): make_quote(bid=2.90, ask=3.10, delta=0.25, oi=200),    # short
            make_strike(110.0): make_quote(bid=0.40, ask=0.60, delta=0.20, oi=200),    # long
        }
        return OptionChain(
            ticker="TEST", expiration=date(2026, 7, 18),
            stock_price=Money(100.0), calls=calls, puts=puts,
        )

    def test_vrp_marginal_halves_contracts_vs_no_reduction(self):
        config = self._boosted_config()
        liquidity_scorer = MagicMock()
        liquidity_scorer.classify_option_tier.return_value = "EXCELLENT"
        liquidity_scorer.calculate_spread_pct.return_value = 15.0
        gen = StrategyGenerator(config=config, liquidity_scorer=liquidity_scorer)
        chain = self._make_favorable_chain()

        baseline = gen.generate_strategies(
            "TEST", chain, make_vrp(ratio=2.0), sizing_context=SizingContext(),
        )
        reduced = gen.generate_strategies(
            "TEST", chain, make_vrp(ratio=2.0),
            sizing_context=SizingContext(vrp_marginal=True),
        )

        base_contracts = baseline.strategies[0].contracts
        reduced_contracts = reduced.strategies[0].contracts
        assert base_contracts == 100
        assert reduced_contracts == 50
        # max_loss must scale with contracts, not be left stale from a
        # different count.
        per_contract_loss = float(baseline.strategies[0].max_loss.amount) / base_contracts
        assert float(reduced.strategies[0].max_loss.amount) == pytest.approx(
            per_contract_loss * reduced_contracts, rel=0.01
        )

    def test_warning_liquidity_triggers_rebuild_and_halves_contracts(self):
        config = self._boosted_config()

        excellent_scorer = MagicMock()
        excellent_scorer.classify_option_tier.return_value = "EXCELLENT"
        excellent_scorer.calculate_spread_pct.return_value = 15.0
        gen_excellent = StrategyGenerator(config=config, liquidity_scorer=excellent_scorer)

        warning_scorer = MagicMock()
        warning_scorer.classify_option_tier.return_value = "WARNING"
        warning_scorer.calculate_spread_pct.return_value = 20.0
        gen_warning = StrategyGenerator(config=config, liquidity_scorer=warning_scorer)

        chain = self._make_favorable_chain()
        baseline = gen_excellent.generate_strategies(
            "TEST", chain, make_vrp(ratio=2.0), sizing_context=SizingContext(),
        )
        reduced = gen_warning.generate_strategies(
            "TEST", chain, make_vrp(ratio=2.0), sizing_context=SizingContext(),
        )

        base_contracts = baseline.strategies[0].contracts
        reduced_contracts = reduced.strategies[0].contracts
        assert base_contracts == 100
        assert reduced_contracts == 50
        assert reduced.strategies[0].liquidity_tier == "WARNING"

        per_contract_loss = float(baseline.strategies[0].max_loss.amount) / base_contracts
        assert float(reduced.strategies[0].max_loss.amount) == pytest.approx(
            per_contract_loss * reduced_contracts, rel=0.01
        )

    def test_warning_liquidity_skipped_when_vrp_already_reduced(self):
        # Both conditions fire -> single reduction (not stacked to 25).
        config = self._boosted_config()
        warning_scorer = MagicMock()
        warning_scorer.classify_option_tier.return_value = "WARNING"
        warning_scorer.calculate_spread_pct.return_value = 20.0
        gen = StrategyGenerator(config=config, liquidity_scorer=warning_scorer)

        result = gen.generate_strategies(
            "TEST", self._make_favorable_chain(), make_vrp(ratio=2.0),
            sizing_context=SizingContext(vrp_marginal=True),
        )
        assert result.strategies[0].contracts == 50
        assert result.strategies[0].liquidity_tier == "WARNING"

    def test_no_reduction_when_liquidity_excellent_and_vrp_not_marginal(self):
        config = self._boosted_config()
        excellent_scorer = MagicMock()
        excellent_scorer.classify_option_tier.return_value = "EXCELLENT"
        excellent_scorer.calculate_spread_pct.return_value = 15.0
        gen = StrategyGenerator(config=config, liquidity_scorer=excellent_scorer)

        result = gen.generate_strategies(
            "TEST", self._make_favorable_chain(), make_vrp(ratio=2.0),
            sizing_context=SizingContext(),
        )
        assert result.strategies[0].contracts == 100
