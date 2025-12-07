"""
Unit tests for ticker scoring system.
"""

import pytest
from datetime import date

from src.config.scoring_config import ScoringConfig, ScoringWeights, ScoringThresholds
from src.application.services.scorer import TickerScorer, TickerScore


@pytest.fixture
def default_config():
    """Default scoring configuration for testing."""
    return ScoringConfig(
        name="Test",
        description="Test config",
        weights=ScoringWeights(
            vrp_weight=0.40,
            consistency_weight=0.30,
            skew_weight=0.15,
            liquidity_weight=0.15,
        ),
        thresholds=ScoringThresholds(),
        max_positions=10,
        min_score=60.0,
    )


@pytest.fixture
def scorer(default_config):
    """Scorer instance for testing."""
    return TickerScorer(default_config)


class TestVRPScore:
    """Tests for VRP scoring."""

    def test_excellent_vrp(self, scorer):
        """VRP >= 2.0x should score 100."""
        score = scorer.calculate_vrp_score(2.0)
        assert score == 100.0

        score = scorer.calculate_vrp_score(2.5)
        assert score == 100.0

    def test_good_vrp(self, scorer):
        """VRP 1.5-2.0x should score 75-100."""
        score = scorer.calculate_vrp_score(1.5)
        assert score == 75.0

        score = scorer.calculate_vrp_score(1.75)
        assert 75.0 < score < 100.0

    def test_marginal_vrp(self, scorer):
        """VRP 1.2-1.5x should score 50-75."""
        score = scorer.calculate_vrp_score(1.2)
        assert score == 50.0

        score = scorer.calculate_vrp_score(1.35)
        assert 50.0 < score < 75.0

    def test_poor_vrp(self, scorer):
        """VRP < 1.0x should score 0 (negative edge)."""
        # VRP < 1.0 means implied vol is LESS than historical (negative edge)
        score = scorer.calculate_vrp_score(0.5)
        assert score == 0.0  # Below 1.0x = no edge

        score = scorer.calculate_vrp_score(0.0)
        assert score == 0.0

    def test_between_1_and_marginal_vrp(self, scorer):
        """VRP between 1.0x and marginal (1.2x) should interpolate 0-50."""
        score = scorer.calculate_vrp_score(1.1)  # Between 1.0 and 1.2
        assert 0.0 < score < 50.0

    def test_invalid_vrp(self, scorer):
        """Negative or None VRP should score 0."""
        assert scorer.calculate_vrp_score(None) == 0.0
        assert scorer.calculate_vrp_score(-1.0) == 0.0


class TestConsistencyScore:
    """Tests for consistency scoring."""

    def test_excellent_consistency(self, scorer):
        """Consistency >= 0.8 should score 100."""
        score = scorer.calculate_consistency_score(0.8)
        assert score == 100.0

        score = scorer.calculate_consistency_score(1.0)
        assert score == 100.0

    def test_good_consistency(self, scorer):
        """Consistency 0.6-0.8 should score 75-100."""
        score = scorer.calculate_consistency_score(0.6)
        assert score == 75.0

        score = scorer.calculate_consistency_score(0.7)
        assert 75.0 < score < 100.0

    def test_marginal_consistency(self, scorer):
        """Consistency 0.4-0.6 should score 50-75."""
        score = scorer.calculate_consistency_score(0.4)
        assert score == 50.0

        score = scorer.calculate_consistency_score(0.5)
        assert 50.0 < score < 75.0

    def test_poor_consistency(self, scorer):
        """Consistency < 0.4 (marginal threshold) should score 0."""
        # Below marginal = no meaningful consistency
        score = scorer.calculate_consistency_score(0.2)
        assert score == 0.0

        score = scorer.calculate_consistency_score(0.0)
        assert score == 0.0

    def test_invalid_consistency(self, scorer):
        """Negative or None consistency should score 0."""
        assert scorer.calculate_consistency_score(None) == 0.0
        assert scorer.calculate_consistency_score(-0.1) == 0.0


class TestSkewScore:
    """Tests for skew scoring."""

    def test_neutral_skew(self, scorer):
        """Neutral skew (abs ≤ 0.15) should score 100."""
        assert scorer.calculate_skew_score(0.0) == 100.0
        assert scorer.calculate_skew_score(0.10) == 100.0
        assert scorer.calculate_skew_score(-0.15) == 100.0

    def test_moderate_skew(self, scorer):
        """Moderate skew (0.15-0.35) should score 70-100."""
        score = scorer.calculate_skew_score(0.25)
        assert 70.0 <= score < 100.0

    def test_extreme_skew(self, scorer):
        """Extreme skew (>0.35) should score lower."""
        score = scorer.calculate_skew_score(0.5)
        assert score < 70.0

    def test_no_skew_data(self, scorer):
        """None skew should default to 75 (neutral)."""
        assert scorer.calculate_skew_score(None) == 75.0


class TestLiquidityScore:
    """Tests for liquidity scoring."""

    def test_excellent_liquidity(self, scorer):
        """Excellent liquidity should score high."""
        score = scorer.calculate_liquidity_score(
            open_interest=1000,
            bid_ask_spread_pct=3.0,
            volume=500,
        )
        assert score == 100.0

    def test_good_liquidity(self, scorer):
        """Good liquidity should score 60-80."""
        score = scorer.calculate_liquidity_score(
            open_interest=500,
            bid_ask_spread_pct=7.0,
            volume=100,
        )
        assert 60.0 <= score < 100.0

    def test_marginal_liquidity(self, scorer):
        """Marginal liquidity should score 40-60."""
        score = scorer.calculate_liquidity_score(
            open_interest=100,
            bid_ask_spread_pct=12.0,
            volume=50,
        )
        assert 40.0 <= score < 70.0

    def test_poor_liquidity(self, scorer):
        """Poor liquidity should score low."""
        score = scorer.calculate_liquidity_score(
            open_interest=25,
            bid_ask_spread_pct=20.0,
            volume=10,
        )
        assert score < 40.0

    def test_missing_liquidity_data(self, scorer):
        """Missing data should use neutral scoring."""
        score = scorer.calculate_liquidity_score(
            open_interest=None,
            bid_ask_spread_pct=None,
            volume=None,
        )
        assert 40.0 <= score <= 60.0  # Neutral range


class TestCompositeScoring:
    """Tests for composite scoring."""

    def test_excellent_opportunity(self, scorer):
        """Excellent opportunity should score high."""
        score = scorer.score_ticker(
            ticker="TEST",
            earnings_date=date(2024, 10, 1),
            vrp_ratio=2.0,
            consistency=0.8,
            skew=0.0,
            avg_historical_move=5.0,
            open_interest=1000,
            bid_ask_spread_pct=3.0,
            volume=500,
        )

        assert score.composite_score >= 90.0
        assert score.vrp_score == 100.0
        assert score.consistency_score == 100.0
        assert score.skew_score == 100.0
        assert score.liquidity_score == 100.0

    def test_marginal_opportunity(self, scorer):
        """Marginal opportunity should score mid-range."""
        score = scorer.score_ticker(
            ticker="TEST",
            earnings_date=date(2024, 10, 1),
            vrp_ratio=1.3,
            consistency=0.5,
            skew=0.2,
            avg_historical_move=3.0,
            open_interest=150,
            bid_ask_spread_pct=12.0,
            volume=60,
        )

        assert 50.0 <= score.composite_score < 70.0

    def test_poor_opportunity(self, scorer):
        """Poor opportunity should score low."""
        score = scorer.score_ticker(
            ticker="TEST",
            earnings_date=date(2024, 10, 1),
            vrp_ratio=0.8,
            consistency=0.2,
            skew=0.5,
            avg_historical_move=2.0,
            open_interest=30,
            bid_ask_spread_pct=18.0,
            volume=20,
        )

        assert score.composite_score < 50.0

    def test_weight_application(self):
        """Test that weights are correctly applied."""
        # Config with 100% VRP weight
        vrp_only_config = ScoringConfig(
            name="VRP-Only",
            description="Test",
            weights=ScoringWeights(
                vrp_weight=1.0,
                consistency_weight=0.0,
                skew_weight=0.0,
                liquidity_weight=0.0,
            ),
            thresholds=ScoringThresholds(),
            max_positions=10,
            min_score=50.0,
        )

        scorer = TickerScorer(vrp_only_config)
        score = scorer.score_ticker(
            ticker="TEST",
            earnings_date=date(2024, 10, 1),
            vrp_ratio=2.0,  # 100 points
            consistency=0.2,  # Would be low score
            skew=0.5,  # Would be low score
            avg_historical_move=3.0,
            open_interest=30,  # Would be low score
            bid_ask_spread_pct=18.0,
            volume=20,
        )

        # Should equal VRP score since weight is 100%
        assert score.composite_score == score.vrp_score
        assert score.composite_score == 100.0


class TestRankingAndSelection:
    """Tests for ranking and selection logic."""

    def test_ranking_order(self, scorer):
        """Scores should be ranked by composite score."""
        scores = [
            TickerScore("A", date(2024, 10, 1), 80, 70, 60, 50, 70.0),
            TickerScore("B", date(2024, 10, 2), 90, 80, 70, 60, 85.0),
            TickerScore("C", date(2024, 10, 3), 70, 60, 50, 40, 60.0),
        ]

        ranked = scorer.rank_and_select(scores)

        assert ranked[0].ticker == "B"  # Highest composite
        assert ranked[1].ticker == "A"
        assert ranked[2].ticker == "C"  # Lowest composite

        assert ranked[0].rank == 1
        assert ranked[1].rank == 2
        assert ranked[2].rank == 3

    def test_selection_limit(self, scorer):
        """Only top N should be selected."""
        scores = [
            TickerScore("A", date(2024, 10, 1), 80, 70, 60, 50, 80.0),
            TickerScore("B", date(2024, 10, 2), 90, 80, 70, 60, 85.0),
            TickerScore("C", date(2024, 10, 3), 70, 60, 50, 40, 75.0),
        ]

        # Config with max 2 positions
        config = ScoringConfig(
            name="Test",
            description="Test",
            weights=ScoringWeights(0.4, 0.3, 0.15, 0.15),
            thresholds=ScoringThresholds(),
            max_positions=2,
            min_score=60.0,
        )
        scorer = TickerScorer(config)

        ranked = scorer.rank_and_select(scores)

        assert ranked[0].selected is True
        assert ranked[1].selected is True
        assert ranked[2].selected is False

    def test_minimum_score_filter(self, scorer):
        """Scores below minimum should not be qualified."""
        scores = [
            TickerScore("A", date(2024, 10, 1), 80, 70, 60, 50, 70.0),
            TickerScore("B", date(2024, 10, 2), 50, 40, 30, 20, 45.0),  # Below 60
            TickerScore("C", date(2024, 10, 3), 70, 60, 50, 40, 62.0),
        ]

        ranked = scorer.rank_and_select(scores)

        # Only A and C should be qualified
        assert len(ranked) == 2
        assert all(s.composite_score >= 60.0 for s in ranked)
