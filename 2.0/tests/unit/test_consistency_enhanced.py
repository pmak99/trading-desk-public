"""
Tests for enhanced consistency analyzer (Phase 4).
"""

import statistics
from datetime import date, timedelta

import pytest

from src.application.metrics.consistency_enhanced import (
    ConsistencyAnalysis,
    ConsistencyAnalyzerEnhanced,
)
from src.domain.errors import ErrorCode
from src.domain.types import HistoricalMove, Money, Percentage


class TestConsistencyAnalyzerEnhanced:
    """Test consistency analysis: one equal-weighted convention, trend, trust."""

    @pytest.fixture
    def analyzer(self):
        """Create consistency analyzer."""
        return ConsistencyAnalyzerEnhanced()

    def create_historical_moves(
        self,
        ticker="TEST",
        num_quarters=8,
        pattern="stable"  # stable, increasing, decreasing, volatile
    ):
        """
        Create historical moves with specific pattern.

        Args:
            ticker: Stock ticker
            num_quarters: Number of quarters to generate
            pattern: Pattern type (stable, increasing, decreasing, volatile)

        Returns:
            List of HistoricalMove (newest first)

        Note: The ConsistencyAnalyzerEnhanced defaults to using close_move_pct,
        so we set that field according to the pattern.
        """
        moves = []
        base_date = date(2026, 3, 16)

        for i in range(num_quarters):
            earnings_date = base_date - timedelta(days=90 * i)

            if pattern == "stable":
                # Consistent ~5% moves
                move_pct = 5.0 + (i % 3 - 1) * 0.5  # 4.5%, 5.0%, 5.5%

            elif pattern == "increasing":
                # Increasing moves over time (bad for strategy)
                # i=0 is newest, so newest should be highest
                move_pct = 10.0 - i * 1.0  # Newest:10%, ..., Oldest:3% → trend increasing

            elif pattern == "decreasing":
                # Decreasing moves over time (good for strategy)
                # i=0 is newest, so newest should be lowest
                move_pct = 3.0 + i * 1.0  # Newest:3%, ..., Oldest:10% → trend decreasing

            else:  # volatile
                # Volatile moves
                move_pct = 5.0 + ((-1) ** i) * 3.0  # 2%, 8%, 2%, 8%, ...

            move = HistoricalMove(
                ticker=ticker,
                earnings_date=earnings_date,
                prev_close=Money(100.0),
                earnings_open=Money(102.0),
                earnings_high=Money(105.0),
                earnings_low=Money(98.0),
                earnings_close=Money(100.0 + move_pct),  # Set earnings_close for consistency
                intraday_move_pct=Percentage(move_pct),  # Keep for intraday metric
                gap_move_pct=Percentage(2.0),
                close_move_pct=Percentage(move_pct)  # Set to match pattern (default metric)
            )
            moves.append(move)

        return moves

    def test_stable_pattern_detection(self, analyzer):
        """Test detection of stable move pattern."""
        moves = self.create_historical_moves(pattern="stable")

        result = analyzer.analyze_consistency("TEST", moves)

        assert result.is_ok
        analysis = result.value

        assert isinstance(analysis, ConsistencyAnalysis)
        assert analysis.ticker == "TEST"
        assert analysis.num_quarters == len(moves)
        assert analysis.trend == "stable"
        assert analysis.consistency_score > 50  # Should be fairly consistent
        assert analysis.trustworthiness > 0.5  # Should be trustworthy

    def test_increasing_trend_detection(self, analyzer):
        """Test detection of increasing volatility trend (bad signal)."""
        moves = self.create_historical_moves(pattern="increasing")

        result = analyzer.analyze_consistency("TEST", moves)

        assert result.is_ok
        analysis = result.value

        assert analysis.trend == "increasing"
        assert analysis.trend_slope > analyzer.STABLE_THRESHOLD
        # Trustworthiness should be penalized for increasing trend
        assert analysis.trustworthiness < 1.0

    def test_decreasing_trend_detection(self, analyzer):
        """Test detection of decreasing volatility trend (good signal)."""
        moves = self.create_historical_moves(pattern="decreasing")

        result = analyzer.analyze_consistency("TEST", moves)

        assert result.is_ok
        analysis = result.value

        assert analysis.trend == "decreasing"
        assert analysis.trend_slope < -analyzer.STABLE_THRESHOLD

    def test_volatile_pattern_low_consistency(self, analyzer):
        """Test that volatile pattern results in low consistency score."""
        moves = self.create_historical_moves(pattern="volatile")

        result = analyzer.analyze_consistency("TEST", moves)

        assert result.is_ok
        analysis = result.value

        # Volatile pattern should have lower consistency
        assert analysis.consistency_score < 80
        assert analysis.std_dev.value > 1.0  # Higher std dev

    def test_insufficient_data_error(self, analyzer):
        """Test error when insufficient historical data."""
        moves = self.create_historical_moves(num_quarters=2)

        result = analyzer.analyze_consistency("TEST", moves)

        assert result.is_err
        assert result.error.code == ErrorCode.NODATA

    def moves_from(self, pcts):
        """HistoricalMoves, newest first, whose close_move_pct is each value."""
        base_date = date(2026, 3, 16)
        return [
            HistoricalMove(
                ticker="TEST",
                earnings_date=base_date - timedelta(days=90 * i),
                prev_close=Money(100.0),
                earnings_open=Money(100.0),
                earnings_high=Money(100.0 + abs(pct)),
                earnings_low=Money(100.0 - abs(pct)),
                earnings_close=Money(100.0 + pct),
                intraday_move_pct=Percentage(pct),
                gap_move_pct=Percentage(0.0),
                close_move_pct=Percentage(pct),
            )
            for i, pct in enumerate(pcts)
        ]

    def test_hand_computed_example(self, analyzer):
        """Moves 2, 4, 6, 8 (newest first), worked by hand.

        mean      = 20 / 4 = 5.0
        sample SD = sqrt(((-3)^2 + (-1)^2 + 1^2 + 3^2) / 3) = sqrt(20/3) = 2.581989
        score     = 100 * (1 - 2.581989 / 5) = 48.360222
        trust     = 0.48360222 * 4/(4+4) * 1.0 (slope -2 a quarter, decreasing) = 0.241801
        recent bias: the recent four are all four moves, so 0

        The old convention -- mean weighted 0.85^i, SD unweighted about that
        weighted mean -- scored the same moves 42.92 on a mean of 4.5967.
        """
        result = analyzer.analyze_consistency("TEST", self.moves_from([2.0, 4.0, 6.0, 8.0]))

        assert result.is_ok
        a = result.value
        assert a.mean_move.value == pytest.approx(5.0)
        assert a.std_dev.value == pytest.approx(2.581989, abs=1e-6)
        assert a.consistency_score == pytest.approx(48.360222, abs=1e-6)
        assert a.trend == "decreasing"
        assert a.trustworthiness == pytest.approx(0.241801, abs=1e-6)
        assert a.recent_bias == pytest.approx(0.0)

    def test_one_weighting_convention(self, analyzer):
        """Mean and SD are both equal-weighted, as the VRP denominator is.

        Four recent 8% moves over four older 4% moves: the mean is 6.0 -- what
        VRPCalculator divides by for the same history -- and the SD is the plain
        sample SD about it, sqrt(8 * 2^2 / 7) = 2.138090. The recency-weighted mean
        this replaced read 6.63 here, and the SD taken about it counted the
        distance between the two means as dispersion.
        """
        pcts = [8.0] * 4 + [4.0] * 4
        result = analyzer.analyze_consistency("TEST", self.moves_from(pcts))

        assert result.is_ok
        a = result.value
        assert a.mean_move.value == pytest.approx(statistics.mean(pcts))
        assert a.std_dev.value == pytest.approx(statistics.stdev(pcts))
        assert a.consistency_score == pytest.approx(100 * (1 - statistics.stdev(pcts) / 6.0))
        # the recent four average 8.0, against the same 6.0 mean
        assert a.recent_bias == pytest.approx((8.0 - 6.0) / 6.0 * 100)

    def test_signed_moves_count_by_magnitude(self, analyzer):
        """-2, 4, -6, 8 is the 2, 4, 6, 8 example: direction is not dispersion."""
        signed = analyzer.analyze_consistency("TEST", self.moves_from([-2.0, 4.0, -6.0, 8.0]))

        assert signed.is_ok
        assert signed.value.mean_move.value == pytest.approx(5.0)
        assert signed.value.consistency_score == pytest.approx(48.360222, abs=1e-6)

    def test_recent_bias_calculation(self, analyzer):
        """Test recent bias detection."""
        moves = self.create_historical_moves(pattern="increasing", num_quarters=8)

        result = analyzer.analyze_consistency("TEST", moves)

        assert result.is_ok
        analysis = result.value

        # Recent moves should be higher than overall mean
        assert analysis.recent_bias != 0.0

    def test_trustworthiness_increases_with_sample_size(self, analyzer):
        """Test that trustworthiness increases with more data."""
        moves_small = self.create_historical_moves(pattern="stable", num_quarters=4)
        moves_large = self.create_historical_moves(pattern="stable", num_quarters=12)

        result_small = analyzer.analyze_consistency("TEST", moves_small)
        result_large = analyzer.analyze_consistency("TEST", moves_large)

        assert result_small.is_ok
        assert result_large.is_ok

        # More data should increase trustworthiness (all else equal)
        assert result_large.value.trustworthiness >= result_small.value.trustworthiness

    def test_consistency_score_range(self, analyzer):
        """Test that consistency score is in valid range."""
        moves = self.create_historical_moves(pattern="stable")

        result = analyzer.analyze_consistency("TEST", moves)

        assert result.is_ok
        analysis = result.value

        assert 0.0 <= analysis.consistency_score <= 100.0

    def test_trustworthiness_range(self, analyzer):
        """Test that trustworthiness is in valid range."""
        moves = self.create_historical_moves(pattern="stable")

        result = analyzer.analyze_consistency("TEST", moves)

        assert result.is_ok
        analysis = result.value

        assert 0.0 <= analysis.trustworthiness <= 1.0

    def test_mean_and_std_dev_calculated(self, analyzer):
        """Test that mean and std dev are calculated."""
        moves = self.create_historical_moves(pattern="stable")

        result = analyzer.analyze_consistency("TEST", moves)

        assert result.is_ok
        analysis = result.value

        assert analysis.mean_move is not None
        assert isinstance(analysis.mean_move, Percentage)
        assert analysis.std_dev is not None
        assert isinstance(analysis.std_dev, Percentage)
        assert analysis.std_dev.value >= 0.0
