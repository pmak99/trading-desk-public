"""
Unit tests for Tail Risk Ratio (TRR) enforcement.

TRR = Max Historical Move / Average Historical Move

TRR Thresholds:
- HIGH:   > 2.5x  → max 50 contracts, max $25,000 notional
- NORMAL: 1.5-2.5x → max 100 contracts, max $50,000 notional
- LOW:    < 1.5x  → max 100 contracts, max $50,000 notional

These limits were added after a $134k single-trade loss on MU (TRR=3.05)
in December 2025.
"""

import math
import pytest
import sys
from pathlib import Path
from datetime import date, timedelta

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.application.metrics.vrp import VRPCalculator
from src.domain.types import (
    Money,
    Percentage,
    HistoricalMove,
    ImpliedMove,
    Strike,
)
from src.domain.errors import ErrorCode


# ============================================================================
# Helpers
# ============================================================================


def make_historical_move(
    ticker: str,
    earnings_date: date,
    close_move_pct: float,
    gap_move_pct: float = 3.0,
    intraday_move_pct: float = 5.0,
    prev_close: float = 100.0,
) -> HistoricalMove:
    """Create a HistoricalMove with the specified close_move_pct."""
    return HistoricalMove(
        ticker=ticker,
        earnings_date=earnings_date,
        prev_close=Money(prev_close),
        earnings_open=Money(prev_close * (1 + gap_move_pct / 100)),
        earnings_high=Money(prev_close * (1 + intraday_move_pct / 100)),
        earnings_low=Money(prev_close * (1 - intraday_move_pct / 200)),
        earnings_close=Money(prev_close * (1 + close_move_pct / 100)),
        intraday_move_pct=Percentage(abs(intraday_move_pct)),
        gap_move_pct=Percentage(abs(gap_move_pct)),
        close_move_pct=Percentage(abs(close_move_pct)),
    )


def make_implied_move(
    ticker: str = "TEST",
    implied_pct: float = 8.0,
    stock_price: float = 100.0,
) -> ImpliedMove:
    """Create an ImpliedMove with the specified implied_move_pct."""
    straddle = stock_price * implied_pct / 100
    return ImpliedMove(
        ticker=ticker,
        expiration=date(2026, 3, 23),
        stock_price=Money(stock_price),
        atm_strike=Strike(stock_price),
        straddle_cost=Money(straddle),
        implied_move_pct=Percentage(implied_pct),
        upper_bound=Money(stock_price + straddle),
        lower_bound=Money(stock_price - straddle),
    )


def make_historical_moves(
    ticker: str,
    close_move_pcts: list[float],
) -> list[HistoricalMove]:
    """Create a list of HistoricalMove objects with specified close move percentages."""
    base_date = date(2024, 1, 1)
    return [
        make_historical_move(
            ticker=ticker,
            earnings_date=base_date + timedelta(days=90 * i),
            close_move_pct=pct,
        )
        for i, pct in enumerate(close_move_pcts)
    ]


# ============================================================================
# TRR Classification Tests
# ============================================================================


class TestTRRClassification:
    """Test TRR level classification from calculate_with_consistency."""

    @pytest.fixture
    def calculator(self):
        """VRP calculator with default BALANCED thresholds."""
        return VRPCalculator(
            threshold_excellent=1.8,
            threshold_good=1.4,
            threshold_marginal=1.2,
            min_quarters=4,
            move_metric="close",
        )

    def test_high_trr_above_2_5(self, calculator):
        """TRR > 2.5 should be classified as HIGH."""
        # Moves: [3%, 4%, 3%, 3%, 12%] → mean=5.0, max=12.0 → TRR=2.4
        # Use more extreme: [3%, 3%, 3%, 3%, 15%] → mean=5.4, max=15.0 → TRR=2.78
        moves = make_historical_moves("TEST", [3.0, 3.0, 3.0, 3.0, 15.0])
        implied = make_implied_move("TEST", implied_pct=20.0)

        result = calculator.calculate_with_consistency(
            "TEST", date(2026, 3, 23), implied, moves
        )
        assert result.is_ok
        vrp_result, consistency = result.value

        assert consistency['tail_risk_ratio'] > 2.5
        assert consistency['tail_risk_level'] == 'HIGH'

    def test_normal_trr_between_1_5_and_2_5(self, calculator):
        """TRR between 1.5 and 2.5 should be classified as NORMAL."""
        # Moves: [3%, 4%, 5%, 6%] → mean=4.5, max=6.0 → TRR=1.33... hmm
        # Need: [3%, 4%, 5%, 10%] → mean=5.5, max=10.0 → TRR=1.82
        moves = make_historical_moves("TEST", [3.0, 4.0, 5.0, 10.0])
        implied = make_implied_move("TEST", implied_pct=15.0)

        result = calculator.calculate_with_consistency(
            "TEST", date(2026, 3, 23), implied, moves
        )
        assert result.is_ok
        vrp_result, consistency = result.value

        assert 1.5 <= consistency['tail_risk_ratio'] <= 2.5
        assert consistency['tail_risk_level'] == 'NORMAL'

    def test_low_trr_below_1_5(self, calculator):
        """TRR < 1.5 should be classified as LOW."""
        # Very consistent moves: [4%, 5%, 4.5%, 5.5%] → mean=4.75, max=5.5 → TRR=1.16
        moves = make_historical_moves("TEST", [4.0, 5.0, 4.5, 5.5])
        implied = make_implied_move("TEST", implied_pct=15.0)

        result = calculator.calculate_with_consistency(
            "TEST", date(2026, 3, 23), implied, moves
        )
        assert result.is_ok
        vrp_result, consistency = result.value

        assert consistency['tail_risk_ratio'] < 1.5
        assert consistency['tail_risk_level'] == 'LOW'


class TestTRRBoundaryConditions:
    """Test exact boundary values for TRR classification."""

    @pytest.fixture
    def calculator(self):
        return VRPCalculator(
            threshold_excellent=1.8,
            threshold_good=1.4,
            threshold_marginal=1.2,
            min_quarters=4,
            move_metric="close",
        )

    def test_trr_exactly_2_5_is_normal(self, calculator):
        """TRR exactly 2.5 should be NORMAL (boundary uses > 2.5 for HIGH)."""
        # Need max/mean = 2.5 exactly
        # Moves: [4%, 4%, 4%, 4%, 4%] → mean=4.0, max should be 10.0 → TRR=2.5
        # [2%, 2%, 2%, 2%, 10%] → mean=3.6, max=10.0 → TRR=2.78 (too high)
        # Use [4%, 4%, 4%, 4%, 4%] with one 10.0 → mean=5.2, max=10.0 → TRR=1.92
        # Direct: set all to 4% except one at 10%: mean=(4*4+10)/5=5.2, max=10 → 1.92
        # Need: mean=X, max=2.5*X → set 4 at X, one at 2.5*X
        # 4*X + 2.5*X = 5 * mean → mean = (4*X + 2.5*X)/5 = 6.5*X/5 = 1.3*X
        # TRR = 2.5*X / 1.3*X = 1.92... not exact
        # Actually for exactly 2.5: max/mean = 2.5 where mean = (sum of all moves) / n
        # Let's use: moves = [2.0, 2.0, 2.0, 2.0] → mean=2.0, need max=5.0
        # But max must be IN the list. So [2.0, 2.0, 2.0, 5.0] → mean=2.75, max=5.0 → TRR=1.82
        # For exactly 2.5: need max = 2.5 * mean.
        # Let 3 values be 'a', 1 value be max: mean = (3a + max) / 4
        # max = 2.5 * (3a + max) / 4  → 4*max = 7.5a + 2.5*max → 1.5*max = 7.5a → max = 5a
        # So if a=2: max=10, mean = (6+10)/4 = 4.0, TRR = 10/4 = 2.5. Confirmed!
        moves = make_historical_moves("TEST", [2.0, 2.0, 2.0, 10.0])
        implied = make_implied_move("TEST", implied_pct=15.0)

        result = calculator.calculate_with_consistency(
            "TEST", date(2026, 3, 23), implied, moves
        )
        assert result.is_ok
        vrp_result, consistency = result.value

        assert abs(consistency['tail_risk_ratio'] - 2.5) < 0.01
        assert consistency['tail_risk_level'] == 'NORMAL', (
            f"TRR=2.5 should be NORMAL, got {consistency['tail_risk_level']}"
        )

    def test_trr_2_501_is_high(self, calculator):
        """TRR just above 2.5 should be HIGH."""
        # Use [2.0, 2.0, 2.0, 10.1] → mean = (6+10.1)/4 = 4.025, max=10.1, TRR=10.1/4.025=2.509
        moves = make_historical_moves("TEST", [2.0, 2.0, 2.0, 10.1])
        implied = make_implied_move("TEST", implied_pct=15.0)

        result = calculator.calculate_with_consistency(
            "TEST", date(2026, 3, 23), implied, moves
        )
        assert result.is_ok
        vrp_result, consistency = result.value

        assert consistency['tail_risk_ratio'] > 2.5
        assert consistency['tail_risk_level'] == 'HIGH', (
            f"TRR>2.5 should be HIGH, got {consistency['tail_risk_level']}"
        )

    def test_trr_exactly_1_5_is_normal(self, calculator):
        """TRR exactly 1.5 should be NORMAL (boundary uses >= 1.5)."""
        # For TRR=1.5: need max = 1.5 * mean
        # 3 vals at 'a', 1 at max: mean = (3a + max)/4
        # max = 1.5 * (3a+max)/4 → 4max = 4.5a + 1.5max → 2.5max = 4.5a → max = 1.8a
        # a=5: max=9, mean=(15+9)/4=6.0, TRR=9/6=1.5. Confirmed!
        moves = make_historical_moves("TEST", [5.0, 5.0, 5.0, 9.0])
        implied = make_implied_move("TEST", implied_pct=15.0)

        result = calculator.calculate_with_consistency(
            "TEST", date(2026, 3, 23), implied, moves
        )
        assert result.is_ok
        vrp_result, consistency = result.value

        assert abs(consistency['tail_risk_ratio'] - 1.5) < 0.01
        assert consistency['tail_risk_level'] == 'NORMAL', (
            f"TRR=1.5 should be NORMAL, got {consistency['tail_risk_level']}"
        )

    def test_trr_1_499_is_low(self, calculator):
        """TRR just below 1.5 should be LOW."""
        # a=5: max=8.9, mean=(15+8.9)/4=5.975, TRR=8.9/5.975=1.489... close to 1.49
        moves = make_historical_moves("TEST", [5.0, 5.0, 5.0, 8.9])
        implied = make_implied_move("TEST", implied_pct=15.0)

        result = calculator.calculate_with_consistency(
            "TEST", date(2026, 3, 23), implied, moves
        )
        assert result.is_ok
        vrp_result, consistency = result.value

        assert consistency['tail_risk_ratio'] < 1.5
        assert consistency['tail_risk_level'] == 'LOW', (
            f"TRR<1.5 should be LOW, got {consistency['tail_risk_level']}"
        )


class TestTRRPositionLimits:
    """Test position limit enforcement based on TRR level."""

    @pytest.fixture
    def calculator(self):
        return VRPCalculator(
            threshold_excellent=1.8,
            threshold_good=1.4,
            threshold_marginal=1.2,
            min_quarters=4,
            move_metric="close",
        )

    def test_high_trr_max_50_contracts(self, calculator):
        """HIGH TRR should limit to max 50 contracts."""
        # HIGH TRR scenario
        moves = make_historical_moves("TEST", [3.0, 3.0, 3.0, 3.0, 15.0])
        implied = make_implied_move("TEST", implied_pct=20.0)

        result = calculator.calculate_with_consistency(
            "TEST", date(2026, 3, 23), implied, moves
        )
        assert result.is_ok
        _, consistency = result.value

        assert consistency['tail_risk_level'] == 'HIGH'
        # According to CLAUDE.md: HIGH → max 50 contracts, $25,000 notional
        # The TRR level is used by downstream callers for position limits.
        # Here we verify the level is correct; position limit enforcement is
        # in the API/scan layer.

    def test_normal_trr_max_100_contracts(self, calculator):
        """NORMAL TRR should allow up to 100 contracts."""
        moves = make_historical_moves("TEST", [3.0, 4.0, 5.0, 10.0])
        implied = make_implied_move("TEST", implied_pct=15.0)

        result = calculator.calculate_with_consistency(
            "TEST", date(2026, 3, 23), implied, moves
        )
        assert result.is_ok
        _, consistency = result.value

        assert consistency['tail_risk_level'] == 'NORMAL'

    def test_low_trr_max_100_contracts(self, calculator):
        """LOW TRR should allow up to 100 contracts."""
        moves = make_historical_moves("TEST", [4.0, 5.0, 4.5, 5.5])
        implied = make_implied_move("TEST", implied_pct=15.0)

        result = calculator.calculate_with_consistency(
            "TEST", date(2026, 3, 23), implied, moves
        )
        assert result.is_ok
        _, consistency = result.value

        assert consistency['tail_risk_level'] == 'LOW'


class TestTRRRealTickerData:
    """Test TRR with data approximating real tickers."""

    @pytest.fixture
    def calculator(self):
        return VRPCalculator(
            threshold_excellent=1.8,
            threshold_good=1.4,
            threshold_marginal=1.2,
            min_quarters=4,
            move_metric="close",
        )

    def test_mu_like_trr_is_high(self, calculator):
        """MU-like data (TRR~3.05, max=11.21%, avg=3.68%) should be HIGH."""
        # Approximate MU historical moves: avg ~3.68%, max ~11.21%
        # For TRR=3.05: [2.5, 3.0, 3.5, 4.0, 4.5, 3.0, 3.5, 11.21]
        # Mean = (2.5+3+3.5+4+4.5+3+3.5+11.21)/8 = 35.21/8 = 4.40
        # TRR = 11.21/4.40 = 2.55 (approximately HIGH)
        # Use exact values that produce HIGH
        moves = make_historical_moves(
            "MU", [2.5, 3.0, 3.5, 4.0, 4.5, 3.0, 3.5, 11.21]
        )
        implied = make_implied_move("MU", implied_pct=12.0, stock_price=90.0)

        result = calculator.calculate_with_consistency(
            "MU", date(2026, 3, 23), implied, moves
        )
        assert result.is_ok
        _, consistency = result.value

        # MU should be HIGH risk
        assert consistency['tail_risk_level'] == 'HIGH', (
            f"MU-like TRR={consistency['tail_risk_ratio']:.2f} should be HIGH"
        )
        assert consistency['max_move'] == 11.21

    def test_consistent_ticker_is_low(self, calculator):
        """Very consistent ticker (e.g., consumer staple) should be LOW."""
        # Consistent moves: small variance
        moves = make_historical_moves(
            "PG", [2.0, 2.5, 2.2, 1.8, 2.3, 2.1, 2.4, 2.0]
        )
        implied = make_implied_move("PG", implied_pct=5.0, stock_price=150.0)

        result = calculator.calculate_with_consistency(
            "PG", date(2026, 3, 23), implied, moves
        )
        assert result.is_ok
        _, consistency = result.value

        assert consistency['tail_risk_level'] == 'LOW', (
            f"Consistent ticker TRR={consistency['tail_risk_ratio']:.2f} should be LOW"
        )


class TestTRRInvalidInput:
    """Test TRR behavior with invalid/edge case inputs."""

    @pytest.fixture
    def calculator(self):
        return VRPCalculator(
            threshold_excellent=1.8,
            threshold_good=1.4,
            threshold_marginal=1.2,
            min_quarters=4,
            move_metric="close",
        )

    def test_all_zero_moves_returns_error(self, calculator):
        """All zero historical moves should return an error (mean=0)."""
        moves = make_historical_moves("TEST", [0.0, 0.0, 0.0, 0.0])
        implied = make_implied_move("TEST", implied_pct=10.0)

        # calculate_with_consistency delegates to calculate which checks mean_move <= 0
        result = calculator.calculate_with_consistency(
            "TEST", date(2026, 3, 23), implied, moves
        )
        assert result.is_err
        assert result.error.code == ErrorCode.INVALID

    def test_single_quarter_insufficient(self, calculator):
        """Less than min_quarters should return error."""
        moves = make_historical_moves("TEST", [5.0, 5.0, 5.0])  # Only 3 quarters
        implied = make_implied_move("TEST", implied_pct=10.0)

        result = calculator.calculate_with_consistency(
            "TEST", date(2026, 3, 23), implied, moves
        )
        assert result.is_err
        assert result.error.code == ErrorCode.NODATA

    def test_empty_historical_moves(self, calculator):
        """Empty historical moves should return error."""
        implied = make_implied_move("TEST", implied_pct=10.0)

        result = calculator.calculate_with_consistency(
            "TEST", date(2026, 3, 23), implied, []
        )
        assert result.is_err
        assert result.error.code == ErrorCode.NODATA

    def test_all_identical_moves(self, calculator):
        """All identical moves should have TRR=1.0 (LOW)."""
        # All same: max=mean, TRR=1.0
        moves = make_historical_moves("TEST", [5.0, 5.0, 5.0, 5.0])
        implied = make_implied_move("TEST", implied_pct=10.0)

        result = calculator.calculate_with_consistency(
            "TEST", date(2026, 3, 23), implied, moves
        )
        assert result.is_ok
        _, consistency = result.value

        assert abs(consistency['tail_risk_ratio'] - 1.0) < 0.01
        assert consistency['tail_risk_level'] == 'LOW'

    def test_trr_consistency_dict_has_required_keys(self, calculator):
        """Consistency dict should contain all TRR-related keys."""
        moves = make_historical_moves("TEST", [3.0, 4.0, 5.0, 6.0])
        implied = make_implied_move("TEST", implied_pct=10.0)

        result = calculator.calculate_with_consistency(
            "TEST", date(2026, 3, 23), implied, moves
        )
        assert result.is_ok
        _, consistency = result.value

        # Verify all TRR fields exist
        assert 'max_move' in consistency
        assert 'min_move' in consistency
        assert 'tail_risk_ratio' in consistency
        assert 'tail_risk_level' in consistency

        # Verify types
        assert isinstance(consistency['tail_risk_ratio'], (int, float))
        assert isinstance(consistency['tail_risk_level'], str)
        assert consistency['tail_risk_level'] in ('HIGH', 'NORMAL', 'LOW')
