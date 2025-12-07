"""
Test suite for TickerScorer.

Tests composite scoring logic with new VRP thresholds and ensures
proper ranking and selection of tickers.
"""

import pytest
from datetime import date
from src.application.services.scorer import TickerScorer, TickerScore
from src.config.scoring_config import (
    ScoringConfig,
    ScoringWeights,
    ScoringThresholds,
    get_config,
)


class TestVRPScoring:
    """Test VRP score calculation with new thresholds."""

    @pytest.fixture
    def scorer(self):
        """Create scorer with default thresholds (2.0/1.5/1.2)."""
        config = get_config("balanced")
        return TickerScorer(config)

    def test_vrp_excellent_100_points(self, scorer):
        """VRP >= 2.0x gets 100 points."""
        score = scorer.calculate_vrp_score(2.0)
        assert score == 100.0

        score = scorer.calculate_vrp_score(2.5)
        assert score == 100.0

    def test_vrp_good_75_points(self, scorer):
        """VRP = 1.5x gets 75 points."""
        score = scorer.calculate_vrp_score(1.5)
        assert score == 75.0

    def test_vrp_between_good_and_excellent(self, scorer):
        """VRP between 1.5x and 2.0x interpolates between 75-100."""
        score = scorer.calculate_vrp_score(1.75)  # Midpoint
        assert 75.0 < score < 100.0
        assert abs(score - 87.5) < 0.1  # Should be close to midpoint

    def test_vrp_marginal_50_points(self, scorer):
        """VRP = 1.2x gets 50 points."""
        score = scorer.calculate_vrp_score(1.2)
        assert score == 50.0

    def test_vrp_between_marginal_and_good(self, scorer):
        """VRP between 1.2x and 1.5x interpolates between 50-75."""
        score = scorer.calculate_vrp_score(1.35)  # Midpoint
        assert 50.0 < score < 75.0
        assert abs(score - 62.5) < 0.1

    def test_vrp_between_one_and_marginal(self, scorer):
        """VRP between 1.0x and 1.2x interpolates between 0-50."""
        score = scorer.calculate_vrp_score(1.1)  # Midpoint
        assert 0.0 < score < 50.0
        assert abs(score - 25.0) < 0.1

    def test_vrp_exactly_one_gets_zero(self, scorer):
        """VRP = 1.0x gets 0 points (no edge)."""
        score = scorer.calculate_vrp_score(1.0)
        assert score == 0.0

    def test_vrp_below_one_gets_zero(self, scorer):
        """VRP < 1.0x gets 0 points (negative edge)."""
        score = scorer.calculate_vrp_score(0.9)
        assert score == 0.0

        score = scorer.calculate_vrp_score(0.5)
        assert score == 0.0

    def test_vrp_none_gets_zero(self, scorer):
        """VRP = None gets 0 points."""
        score = scorer.calculate_vrp_score(None)
        assert score == 0.0

    def test_vrp_negative_gets_zero(self, scorer):
        """VRP < 0 gets 0 points."""
        score = scorer.calculate_vrp_score(-0.5)
        assert score == 0.0

    def test_vrp_aggressive_thresholds(self):
        """Aggressive config uses lower thresholds (1.5/1.3/1.1)."""
        config = get_config("aggressive")
        scorer = TickerScorer(config)

        # 1.5x is excellent for aggressive
        score = scorer.calculate_vrp_score(1.5)
        assert score == 100.0

        # 1.3x is good
        score = scorer.calculate_vrp_score(1.3)
        assert score == 75.0

        # 1.1x is marginal
        score = scorer.calculate_vrp_score(1.1)
        assert score == 50.0

    def test_vrp_conservative_thresholds(self):
        """Conservative config uses higher thresholds (2.5/1.8/1.4)."""
        config = get_config("conservative")
        scorer = TickerScorer(config)

        # 2.5x is excellent for conservative
        score = scorer.calculate_vrp_score(2.5)
        assert score == 100.0

        # 1.8x is good
        score = scorer.calculate_vrp_score(1.8)
        assert score == 75.0

        # 1.4x is marginal
        score = scorer.calculate_vrp_score(1.4)
        assert score == 50.0

        # 2.0x (default excellent) is between good and excellent for conservative
        score = scorer.calculate_vrp_score(2.0)
        assert 75.0 < score < 100.0


class TestConsistencyScoring:
    """Test consistency score calculation."""

    @pytest.fixture
    def scorer(self):
        """Create scorer with default config."""
        config = get_config("balanced")
        return TickerScorer(config)

    def test_consistency_excellent_100_points(self, scorer):
        """Consistency >= 0.8 gets 100 points."""
        score = scorer.calculate_consistency_score(0.8)
        assert score == 100.0

        score = scorer.calculate_consistency_score(0.95)
        assert score == 100.0

    def test_consistency_good_75_points(self, scorer):
        """Consistency = 0.6 gets 75 points."""
        score = scorer.calculate_consistency_score(0.6)
        assert score == 75.0

    def test_consistency_marginal_50_points(self, scorer):
        """Consistency = 0.4 gets 50 points."""
        score = scorer.calculate_consistency_score(0.4)
        assert score == 50.0

    def test_consistency_below_marginal_zero(self, scorer):
        """Consistency < 0.4 gets 0 points."""
        score = scorer.calculate_consistency_score(0.3)
        assert score == 0.0

    def test_consistency_none_gets_zero(self, scorer):
        """Consistency = None gets 0 points."""
        score = scorer.calculate_consistency_score(None)
        assert score == 0.0


class TestSkewScoring:
    """Test skew score calculation."""

    @pytest.fixture
    def scorer(self):
        """Create scorer with default config."""
        config = get_config("balanced")
        return TickerScorer(config)

    def test_skew_neutral_100_points(self, scorer):
        """Neutral skew (|skew| <= 0.15) gets 100 points."""
        score = scorer.calculate_skew_score(0.0)
        assert score == 100.0

        score = scorer.calculate_skew_score(0.10)
        assert score == 100.0

        score = scorer.calculate_skew_score(-0.10)
        assert score == 100.0

    def test_skew_moderate_70_points(self, scorer):
        """Moderate skew (0.15 < |skew| <= 0.35) gets ~70 points."""
        score = scorer.calculate_skew_score(0.35)
        assert 65.0 <= score <= 75.0

        score = scorer.calculate_skew_score(-0.35)
        assert 65.0 <= score <= 75.0

    def test_skew_extreme_penalized(self, scorer):
        """Extreme skew (|skew| > 0.35) is penalized."""
        score = scorer.calculate_skew_score(0.50)
        assert 40.0 <= score < 70.0

        score = scorer.calculate_skew_score(-0.50)
        assert 40.0 <= score < 70.0

    def test_skew_none_neutral_assumption(self, scorer):
        """Skew = None assumes neutral (75 points)."""
        score = scorer.calculate_skew_score(None)
        assert score == 75.0


class TestLiquidityScoring:
    """Test liquidity score calculation."""

    @pytest.fixture
    def scorer(self):
        """Create scorer with default config."""
        config = get_config("balanced")
        return TickerScorer(config)

    def test_liquidity_excellent_100_points(self, scorer):
        """Excellent liquidity (OI >= 1000, spread <= 5%, vol >= 500) gets 100 points."""
        score = scorer.calculate_liquidity_score(
            open_interest=1000,
            bid_ask_spread_pct=5.0,
            volume=500,
        )
        assert score == 100.0

    def test_liquidity_good_75_points(self, scorer):
        """Good liquidity (OI >= 500, spread <= 10%, vol >= 100) gets ~75 points."""
        score = scorer.calculate_liquidity_score(
            open_interest=500,
            bid_ask_spread_pct=10.0,
            volume=100,
        )
        assert 70.0 <= score <= 80.0

    def test_liquidity_marginal_50_points(self, scorer):
        """Marginal liquidity (OI >= 100, spread <= 15%, vol >= 50) gets ~38 points.

        With 4-tier scoring:
        - OI 100: 2.5 pts (just above min, below warning tier)
        - Spread 15%: 5.0 pts (warning tier)
        - Volume 50: 2.0 pts (at min)
        Total: 9.5 * 4 = 38.0
        """
        score = scorer.calculate_liquidity_score(
            open_interest=100,
            bid_ask_spread_pct=15.0,
            volume=50,
        )
        assert 35.0 <= score <= 42.0

    def test_liquidity_poor_low_score(self, scorer):
        """Poor liquidity gets low score."""
        score = scorer.calculate_liquidity_score(
            open_interest=50,  # Below min
            bid_ask_spread_pct=20.0,  # Too wide
            volume=10,  # Too low
        )
        assert score < 30.0

    def test_liquidity_none_values_neutral(self, scorer):
        """None values default to neutral scores."""
        score = scorer.calculate_liquidity_score(
            open_interest=None,
            bid_ask_spread_pct=None,
            volume=None,
        )
        assert 45.0 <= score <= 55.0  # Neutral


class TestCompositeScoring:
    """Test composite score calculation."""

    @pytest.fixture
    def balanced_scorer(self):
        """Create balanced scorer (40% VRP, 25% consistency, 15% skew, 20% liquidity)."""
        return TickerScorer(get_config("balanced"))

    @pytest.fixture
    def vrp_dominant_scorer(self):
        """Create VRP-dominant scorer (70% VRP, 20% consistency, 5% skew, 5% liquidity)."""
        return TickerScorer(get_config("vrp_dominant"))

    def test_composite_score_perfect_ticker(self, balanced_scorer):
        """Perfect ticker gets 100 composite score."""
        score = balanced_scorer.score_ticker(
            ticker="AAPL",
            earnings_date=date(2024, 11, 1),
            vrp_ratio=2.5,  # Excellent
            consistency=0.9,  # Excellent
            skew=0.05,  # Neutral
            open_interest=2000,  # Excellent
            bid_ask_spread_pct=3.0,  # Excellent
            volume=1000,  # Excellent
        )

        assert score.vrp_score == 100.0
        assert score.consistency_score == 100.0
        assert score.skew_score == 100.0
        assert score.liquidity_score == 100.0
        assert score.composite_score == 100.0

    def test_composite_score_weighted_calculation(self, balanced_scorer):
        """Composite score is weighted sum of component scores."""
        score = balanced_scorer.score_ticker(
            ticker="AAPL",
            earnings_date=date(2024, 11, 1),
            vrp_ratio=2.0,  # 100 points
            consistency=0.8,  # 100 points
            skew=0.0,  # 100 points
            open_interest=1000,  # 10 points
            bid_ask_spread_pct=5.0,  # 10 points
            volume=500,  # 5 points -> Total liquidity: 100 points
        )

        # 40% * 100 + 25% * 100 + 15% * 100 + 20% * 100 = 100
        assert score.composite_score == 100.0

    def test_composite_score_vrp_dominant_weighting(self, vrp_dominant_scorer):
        """VRP-dominant config weighs VRP heavily (70%)."""
        score = vrp_dominant_scorer.score_ticker(
            ticker="AAPL",
            earnings_date=date(2024, 11, 1),
            vrp_ratio=2.5,  # 100 points
            consistency=0.3,  # 0 points
            skew=0.5,  # ~40 points
            open_interest=50,  # 0 points
        )

        # 70% * 100 + 20% * 0 + 5% * 40 + 5% * 0 = 72
        assert 70.0 <= score.composite_score <= 75.0

    def test_composite_score_stores_raw_metrics(self, balanced_scorer):
        """Composite score stores raw metrics for reference."""
        score = balanced_scorer.score_ticker(
            ticker="AAPL",
            earnings_date=date(2024, 11, 1),
            vrp_ratio=2.0,
            consistency=0.8,
            avg_historical_move=5.5,
        )

        assert score.vrp_ratio == 2.0
        assert score.consistency == 0.8
        assert score.avg_historical_move == 5.5


class TestRankingAndSelection:
    """Test ticker ranking and selection."""

    @pytest.fixture
    def balanced_scorer(self):
        """Create balanced scorer."""
        return TickerScorer(get_config("balanced"))

    def test_ranking_sorts_by_composite_score(self, balanced_scorer):
        """Tickers are ranked by descending composite score."""
        scores = [
            TickerScore(
                ticker="AAPL",
                earnings_date=date(2024, 11, 1),
                vrp_score=80.0,
                consistency_score=80.0,
                skew_score=80.0,
                liquidity_score=80.0,
                composite_score=80.0,
            ),
            TickerScore(
                ticker="GOOGL",
                earnings_date=date(2024, 11, 2),
                vrp_score=90.0,
                consistency_score=90.0,
                skew_score=90.0,
                liquidity_score=90.0,
                composite_score=90.0,
            ),
            TickerScore(
                ticker="MSFT",
                earnings_date=date(2024, 11, 3),
                vrp_score=70.0,
                consistency_score=70.0,
                skew_score=70.0,
                liquidity_score=70.0,
                composite_score=70.0,
            ),
        ]

        ranked = balanced_scorer.rank_and_select(scores)

        assert ranked[0].ticker == "GOOGL"
        assert ranked[0].rank == 1
        assert ranked[1].ticker == "AAPL"
        assert ranked[1].rank == 2
        assert ranked[2].ticker == "MSFT"
        assert ranked[2].rank == 3

    def test_selection_respects_max_positions(self, balanced_scorer):
        """Only top max_positions tickers are selected."""
        # Balanced config has max_positions=12
        scores = [
            TickerScore(
                ticker=f"TICK{i}",
                earnings_date=date(2024, 11, 1),
                vrp_score=100.0 - i,
                consistency_score=100.0 - i,
                skew_score=100.0 - i,
                liquidity_score=100.0 - i,
                composite_score=100.0 - i,
            )
            for i in range(15)
        ]

        ranked = balanced_scorer.rank_and_select(scores)

        selected = [s for s in ranked if s.selected]
        not_selected = [s for s in ranked if not s.selected]

        assert len(selected) == 12
        assert len(not_selected) == 3

    def test_filtering_by_min_score(self, balanced_scorer):
        """Tickers below min_score are filtered out."""
        # Balanced config has min_score=60.0
        scores = [
            TickerScore(
                ticker="GOOD1",
                earnings_date=date(2024, 11, 1),
                vrp_score=80.0,
                consistency_score=80.0,
                skew_score=80.0,
                liquidity_score=80.0,
                composite_score=80.0,
            ),
            TickerScore(
                ticker="BAD1",
                earnings_date=date(2024, 11, 2),
                vrp_score=50.0,
                consistency_score=50.0,
                skew_score=50.0,
                liquidity_score=50.0,
                composite_score=50.0,  # Below 60.0
            ),
            TickerScore(
                ticker="GOOD2",
                earnings_date=date(2024, 11, 3),
                vrp_score=70.0,
                consistency_score=70.0,
                skew_score=70.0,
                liquidity_score=70.0,
                composite_score=70.0,
            ),
        ]

        ranked = balanced_scorer.rank_and_select(scores)

        assert len(ranked) == 2
        assert all(s.composite_score >= 60.0 for s in ranked)
        assert "BAD1" not in [s.ticker for s in ranked]

    def test_empty_scores_returns_empty(self, balanced_scorer):
        """Empty scores list returns empty result."""
        ranked = balanced_scorer.rank_and_select([])
        assert len(ranked) == 0

    def test_all_below_threshold_returns_empty(self, balanced_scorer):
        """All scores below min_score returns empty."""
        scores = [
            TickerScore(
                ticker=f"TICK{i}",
                earnings_date=date(2024, 11, 1),
                vrp_score=40.0,
                consistency_score=40.0,
                skew_score=40.0,
                liquidity_score=40.0,
                composite_score=40.0,  # All below 60.0
            )
            for i in range(5)
        ]

        ranked = balanced_scorer.rank_and_select(scores)
        assert len(ranked) == 0
