"""
Unit tests for profit zone penalty multiplier in StrategyScorer.

Tests the _calculate_profit_zone_multiplier method which penalizes strategies
where the profit zone is narrower than the implied move range.

January 2026: Floor raised from 0.3 to 0.6 to reduce Iron Condor suppression.
"""

import pytest
from unittest.mock import MagicMock
from decimal import Decimal

from src.domain.scoring.strategy_scorer import StrategyScorer
from src.domain.enums import StrategyType


def make_mock_strategy(breakevens: list[float], stock_price: float, strategy_type=StrategyType.IRON_CONDOR):
    """Create a mock Strategy with given breakevens and stock price."""
    strategy = MagicMock()
    strategy.strategy_type = strategy_type
    strategy.stock_price = MagicMock()
    strategy.stock_price.amount = Decimal(str(stock_price))

    if breakevens:
        strategy.breakeven = [MagicMock(amount=Decimal(str(be))) for be in breakevens]
    else:
        strategy.breakeven = []

    return strategy


def make_mock_vrp(implied_move_pct: float):
    """Create a mock VRPResult with given implied move percentage."""
    vrp = MagicMock()
    vrp.implied_move_pct = MagicMock()
    vrp.implied_move_pct.value = implied_move_pct
    return vrp


class TestProfitZonePenalty:
    """Tests for profit zone penalty multiplier."""

    @pytest.fixture
    def scorer(self):
        return StrategyScorer()

    def test_no_penalty_when_zone_exceeds_move(self, scorer):
        """Profit zone >= implied move should have no penalty (multiplier = 1.0)."""
        # Stock at $100, breakevens at $85 and $115 = 30% profit zone
        # Implied move 10% = 20% total range
        # Zone (30%) > Range (20%) → no penalty
        strategy = make_mock_strategy([85.0, 115.0], 100.0)
        vrp = make_mock_vrp(10.0)

        multiplier = scorer._calculate_profit_zone_multiplier(strategy, vrp)
        assert multiplier == 1.0

    def test_slight_penalty_70_to_100_ratio(self, scorer):
        """Profit zone 70-100% of implied move should get slight penalty (0.9-1.0)."""
        # Stock at $100, breakevens at $92 and $108 = 16% profit zone
        # Implied move 10% = 20% total range
        # Zone (16%) / Range (20%) = 0.8 → slight penalty
        strategy = make_mock_strategy([92.0, 108.0], 100.0)
        vrp = make_mock_vrp(10.0)

        multiplier = scorer._calculate_profit_zone_multiplier(strategy, vrp)
        assert 0.9 <= multiplier < 1.0

    def test_moderate_penalty_40_to_70_ratio(self, scorer):
        """Profit zone 40-70% of implied move should get moderate penalty (0.8-0.9)."""
        # Stock at $100, breakevens at $95 and $105 = 10% profit zone
        # Implied move 10% = 20% total range
        # Zone (10%) / Range (20%) = 0.5 → moderate penalty
        strategy = make_mock_strategy([95.0, 105.0], 100.0)
        vrp = make_mock_vrp(10.0)

        multiplier = scorer._calculate_profit_zone_multiplier(strategy, vrp)
        assert 0.8 <= multiplier < 0.9

    def test_heavy_penalty_20_to_40_ratio(self, scorer):
        """Profit zone 20-40% of implied move should get heavy penalty (0.7-0.8)."""
        # Stock at $100, breakevens at $97 and $103 = 6% profit zone
        # Implied move 10% = 20% total range
        # Zone (6%) / Range (20%) = 0.3 → heavy penalty
        strategy = make_mock_strategy([97.0, 103.0], 100.0)
        vrp = make_mock_vrp(10.0)

        multiplier = scorer._calculate_profit_zone_multiplier(strategy, vrp)
        assert 0.7 <= multiplier < 0.8

    def test_severe_penalty_floor_at_0_6(self, scorer):
        """Profit zone < 20% of implied move should get floor penalty of 0.6."""
        # Stock at $100, breakevens at $99 and $101 = 2% profit zone
        # Implied move 10% = 20% total range
        # Zone (2%) / Range (20%) = 0.1 → severe penalty at floor
        strategy = make_mock_strategy([99.0, 101.0], 100.0)
        vrp = make_mock_vrp(10.0)

        multiplier = scorer._calculate_profit_zone_multiplier(strategy, vrp)
        assert multiplier == 0.6

    def test_no_breakevens_no_penalty(self, scorer):
        """Strategy with no breakevens should have no penalty."""
        strategy = make_mock_strategy([], 100.0)
        vrp = make_mock_vrp(10.0)

        multiplier = scorer._calculate_profit_zone_multiplier(strategy, vrp)
        assert multiplier == 1.0

    def test_single_breakeven_no_penalty(self, scorer):
        """Credit spread with single breakeven should have no penalty."""
        strategy = make_mock_strategy([95.0], 100.0)
        vrp = make_mock_vrp(10.0)

        multiplier = scorer._calculate_profit_zone_multiplier(strategy, vrp)
        assert multiplier == 1.0

    def test_extreme_narrow_zone_still_0_6_floor(self, scorer):
        """Even extremely narrow zone should not go below 0.6 floor."""
        # Stock at $100, breakevens at $99.9 and $100.1 = 0.2% profit zone
        strategy = make_mock_strategy([99.9, 100.1], 100.0)
        vrp = make_mock_vrp(15.0)  # 30% total range

        multiplier = scorer._calculate_profit_zone_multiplier(strategy, vrp)
        assert multiplier == 0.6  # Floor, not lower


class TestProfitZonePenaltyInterpolation:
    """Tests for smooth interpolation between penalty tiers."""

    @pytest.fixture
    def scorer(self):
        return StrategyScorer()

    def test_boundary_at_70_percent(self, scorer):
        """Test boundary between slight and moderate penalty at 70%."""
        # Stock at $100, zone exactly 70% of range
        # 70% of 20% = 14% zone → breakevens at $93 and $107
        strategy = make_mock_strategy([93.0, 107.0], 100.0)
        vrp = make_mock_vrp(10.0)

        multiplier = scorer._calculate_profit_zone_multiplier(strategy, vrp)
        assert multiplier == 0.9  # Bottom of slight penalty tier

    def test_boundary_at_40_percent(self, scorer):
        """Test boundary between moderate and heavy penalty at 40%."""
        # Stock at $100, zone exactly 40% of range
        # 40% of 20% = 8% zone → breakevens at $96 and $104
        strategy = make_mock_strategy([96.0, 104.0], 100.0)
        vrp = make_mock_vrp(10.0)

        multiplier = scorer._calculate_profit_zone_multiplier(strategy, vrp)
        assert multiplier == 0.8  # Bottom of moderate penalty tier

    def test_boundary_at_20_percent(self, scorer):
        """Test boundary between heavy and severe penalty at 20%."""
        # Stock at $100, zone exactly 20% of range
        # 20% of 20% = 4% zone → breakevens at $98 and $102
        strategy = make_mock_strategy([98.0, 102.0], 100.0)
        vrp = make_mock_vrp(10.0)

        multiplier = scorer._calculate_profit_zone_multiplier(strategy, vrp)
        assert multiplier == 0.7  # Bottom of heavy penalty tier


class TestProfitZonePenaltyJan2026Changes:
    """Tests specifically verifying the January 2026 floor raise from 0.3 to 0.6."""

    @pytest.fixture
    def scorer(self):
        return StrategyScorer()

    def test_floor_is_0_6_not_0_3(self, scorer):
        """Verify floor was raised from old 0.3 to new 0.6."""
        # Create extremely narrow zone to trigger floor
        strategy = make_mock_strategy([99.5, 100.5], 100.0)  # 1% zone
        vrp = make_mock_vrp(20.0)  # 40% total range, ratio = 0.025

        multiplier = scorer._calculate_profit_zone_multiplier(strategy, vrp)
        # Old behavior would return 0.3, new behavior returns 0.6
        assert multiplier == 0.6
        assert multiplier != 0.3  # Explicitly verify not old value

    def test_impact_on_iron_condor_score(self, scorer):
        """Iron Condor with tight zone should score higher than old system."""
        strategy = make_mock_strategy([98.0, 102.0], 100.0, StrategyType.IRON_CONDOR)
        vrp = make_mock_vrp(10.0)  # 20% total range, 4% zone = 20% ratio

        multiplier = scorer._calculate_profit_zone_multiplier(strategy, vrp)

        # At exactly 20% boundary: multiplier should be 0.7 (new) vs 0.5 (old)
        assert multiplier >= 0.7

        # A base score of 75 would become:
        # Old: 75 * 0.5 = 37.5 (would likely be filtered out)
        # New: 75 * 0.7 = 52.5 (more likely to be recommended)
