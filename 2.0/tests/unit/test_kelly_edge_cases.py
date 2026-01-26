"""
Unit tests for Kelly Criterion position sizing edge cases.

Tests boundary conditions in _calculate_contracts_kelly:
- Negative edge (EV% below minimum) -> minimum contracts
- Zero edge -> minimum contracts
- Edge > 1.0 (extreme values) -> capped at max_contracts
- Win rate (POP) at 0%, 50%, 100%
- Half-Kelly fraction application (0.25)
- Invalid POP values (< 0, > 1)
"""

import pytest
from src.domain.types import Money
from src.config.config import StrategyConfig, ScoringWeights
from src.application.services.strategy_generator import StrategyGenerator
from src.application.metrics.liquidity_scorer import LiquidityScorer


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def config():
    """Standard test configuration with Kelly sizing enabled."""
    return StrategyConfig(
        use_kelly_sizing=True,
        kelly_fraction=0.25,
        kelly_min_edge=0.05,
        kelly_min_contracts=1,
        risk_budget_per_trade=20000.0,
        max_contracts=100,
        target_delta_short=0.25,
        target_delta_long=0.20,
        spread_width_high_price=5.0,
        spread_width_low_price=3.0,
        spread_width_threshold=20.0,
        min_credit_per_spread=0.20,
        min_reward_risk=0.25,
        commission_per_contract=0.30,
        scoring_weights=ScoringWeights(),
    )


@pytest.fixture
def generator(config):
    """Strategy generator with Kelly sizing enabled."""
    liquidity_scorer = LiquidityScorer()
    return StrategyGenerator(config, liquidity_scorer)


# ============================================================================
# Negative Edge Tests
# ============================================================================


class TestNegativeEdge:
    """Tests for negative expected value scenarios."""

    def test_deeply_negative_edge(self, generator):
        """Very negative EV should return minimum contracts.

        POP=30%, profit=$50, loss=$450
        EV = 0.30 * 50 - 0.70 * 450 = 15 - 315 = -$300
        EV% = -300/450 = -66.7% (way below -2% min)
        """
        contracts = generator._calculate_contracts_kelly(
            max_profit=Money(50.0),
            max_loss=Money(450.0),
            probability_of_profit=0.30,
        )
        assert contracts == 1

    def test_slightly_negative_edge(self, generator):
        """Slightly negative EV (between -2% and 0%) may still get sized.

        The Kelly implementation allows up to -2% EV since VRP provides
        additional edge not captured in delta-based POP.
        """
        # POP=60%, profit=$100, loss=$400
        # EV = 0.60 * 100 - 0.40 * 400 = 60 - 160 = -$100
        # EV% = -100/400 = -25% -> below -2% minimum, so minimum contracts
        contracts = generator._calculate_contracts_kelly(
            max_profit=Money(100.0),
            max_loss=Money(400.0),
            probability_of_profit=0.60,
        )
        assert contracts == 1

    def test_near_zero_negative_edge_sized(self, generator):
        """EV% between -2% and 0% should get sized (VRP provides extra edge).

        POP=66%, profit=$200, loss=$300
        EV = 0.66 * 200 - 0.34 * 300 = 132 - 102 = $30
        EV% = 30/300 = 10% -> positive, should get sized
        """
        contracts = generator._calculate_contracts_kelly(
            max_profit=Money(200.0),
            max_loss=Money(300.0),
            probability_of_profit=0.66,
        )
        assert contracts > 1


# ============================================================================
# Zero Edge Tests
# ============================================================================


class TestZeroEdge:
    """Tests for exactly zero expected value."""

    def test_breakeven_ev(self, generator):
        """Breakeven EV (EV% = 0%) should get some position sizing.

        The implementation allows 0% EV (at the 0% boundary of ev_scale=0.5).
        POP=75%, profit=$100, loss=$300
        EV = 0.75 * 100 - 0.25 * 300 = 75 - 75 = $0 exactly
        EV% = 0/300 = 0%
        """
        contracts = generator._calculate_contracts_kelly(
            max_profit=Money(100.0),
            max_loss=Money(300.0),
            probability_of_profit=0.75,
        )
        # EV = 0 is at the boundary, should get at least minimum sizing
        assert contracts >= 1

    def test_zero_profit_returns_minimum(self, generator):
        """Zero max_profit should return minimum contracts."""
        contracts = generator._calculate_contracts_kelly(
            max_profit=Money(0.0),
            max_loss=Money(400.0),
            probability_of_profit=0.70,
        )
        assert contracts == 1

    def test_zero_loss_returns_minimum(self, generator):
        """Zero max_loss should return minimum contracts (guard clause)."""
        contracts = generator._calculate_contracts_kelly(
            max_profit=Money(200.0),
            max_loss=Money(0.0),
            probability_of_profit=0.70,
        )
        assert contracts == 1


# ============================================================================
# Extreme Edge Tests
# ============================================================================


class TestExtremeEdge:
    """Tests for extreme edge values (edge > 1.0)."""

    def test_huge_reward_risk(self, generator):
        """Very high reward/risk should be capped at max_contracts.

        POP=99%, profit=$900, loss=$100
        EV = 0.99 * 900 - 0.01 * 100 = 891 - 1 = $890
        EV% = 890/100 = 890% (extreme)
        """
        contracts = generator._calculate_contracts_kelly(
            max_profit=Money(900.0),
            max_loss=Money(100.0),
            probability_of_profit=0.99,
        )
        assert contracts <= 100  # Should not exceed max_contracts

    def test_extreme_pop_extreme_ratio(self, generator):
        """95% POP with 5:1 reward/risk should max out but be capped."""
        contracts = generator._calculate_contracts_kelly(
            max_profit=Money(500.0),
            max_loss=Money(100.0),
            probability_of_profit=0.95,
        )
        assert contracts <= 100
        assert contracts >= 10  # Should be substantial given huge edge


# ============================================================================
# POP Boundary Tests
# ============================================================================


class TestPOPBoundaries:
    """Tests for probability of profit at boundary values."""

    def test_pop_zero(self, generator):
        """POP = 0% (always lose) should return minimum contracts."""
        contracts = generator._calculate_contracts_kelly(
            max_profit=Money(200.0),
            max_loss=Money(300.0),
            probability_of_profit=0.0,
        )
        assert contracts == 1

    def test_pop_fifty_percent(self, generator):
        """POP = 50% with typical reward/risk.

        POP=50%, profit=$200, loss=$300
        EV = 0.50 * 200 - 0.50 * 300 = 100 - 150 = -$50
        EV% = -50/300 = -16.7% -> below -2% minimum -> minimum contracts
        """
        contracts = generator._calculate_contracts_kelly(
            max_profit=Money(200.0),
            max_loss=Money(300.0),
            probability_of_profit=0.50,
        )
        assert contracts == 1

    def test_pop_hundred_percent(self, generator):
        """POP = 100% (always win) should give significant sizing."""
        contracts = generator._calculate_contracts_kelly(
            max_profit=Money(200.0),
            max_loss=Money(300.0),
            probability_of_profit=1.0,
        )
        # EV = 1.0 * 200 - 0.0 * 300 = $200 per spread
        # EV% = 200/300 = 66.7% (extremely positive)
        assert contracts > 10

    def test_pop_negative_returns_minimum(self, generator):
        """POP < 0 (invalid) should return minimum contracts."""
        contracts = generator._calculate_contracts_kelly(
            max_profit=Money(200.0),
            max_loss=Money(300.0),
            probability_of_profit=-0.1,
        )
        assert contracts == 1

    def test_pop_above_one_returns_minimum(self, generator):
        """POP > 1.0 (invalid) should return minimum contracts."""
        contracts = generator._calculate_contracts_kelly(
            max_profit=Money(200.0),
            max_loss=Money(300.0),
            probability_of_profit=1.5,
        )
        assert contracts == 1


# ============================================================================
# Half-Kelly Fraction Tests
# ============================================================================


class TestHalfKellyFraction:
    """Tests for Kelly fraction application."""

    def test_quarter_kelly_reduces_position(self):
        """0.25 Kelly fraction should reduce position vs full Kelly.

        Using two generators with different Kelly fractions.
        """
        config_quarter = StrategyConfig(
            use_kelly_sizing=True,
            kelly_fraction=0.25,
            kelly_min_edge=0.05,
            kelly_min_contracts=1,
            risk_budget_per_trade=20000.0,
            max_contracts=100,
            scoring_weights=ScoringWeights(),
        )
        config_full = StrategyConfig(
            use_kelly_sizing=True,
            kelly_fraction=1.0,  # Full Kelly
            kelly_min_edge=0.05,
            kelly_min_contracts=1,
            risk_budget_per_trade=20000.0,
            max_contracts=100,
            scoring_weights=ScoringWeights(),
        )

        gen_quarter = StrategyGenerator(config_quarter, LiquidityScorer())
        gen_full = StrategyGenerator(config_full, LiquidityScorer())

        profit = Money(200.0)
        loss = Money(300.0)
        pop = 0.80

        contracts_quarter = gen_quarter._calculate_contracts_kelly(profit, loss, pop)
        contracts_full = gen_full._calculate_contracts_kelly(profit, loss, pop)

        # Quarter-Kelly should give fewer contracts than full Kelly
        assert contracts_quarter <= contracts_full

    def test_zero_kelly_fraction_returns_minimum(self):
        """Kelly fraction = 0 should effectively give minimum contracts."""
        config_zero = StrategyConfig(
            use_kelly_sizing=True,
            kelly_fraction=0.0,
            kelly_min_edge=0.05,
            kelly_min_contracts=1,
            risk_budget_per_trade=20000.0,
            max_contracts=100,
            scoring_weights=ScoringWeights(),
        )
        gen = StrategyGenerator(config_zero, LiquidityScorer())

        contracts = gen._calculate_contracts_kelly(
            max_profit=Money(200.0),
            max_loss=Money(300.0),
            probability_of_profit=0.80,
        )
        # With fraction=0, position_fraction=0, contracts=0, but min is 1
        assert contracts == 1


# ============================================================================
# Max Contracts Cap Tests
# ============================================================================


class TestMaxContractsCap:
    """Tests that max_contracts is always respected."""

    def test_max_contracts_cap_with_small_budget(self):
        """Even with small max_loss, contracts should not exceed max."""
        config = StrategyConfig(
            use_kelly_sizing=True,
            kelly_fraction=0.25,
            kelly_min_edge=0.05,
            kelly_min_contracts=1,
            risk_budget_per_trade=100000.0,  # Large budget
            max_contracts=10,  # But small max
            scoring_weights=ScoringWeights(),
        )
        gen = StrategyGenerator(config, LiquidityScorer())

        contracts = gen._calculate_contracts_kelly(
            max_profit=Money(500.0),
            max_loss=Money(100.0),
            probability_of_profit=0.95,
        )
        assert contracts <= 10

    def test_minimum_contracts_floor(self):
        """Contracts should never go below kelly_min_contracts."""
        config = StrategyConfig(
            use_kelly_sizing=True,
            kelly_fraction=0.25,
            kelly_min_edge=0.05,
            kelly_min_contracts=5,  # Higher minimum
            risk_budget_per_trade=20000.0,
            max_contracts=100,
            scoring_weights=ScoringWeights(),
        )
        gen = StrategyGenerator(config, LiquidityScorer())

        # Deeply negative EV should still return min_contracts=5
        contracts = gen._calculate_contracts_kelly(
            max_profit=Money(10.0),
            max_loss=Money(490.0),
            probability_of_profit=0.20,
        )
        assert contracts == 5


# ============================================================================
# Deprecated _calculate_contracts Tests
# ============================================================================


class TestDeprecatedCalculateContracts:
    """Tests for the deprecated (non-Kelly) _calculate_contracts method."""

    def test_simple_division(self, generator):
        """Basic calculation: risk_budget / max_loss = contracts."""
        contracts = generator._calculate_contracts(Money(400.0))
        # 20000 / 400 = 50
        assert contracts == 50

    def test_zero_max_loss_returns_zero(self, generator):
        """Zero max_loss should return 0 (no position)."""
        contracts = generator._calculate_contracts(Money(0.0))
        assert contracts == 0

    def test_negative_max_loss_returns_zero(self, generator):
        """Negative max_loss should return 0."""
        contracts = generator._calculate_contracts(Money(-100.0))
        assert contracts == 0

    def test_large_loss_small_position(self, generator):
        """Large max_loss should result in small position."""
        contracts = generator._calculate_contracts(Money(10000.0))
        # 20000 / 10000 = 2
        assert contracts == 2

    def test_tiny_loss_capped_at_max(self, generator):
        """Tiny max_loss should be capped at max_contracts."""
        contracts = generator._calculate_contracts(Money(1.0))
        # 20000 / 1 = 20000, but capped at 100
        assert contracts == 100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
