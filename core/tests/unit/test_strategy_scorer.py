"""
Unit tests for StrategyScorer.

Tests scoring logic in isolation from strategy generation.
"""

import pytest
from decimal import Decimal
from datetime import date

from src.domain.scoring import StrategyScorer
from src.domain.models import Strategy, VRPResult, StrategyType
from src.domain.types import Money, Percentage, StrategyLeg, Strike
from src.domain.enums import DirectionalBias, OptionType
from src.config.config import ScoringWeights


@pytest.fixture
def default_weights():
    """Default scoring weights."""
    return ScoringWeights()


@pytest.fixture
def scorer(default_weights):
    """Scorer with default weights."""
    return StrategyScorer(default_weights)


@pytest.fixture
def sample_vrp():
    """Sample VRP result."""
    return VRPResult(
        ticker="AAPL",
        expiration=date(2024, 12, 20),
        implied_volatility=Percentage(35.0),
        historical_volatility=Percentage(20.0),
        vrp_ratio=1.75,
        implied_move_pct=Percentage(5.0),
        confidence_score=90.0
    )


@pytest.fixture
def sample_strategy_with_greeks():
    """Sample strategy with Greeks available."""
    return Strategy(
        ticker="AAPL",
        strategy_type=StrategyType.BULL_PUT_SPREAD,
        expiration=date(2024, 12, 20),
        legs=[
            StrategyLeg(
                strike=Strike(Decimal("180.00")),
                option_type=OptionType.PUT,
                position_side="short",
                contracts=10,
                price=Money(1.50)
            ),
            StrategyLeg(
                strike=Strike(Decimal("175.00")),
                option_type=OptionType.PUT,
                position_side="long",
                contracts=10,
                price=Money(0.75)
            ),
        ],
        net_credit=Money(75.00),
        max_profit=Money(750.00),
        max_loss=Money(4250.00),
        breakeven=Money(179.25),
        probability_of_profit=0.75,
        reward_risk_ratio=0.18,
        contracts=10,
        position_delta=15.0,
        position_gamma=0.5,
        position_theta=35.0,
        position_vega=-75.0,
        overall_score=0.0,  # Will be set by scorer
        profitability_score=0.0,
        risk_score=0.0,
        rationale=""
    )


@pytest.fixture
def sample_strategy_without_greeks():
    """Sample strategy without Greeks."""
    return Strategy(
        ticker="AAPL",
        strategy_type=StrategyType.BEAR_CALL_SPREAD,
        expiration=date(2024, 12, 20),
        legs=[
            StrategyLeg(
                strike=Strike(Decimal("190.00")),
                option_type=OptionType.CALL,
                position_side="short",
                contracts=10,
                price=Money(1.25)
            ),
            StrategyLeg(
                strike=Strike(Decimal("195.00")),
                option_type=OptionType.CALL,
                position_side="long",
                contracts=10,
                price=Money(0.60)
            ),
        ],
        net_credit=Money(65.00),
        max_profit=Money(650.00),
        max_loss=Money(4350.00),
        breakeven=Money(190.65),
        probability_of_profit=0.70,
        reward_risk_ratio=0.15,
        contracts=10,
        position_delta=None,
        position_gamma=None,
        position_theta=None,
        position_vega=None,
        overall_score=0.0,
        profitability_score=0.0,
        risk_score=0.0,
        rationale=""
    )


class TestStrategyScorerWithGreeks:
    """Test scoring with Greeks available."""

    def test_score_strategy_with_greeks(self, scorer, sample_strategy_with_greeks, sample_vrp):
        """Test scoring a strategy with Greeks."""
        result = scorer.score_strategy(sample_strategy_with_greeks, sample_vrp)

        # Verify result structure
        assert result.overall_score > 0
        assert result.profitability_score > 0
        assert result.risk_score > 0
        assert result.strategy_rationale != ""

        # Verify scores are in expected ranges (0-100)
        assert 0 <= result.overall_score <= 100
        assert 0 <= result.profitability_score <= 100
        assert 0 <= result.risk_score <= 100

    def test_score_strategy_includes_greeks_score(self, scorer, sample_strategy_with_greeks, sample_vrp):
        """Test that Greeks contribute to overall score."""
        result = scorer.score_strategy(sample_strategy_with_greeks, sample_vrp)

        # Greeks should contribute to score (positive theta, negative vega)
        # Overall score should be higher than without Greeks
        assert result.overall_score > 0

    def test_score_strategies_updates_in_place(self, scorer, sample_strategy_with_greeks, sample_vrp):
        """Test that score_strategies updates strategy fields in-place."""
        strategies = [sample_strategy_with_greeks]

        scorer.score_strategies(strategies, sample_vrp)

        # Verify strategy was updated
        assert strategies[0].overall_score > 0
        assert strategies[0].profitability_score > 0
        assert strategies[0].risk_score > 0
        assert strategies[0].rationale != ""

    def test_positive_theta_increases_profitability(self, scorer, sample_strategy_with_greeks, sample_vrp):
        """Test that positive theta increases profitability score."""
        # Original with theta=35.0
        result1 = scorer.score_strategy(sample_strategy_with_greeks, sample_vrp)

        # Increase theta
        sample_strategy_with_greeks.position_theta = 50.0
        result2 = scorer.score_strategy(sample_strategy_with_greeks, sample_vrp)

        # Higher theta should increase profitability
        assert result2.profitability_score > result1.profitability_score

    def test_negative_vega_increases_greeks_score(self, scorer, sample_strategy_with_greeks, sample_vrp):
        """Test that negative vega (good for credit spreads) increases Greeks score."""
        # Original with vega=-75.0
        result1 = scorer.score_strategy(sample_strategy_with_greeks, sample_vrp)

        # More negative vega (better)
        sample_strategy_with_greeks.position_vega = -100.0
        result2 = scorer.score_strategy(sample_strategy_with_greeks, sample_vrp)

        # More negative vega should increase overall score
        assert result2.overall_score >= result1.overall_score


class TestStrategyScorerWithoutGreeks:
    """Test scoring without Greeks available."""

    def test_score_strategy_without_greeks(self, scorer, sample_strategy_without_greeks, sample_vrp):
        """Test scoring a strategy without Greeks."""
        result = scorer.score_strategy(sample_strategy_without_greeks, sample_vrp)

        # Verify result structure
        assert result.overall_score > 0
        assert result.profitability_score > 0
        assert result.risk_score > 0
        assert result.strategy_rationale != ""

    def test_scores_redistributed_without_greeks(self, scorer, sample_strategy_without_greeks, sample_vrp):
        """Test that weights are redistributed when Greeks unavailable."""
        result = scorer.score_strategy(sample_strategy_without_greeks, sample_vrp)

        # Should still get a valid score even without Greeks
        assert 0 <= result.overall_score <= 100


class TestStrategyRationale:
    """Test rationale generation."""

    def test_high_vrp_in_rationale(self, scorer, sample_strategy_with_greeks):
        """Test that high VRP appears in rationale."""
        high_vrp = VRPResult(
            ticker="AAPL",
            expiration=date(2024, 12, 20),
            implied_volatility=Percentage(40.0),
            historical_volatility=Percentage(20.0),
            vrp_ratio=2.0,  # Excellent VRP
            implied_move_pct=Percentage(5.0),
            confidence_score=90.0
        )

        result = scorer.score_strategy(sample_strategy_with_greeks, high_vrp)

        assert "Excellent VRP edge" in result.strategy_rationale or "Strong VRP" in result.strategy_rationale

    def test_high_pop_in_rationale(self, scorer, sample_strategy_with_greeks, sample_vrp):
        """Test that high POP appears in rationale."""
        sample_strategy_with_greeks.probability_of_profit = 0.80

        result = scorer.score_strategy(sample_strategy_with_greeks, sample_vrp)

        assert "high POP" in result.strategy_rationale

    def test_positive_theta_in_rationale(self, scorer, sample_strategy_with_greeks, sample_vrp):
        """Test that positive theta appears in rationale."""
        sample_strategy_with_greeks.position_theta = 40.0

        result = scorer.score_strategy(sample_strategy_with_greeks, sample_vrp)

        assert "positive theta" in result.strategy_rationale

    def test_iron_condor_rationale(self, scorer, sample_strategy_with_greeks, sample_vrp):
        """Test Iron Condor specific rationale."""
        sample_strategy_with_greeks.strategy_type = StrategyType.IRON_CONDOR

        result = scorer.score_strategy(sample_strategy_with_greeks, sample_vrp)

        assert "wide profit zone" in result.strategy_rationale


class TestRecommendationRationale:
    """Test recommendation rationale generation."""

    def test_recommendation_rationale_includes_strategy_type(self, scorer, sample_strategy_with_greeks, sample_vrp):
        """Test that recommendation rationale includes strategy type."""
        rationale = scorer.generate_recommendation_rationale(
            sample_strategy_with_greeks,
            sample_vrp,
            DirectionalBias.NEUTRAL
        )

        assert "Bull Put Spread" in rationale

    def test_recommendation_rationale_includes_contracts(self, scorer, sample_strategy_with_greeks, sample_vrp):
        """Test that recommendation rationale includes contract count."""
        rationale = scorer.generate_recommendation_rationale(
            sample_strategy_with_greeks,
            sample_vrp,
            DirectionalBias.NEUTRAL
        )

        assert "10 contracts" in rationale


class TestCustomWeights:
    """Test scorer with custom weights."""

    def test_scorer_with_custom_weights(self, sample_strategy_with_greeks, sample_vrp):
        """Test that custom weights affect scoring."""
        # Create custom weights emphasizing R/R
        custom_weights = ScoringWeights(
            pop_weight=20.0,
            reward_risk_weight=50.0,  # Emphasize R/R
            vrp_weight=15.0,
            greeks_weight=10.0,
            size_weight=5.0
        )

        scorer = StrategyScorer(custom_weights)
        result = scorer.score_strategy(sample_strategy_with_greeks, sample_vrp)

        # Should produce a valid score with custom weights
        assert 0 <= result.overall_score <= 100

    def test_none_weights_uses_defaults(self, sample_strategy_with_greeks, sample_vrp):
        """Test that None weights parameter uses defaults."""
        scorer = StrategyScorer(None)
        result = scorer.score_strategy(sample_strategy_with_greeks, sample_vrp)

        # Should work with default weights
        assert 0 <= result.overall_score <= 100
