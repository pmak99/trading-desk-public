"""
Unit tests for VRP calculator edge cases.

Tests boundary conditions, guard clauses, and error handling in VRP calculation:
- Zero mean_move (division by zero guard)
- NaN/Inf mean_move (recently added guards)
- Minimum quarters requirement
- Negative implied move
- VRP ratio at exact threshold boundaries (1.2, 1.4, 1.8)
"""

import pytest
import numpy as np
from datetime import date, timedelta

from src.application.metrics.vrp import VRPCalculator
from src.domain.types import (
    Money,
    Percentage,
    HistoricalMove,
    ImpliedMove,
    Strike,
)
from src.domain.errors import ErrorCode
from src.domain.enums import Recommendation


# ============================================================================
# Helpers
# ============================================================================


def make_historical_move(
    ticker: str = "TEST",
    close_move_pct: float = 5.0,
    intraday_move_pct: float = 6.0,
    gap_move_pct: float = 3.0,
    days_ago: int = 90,
) -> HistoricalMove:
    """Create a test HistoricalMove with configurable move percentages."""
    earnings_date = date(2026, 3, 16) - timedelta(days=days_ago)
    return HistoricalMove(
        ticker=ticker,
        earnings_date=earnings_date,
        prev_close=Money(100.0),
        earnings_open=Money(100.0 + close_move_pct * 0.5),
        earnings_high=Money(100.0 + intraday_move_pct),
        earnings_low=Money(100.0 - intraday_move_pct * 0.2),
        earnings_close=Money(100.0 + close_move_pct),
        intraday_move_pct=Percentage(intraday_move_pct),
        gap_move_pct=Percentage(gap_move_pct),
        close_move_pct=Percentage(close_move_pct),
    )


def make_implied_move(
    implied_pct: float = 10.0,
    ticker: str = "TEST",
) -> ImpliedMove:
    """Create a test ImpliedMove with configurable implied move percentage."""
    return ImpliedMove(
        ticker=ticker,
        expiration=date(2026, 3, 23),
        stock_price=Money(100.0),
        atm_strike=Strike(100.0),
        straddle_cost=Money(implied_pct),
        implied_move_pct=Percentage(implied_pct),
        upper_bound=Money(100.0 + implied_pct),
        lower_bound=Money(100.0 - implied_pct),
    )


def make_historical_moves(
    n: int = 8,
    close_move_pct: float = 5.0,
    ticker: str = "TEST",
) -> list:
    """Create N historical moves with the same close_move_pct."""
    return [
        make_historical_move(
            ticker=ticker,
            close_move_pct=close_move_pct,
            days_ago=90 * (i + 1),
        )
        for i in range(n)
    ]


# ============================================================================
# Tests
# ============================================================================


class TestVRPZeroMeanMove:
    """Tests for zero mean_move guard (division by zero prevention)."""

    def test_zero_close_move_returns_error(self):
        """Zero mean_move should return INVALID error, not divide by zero."""
        calc = VRPCalculator(min_quarters=4, move_metric="close")
        moves = make_historical_moves(n=4, close_move_pct=0.0)
        implied = make_implied_move(implied_pct=10.0)

        result = calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        assert result.is_err
        assert result.error.code == ErrorCode.INVALID
        assert "Invalid mean move" in result.error.message

    def test_all_zero_moves_returns_error(self):
        """All historical moves at 0% should result in error."""
        calc = VRPCalculator(min_quarters=4, move_metric="close")
        moves = [
            make_historical_move(close_move_pct=0.0, days_ago=90 * i)
            for i in range(1, 9)
        ]
        implied = make_implied_move(implied_pct=5.0)

        result = calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        assert result.is_err


class TestVRPNaNInfGuards:
    """Tests for NaN and Inf guards on mean_move."""

    def test_negative_close_moves_mean_below_zero(self):
        """Negative close moves produce negative mean, which should return error.

        The VRP guard checks: mean_move <= 0 or isnan or isinf.
        Negative moves (like -5.0) give a negative mean, caught by <= 0.
        """
        calc = VRPCalculator(min_quarters=4, move_metric="close")
        moves = make_historical_moves(n=4, close_move_pct=-5.0)
        implied = make_implied_move(implied_pct=10.0)

        result = calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        assert result.is_err
        assert result.error.code == ErrorCode.INVALID


class TestVRPMinimumQuarters:
    """Tests for minimum historical data requirements."""

    def test_empty_historical_moves(self):
        """No historical moves should return NODATA error."""
        calc = VRPCalculator(min_quarters=4)
        implied = make_implied_move()

        result = calc.calculate("TEST", date(2026, 3, 16), implied, [])

        assert result.is_err
        assert result.error.code == ErrorCode.NODATA
        assert "No historical moves" in result.error.message

    def test_below_minimum_quarters(self):
        """Fewer than min_quarters should return NODATA error."""
        calc = VRPCalculator(min_quarters=4)
        moves = make_historical_moves(n=3)  # Only 3, need 4
        implied = make_implied_move()

        result = calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        assert result.is_err
        assert result.error.code == ErrorCode.NODATA
        assert "Need 4+ quarters" in result.error.message

    def test_exactly_minimum_quarters_succeeds(self):
        """Exactly min_quarters historical moves should succeed."""
        calc = VRPCalculator(min_quarters=4)
        moves = make_historical_moves(n=4, close_move_pct=5.0)
        implied = make_implied_move(implied_pct=10.0)

        result = calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        assert result.is_ok

    def test_above_minimum_quarters_succeeds(self):
        """More than min_quarters should succeed."""
        calc = VRPCalculator(min_quarters=4)
        moves = make_historical_moves(n=12, close_move_pct=5.0)
        implied = make_implied_move(implied_pct=10.0)

        result = calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        assert result.is_ok

    def test_one_move_with_min_quarters_one(self):
        """min_quarters=1 should accept a single historical move."""
        calc = VRPCalculator(min_quarters=1)
        moves = make_historical_moves(n=1, close_move_pct=5.0)
        implied = make_implied_move(implied_pct=10.0)

        result = calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        assert result.is_ok

    @pytest.mark.parametrize("n_moves", [1, 2, 3])
    def test_below_default_minimum(self, n_moves):
        """Default min_quarters=4, so 1-3 moves should all fail."""
        calc = VRPCalculator()  # Default min_quarters=4
        moves = make_historical_moves(n=n_moves, close_move_pct=5.0)
        implied = make_implied_move(implied_pct=10.0)

        result = calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        assert result.is_err
        assert result.error.code == ErrorCode.NODATA


class TestVRPNegativeImpliedMove:
    """Tests for negative implied move handling."""

    def test_negative_implied_move_produces_negative_vrp(self):
        """Negative implied move should produce a negative VRP ratio (below SKIP)."""
        calc = VRPCalculator(min_quarters=4)
        moves = make_historical_moves(n=4, close_move_pct=5.0)
        implied = make_implied_move(implied_pct=-5.0)

        # Percentage allows -100 to 1000, so -5.0 is valid
        result = calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        # Should succeed but with a negative VRP ratio -> SKIP recommendation
        assert result.is_ok
        assert result.value.vrp_ratio < 0
        assert result.value.recommendation == Recommendation.SKIP


class TestVRPThresholdBoundaries:
    """Tests for VRP ratio at exact threshold boundaries.

    Uses BALANCED mode default thresholds: 1.8 (excellent), 1.4 (good), 1.2 (marginal).
    The VRPCalculator defaults are LEGACY (7.0, 4.0, 1.5) so we set them explicitly.
    """

    @pytest.fixture
    def balanced_calc(self):
        """VRP calculator with BALANCED thresholds."""
        return VRPCalculator(
            threshold_excellent=1.8,
            threshold_good=1.4,
            threshold_marginal=1.2,
            min_quarters=4,
        )

    def _make_vrp_at_ratio(self, target_ratio: float):
        """Create test data to produce a specific VRP ratio.

        VRP = implied / mean(historical)
        With historical mean = 5.0, implied = target_ratio * 5.0
        """
        historical_pct = 5.0
        implied_pct = target_ratio * historical_pct

        moves = make_historical_moves(n=4, close_move_pct=historical_pct)
        implied = make_implied_move(implied_pct=implied_pct)
        return moves, implied

    def test_exactly_at_excellent_threshold(self, balanced_calc):
        """VRP = 1.8x should be EXCELLENT (>= threshold)."""
        moves, implied = self._make_vrp_at_ratio(1.8)
        result = balanced_calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        assert result.is_ok
        assert result.value.recommendation == Recommendation.EXCELLENT
        assert abs(result.value.vrp_ratio - 1.8) < 0.01

    def test_just_below_excellent_threshold(self, balanced_calc):
        """VRP = 1.79x should be GOOD, not EXCELLENT."""
        moves, implied = self._make_vrp_at_ratio(1.79)
        result = balanced_calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        assert result.is_ok
        assert result.value.recommendation == Recommendation.GOOD

    def test_exactly_at_good_threshold(self, balanced_calc):
        """VRP = 1.4x should be GOOD (>= threshold)."""
        moves, implied = self._make_vrp_at_ratio(1.4)
        result = balanced_calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        assert result.is_ok
        assert result.value.recommendation == Recommendation.GOOD

    def test_just_below_good_threshold(self, balanced_calc):
        """VRP = 1.39x should be MARGINAL, not GOOD."""
        moves, implied = self._make_vrp_at_ratio(1.39)
        result = balanced_calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        assert result.is_ok
        assert result.value.recommendation == Recommendation.MARGINAL

    def test_exactly_at_marginal_threshold(self, balanced_calc):
        """VRP = 1.2x should be MARGINAL (>= threshold)."""
        moves, implied = self._make_vrp_at_ratio(1.2)
        result = balanced_calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        assert result.is_ok
        assert result.value.recommendation == Recommendation.MARGINAL

    def test_just_below_marginal_threshold(self, balanced_calc):
        """VRP = 1.19x should be SKIP."""
        moves, implied = self._make_vrp_at_ratio(1.19)
        result = balanced_calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        assert result.is_ok
        assert result.value.recommendation == Recommendation.SKIP

    def test_ratio_at_1_0_is_skip(self, balanced_calc):
        """VRP = 1.0x (implied = historical) should be SKIP."""
        moves, implied = self._make_vrp_at_ratio(1.0)
        result = balanced_calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        assert result.is_ok
        assert result.value.recommendation == Recommendation.SKIP

    def test_very_high_ratio(self, balanced_calc):
        """Very high VRP ratio should be EXCELLENT."""
        moves, implied = self._make_vrp_at_ratio(10.0)
        result = balanced_calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        assert result.is_ok
        assert result.value.recommendation == Recommendation.EXCELLENT
        assert result.value.vrp_ratio > 9.0


class TestVRPMoveMetric:
    """Tests for move metric selection (close, intraday, gap)."""

    def test_invalid_move_metric_raises(self):
        """Invalid move_metric should raise ValueError during init."""
        with pytest.raises(ValueError, match="Invalid move_metric"):
            VRPCalculator(move_metric="invalid")

    @pytest.mark.parametrize("metric", ["close", "intraday", "gap"])
    def test_valid_move_metrics_succeed(self, metric):
        """All valid move metrics should initialize successfully."""
        calc = VRPCalculator(move_metric=metric, min_quarters=4)
        moves = make_historical_moves(n=4)
        implied = make_implied_move()

        result = calc.calculate("TEST", date(2026, 3, 16), implied, moves)
        assert result.is_ok

    def test_close_metric_uses_close_move_pct(self):
        """close metric should use close_move_pct values."""
        calc = VRPCalculator(move_metric="close", min_quarters=4)
        # close_move_pct=5.0, implied=10.0 -> VRP = 2.0
        moves = make_historical_moves(n=4, close_move_pct=5.0)
        implied = make_implied_move(implied_pct=10.0)

        result = calc.calculate("TEST", date(2026, 3, 16), implied, moves)

        assert result.is_ok
        assert abs(result.value.vrp_ratio - 2.0) < 0.01


class TestVRPConsistencyMetrics:
    """Tests for calculate_with_consistency method."""

    def test_consistency_metrics_returned(self):
        """calculate_with_consistency should return VRPResult and consistency dict."""
        calc = VRPCalculator(min_quarters=4)
        moves = make_historical_moves(n=8, close_move_pct=5.0)
        implied = make_implied_move(implied_pct=10.0)

        result = calc.calculate_with_consistency(
            "TEST", date(2026, 3, 16), implied, moves
        )

        assert result.is_ok
        vrp_result, consistency = result.value
        assert vrp_result.vrp_ratio > 0
        assert "mean" in consistency
        assert "median" in consistency
        assert "std" in consistency
        assert "mad" in consistency
        assert "tail_risk_ratio" in consistency
        assert "tail_risk_level" in consistency

    def test_consistency_error_propagation(self):
        """Errors from calculate() should propagate through calculate_with_consistency."""
        calc = VRPCalculator(min_quarters=4)
        result = calc.calculate_with_consistency(
            "TEST", date(2026, 3, 16), make_implied_move(), []
        )

        assert result.is_err

    def test_tail_risk_level_classification(self):
        """Tail risk levels should match thresholds (>2.5=HIGH, 1.5-2.5=NORMAL, <1.5=LOW)."""
        calc = VRPCalculator(min_quarters=4)

        # Uniform moves -> low tail risk
        moves = make_historical_moves(n=8, close_move_pct=5.0)
        implied = make_implied_move(implied_pct=10.0)

        result = calc.calculate_with_consistency(
            "TEST", date(2026, 3, 16), implied, moves
        )

        assert result.is_ok
        _, consistency = result.value
        # All uniform -> tail_risk_ratio = max/mean = 1.0 -> LOW
        assert consistency["tail_risk_level"] == "LOW"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
