"""Tests for strategy grouping logic."""

import pytest
from dataclasses import dataclass
from typing import Optional

# Import will fail until we create the module
from scripts.strategy_grouper import (
    group_legs_into_strategies,
    classify_strategy_type,
    StrategyGroup,
    Confidence,
)


@dataclass
class MockLeg:
    """Mock trade leg for testing."""
    id: int
    symbol: str
    acquired_date: str
    sale_date: str
    expiration: Optional[str]
    option_type: Optional[str]
    strike: Optional[float]
    gain_loss: float


class TestClassifyStrategyType:
    """Tests for strategy type classification."""

    def test_single_leg_is_single(self):
        assert classify_strategy_type(1) == "SINGLE"

    def test_two_legs_is_spread(self):
        assert classify_strategy_type(2) == "SPREAD"

    def test_four_legs_is_iron_condor(self):
        assert classify_strategy_type(4) == "IRON_CONDOR"

    def test_three_legs_returns_none(self):
        assert classify_strategy_type(3) is None

    def test_five_legs_returns_none(self):
        assert classify_strategy_type(5) is None


class TestGroupLegsIntoStrategies:
    """Tests for grouping logic."""

    def test_single_leg_groups_alone(self):
        legs = [
            MockLeg(1, "AAPL", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 150.0, 500.0)
        ]
        groups = group_legs_into_strategies(legs)

        assert len(groups) == 1
        assert groups[0].strategy_type == "SINGLE"
        assert groups[0].confidence == Confidence.HIGH
        assert len(groups[0].legs) == 1

    def test_two_matching_legs_form_spread(self):
        legs = [
            MockLeg(1, "APLD", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 25.0, 12542.49),
            MockLeg(2, "APLD", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 23.0, -6561.51),
        ]
        groups = group_legs_into_strategies(legs)

        assert len(groups) == 1
        assert groups[0].strategy_type == "SPREAD"
        assert groups[0].confidence == Confidence.HIGH
        assert len(groups[0].legs) == 2
        assert groups[0].combined_pnl == pytest.approx(5980.98, rel=0.01)

    def test_four_matching_legs_form_iron_condor(self):
        legs = [
            MockLeg(1, "SPY", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 580.0, 100.0),
            MockLeg(2, "SPY", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 575.0, -50.0),
            MockLeg(3, "SPY", "2026-01-07", "2026-01-08", "2026-01-16", "CALL", 600.0, 100.0),
            MockLeg(4, "SPY", "2026-01-07", "2026-01-08", "2026-01-16", "CALL", 605.0, -50.0),
        ]
        groups = group_legs_into_strategies(legs)

        assert len(groups) == 1
        assert groups[0].strategy_type == "IRON_CONDOR"
        assert groups[0].confidence == Confidence.HIGH
        assert len(groups[0].legs) == 4

    def test_different_symbols_not_grouped(self):
        legs = [
            MockLeg(1, "AAPL", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 150.0, 500.0),
            MockLeg(2, "MSFT", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 400.0, 300.0),
        ]
        groups = group_legs_into_strategies(legs)

        assert len(groups) == 2
        assert all(g.strategy_type == "SINGLE" for g in groups)

    def test_different_dates_not_grouped(self):
        legs = [
            MockLeg(1, "AAPL", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 150.0, 500.0),
            MockLeg(2, "AAPL", "2026-01-08", "2026-01-09", "2026-01-16", "PUT", 145.0, 300.0),
        ]
        groups = group_legs_into_strategies(legs)

        assert len(groups) == 2

    def test_different_expirations_medium_confidence(self):
        legs = [
            MockLeg(1, "AAPL", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 150.0, 500.0),
            MockLeg(2, "AAPL", "2026-01-07", "2026-01-08", "2026-01-23", "PUT", 145.0, 300.0),
        ]
        groups = group_legs_into_strategies(legs)

        # Different expirations = don't group (could be calendar spread but we don't support)
        assert len(groups) == 2

    def test_three_legs_flagged_for_review(self):
        legs = [
            MockLeg(1, "AAPL", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 150.0, 500.0),
            MockLeg(2, "AAPL", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 145.0, -200.0),
            MockLeg(3, "AAPL", "2026-01-07", "2026-01-08", "2026-01-16", "PUT", 140.0, 100.0),
        ]
        groups = group_legs_into_strategies(legs)

        assert len(groups) == 1
        assert groups[0].strategy_type is None  # Unknown
        assert groups[0].confidence == Confidence.LOW
        assert groups[0].needs_review is True
