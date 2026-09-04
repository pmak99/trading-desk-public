"""
Tests for enhanced consistency analyzer (Phase 4).
"""

import pytest
from datetime import date, timedelta

from src.application.metrics.consistency_enhanced import (
    ConsistencyAnalyzerEnhanced,
    ConsistencyAnalysis
)
from src.domain.types import Money, Percentage, HistoricalMove
from src.domain.errors import ErrorCode


class TestConsistencyAnalyzerEnhanced:
    """Test exponential-weighted consistency analysis."""

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

    def test_exponential_weighting(self, analyzer):
        """Test that recent quarters are weighted more heavily."""
        # Create moves where recent quarters are higher
        moves = []
        base_date = date(2026, 3, 16)

        # Recent quarters: 8% moves (close_move_pct is the default metric)
        for i in range(4):
            move = HistoricalMove(
                ticker="TEST",
                earnings_date=base_date - timedelta(days=90 * i),
                prev_close=Money(100.0),
                earnings_open=Money(102.0),
                earnings_high=Money(108.0),
                earnings_low=Money(92.0),
                earnings_close=Money(108.0),  # 8% move
                intraday_move_pct=Percentage(8.0),
                gap_move_pct=Percentage(2.0),
                close_move_pct=Percentage(8.0)  # 8% - default metric
            )
            moves.append(move)

        # Older quarters: 4% moves
        for i in range(4, 8):
            move = HistoricalMove(
                ticker="TEST",
                earnings_date=base_date - timedelta(days=90 * i),
                prev_close=Money(100.0),
                earnings_open=Money(102.0),
                earnings_high=Money(104.0),
                earnings_low=Money(96.0),
                earnings_close=Money(104.0),  # 4% move
                intraday_move_pct=Percentage(4.0),
                gap_move_pct=Percentage(2.0),
                close_move_pct=Percentage(4.0)  # 4% - default metric
            )
            moves.append(move)

        result = analyzer.analyze_consistency("TEST", moves)

        assert result.is_ok
        analysis = result.value

        # Weighted mean should be closer to 8% (recent) than 6% (simple average)
        assert analysis.mean_move.value > 6.0
        assert analysis.mean_move.value < 8.0

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
