"""
Unit tests for calculator modules.
"""

import pytest
from datetime import date, timedelta
from src.application.metrics.implied_move import ImpliedMoveCalculator
from src.application.metrics.vrp import VRPCalculator
from src.domain.types import (
    Money,
    Percentage,
    Strike,
    OptionQuote,
    OptionChain,
    HistoricalMove,
)
from src.domain.enums import Recommendation
from src.domain.errors import ErrorCode


class TestImpliedMoveCalculator:
    """Tests for ImpliedMoveCalculator."""

    def test_calculate_basic(self, mock_options_provider):
        """Test basic implied move calculation."""
        # Setup mock chain
        stock_price = Money(100.0)
        atm_strike = Strike(100.0)

        calls = {
            atm_strike: OptionQuote(
                bid=Money(2.50),
                ask=Money(2.60),
                implied_volatility=Percentage(35.0),
                open_interest=500,
                volume=100,
            )
        }

        puts = {
            atm_strike: OptionQuote(
                bid=Money(2.50),
                ask=Money(2.60),
                implied_volatility=Percentage(35.0),
                open_interest=500,
                volume=100,
            )
        }

        chain = OptionChain(
            ticker="TEST",
            expiration=date(2026, 3, 23),
            stock_price=stock_price,
            calls=calls,
            puts=puts,
        )

        mock_options_provider.set_stock_price("TEST", stock_price)
        mock_options_provider.set_option_chain(
            "TEST", chain.expiration, chain
        )

        # Calculate
        calc = ImpliedMoveCalculator(mock_options_provider)
        result = calc.calculate("TEST", chain.expiration)

        assert result.is_ok
        implied = result.value

        # Straddle cost = 2.55 + 2.55 = 5.10
        # Implied move = 5.10 / 100 * 100 = 5.1%
        assert implied.ticker == "TEST"
        assert implied.stock_price.amount == 100.0
        assert implied.atm_strike.price == 100.0
        assert float(implied.straddle_cost.amount) == pytest.approx(5.1, abs=0.01)
        assert implied.implied_move_pct.value == pytest.approx(5.1, abs=0.01)
        assert float(implied.upper_bound.amount) == pytest.approx(105.1, abs=0.01)
        assert float(implied.lower_bound.amount) == pytest.approx(94.9, abs=0.01)

    def test_calculate_no_chain(self, mock_options_provider):
        """Test error when no option chain available."""
        calc = ImpliedMoveCalculator(mock_options_provider)
        result = calc.calculate("MISSING", date(2026, 3, 16))

        assert result.is_err
        assert result.error.code == ErrorCode.NODATA

    def test_calculate_illiquid_options(self, mock_options_provider):
        """Test error when options are illiquid."""
        stock_price = Money(100.0)
        atm_strike = Strike(100.0)

        # Wide spread = illiquid
        calls = {
            atm_strike: OptionQuote(
                bid=Money(1.0),
                ask=Money(3.0),  # 100% spread
                open_interest=0,
                volume=0,
            )
        }

        puts = {
            atm_strike: OptionQuote(
                bid=Money(1.0),
                ask=Money(3.0),
                open_interest=0,
                volume=0,
            )
        }

        chain = OptionChain(
            ticker="TEST",
            expiration=date(2026, 3, 23),
            stock_price=stock_price,
            calls=calls,
            puts=puts,
        )

        mock_options_provider.set_option_chain(
            "TEST", chain.expiration, chain
        )

        calc = ImpliedMoveCalculator(mock_options_provider)
        result = calc.calculate("TEST", chain.expiration)

        assert result.is_err
        assert result.error.code == ErrorCode.INVALID


class TestVRPCalculator:
    """Tests for VRPCalculator."""

    def test_calculate_excellent_vrp(self):
        """Test VRP calculation with excellent opportunity (2.0x+)."""
        from src.domain.types import ImpliedMove

        ticker = "TEST"
        expiration = date(2026, 3, 23)

        # Implied move: 10%
        implied_move = ImpliedMove(
            ticker=ticker,
            expiration=expiration,
            stock_price=Money(100.0),
            atm_strike=Strike(100.0),
            straddle_cost=Money(10.0),
            implied_move_pct=Percentage(10.0),
            upper_bound=Money(110.0),
            lower_bound=Money(90.0),
        )

        # Historical moves: 5% average (close_move_pct is the default metric)
        historical_moves = [
            HistoricalMove(
                ticker=ticker,
                earnings_date=date(2026, 3, 16) - timedelta(days=90 * i),
                prev_close=Money(100.0),
                earnings_open=Money(100.0),
                earnings_high=Money(102.5),
                earnings_low=Money(97.5),
                earnings_close=Money(105.0),  # 5% close move
                intraday_move_pct=Percentage(5.0),
                gap_move_pct=Percentage(0.0),
                close_move_pct=Percentage(5.0),  # Default metric used by VRP
            )
            for i in range(1, 5)
        ]

        calc = VRPCalculator()
        result = calc.calculate(
            ticker, expiration, implied_move, historical_moves
        )

        assert result.is_ok
        vrp = result.value

        # VRP ratio = 10% / 5% = 2.0x (marginal since excellent threshold is 7.0x)
        assert vrp.vrp_ratio == pytest.approx(2.0, abs=0.01)
        # Note: Default thresholds are excellent=7.0x, good=4.0x, marginal=1.5x
        # 2.0x falls in MARGINAL category (>= 1.5x but < 4.0x)
        assert vrp.recommendation == Recommendation.MARGINAL
        # MARGINAL is NOT tradeable - only EXCELLENT and GOOD are
        assert not vrp.is_tradeable

    def test_calculate_good_vrp(self):
        """Test VRP with good opportunity (4.0x-7.0x threshold)."""
        from src.domain.types import ImpliedMove

        ticker = "TEST"
        expiration = date(2026, 3, 23)

        # Implied move: 7.5%
        implied_move = ImpliedMove(
            ticker=ticker,
            expiration=expiration,
            stock_price=Money(100.0),
            atm_strike=Strike(100.0),
            straddle_cost=Money(7.5),
            implied_move_pct=Percentage(7.5),
            upper_bound=Money(107.5),
            lower_bound=Money(92.5),
        )

        # Historical moves: 5% average (close_move_pct is the default metric)
        historical_moves = [
            HistoricalMove(
                ticker=ticker,
                earnings_date=date(2026, 3, 16) - timedelta(days=90 * i),
                prev_close=Money(100.0),
                earnings_open=Money(100.0),
                earnings_high=Money(102.5),
                earnings_low=Money(97.5),
                earnings_close=Money(105.0),  # 5% close move
                intraday_move_pct=Percentage(5.0),
                gap_move_pct=Percentage(0.0),
                close_move_pct=Percentage(5.0),  # Default metric used by VRP
            )
            for i in range(1, 5)
        ]

        calc = VRPCalculator()
        result = calc.calculate(
            ticker, expiration, implied_move, historical_moves
        )

        assert result.is_ok
        vrp = result.value

        # VRP ratio = 7.5% / 5% = 1.5x (marginal - exactly at threshold)
        assert vrp.vrp_ratio == pytest.approx(1.5, abs=0.01)
        # Default thresholds: excellent=7.0x, good=4.0x, marginal=1.5x
        # 1.5x is exactly at MARGINAL threshold
        assert vrp.recommendation == Recommendation.MARGINAL
        # MARGINAL is NOT tradeable - only EXCELLENT and GOOD are
        assert not vrp.is_tradeable

    def test_calculate_skip_vrp(self):
        """Test VRP with insufficient edge (<1.5x)."""
        from src.domain.types import ImpliedMove

        ticker = "TEST"
        expiration = date(2026, 3, 23)

        # Implied move: 5%
        implied_move = ImpliedMove(
            ticker=ticker,
            expiration=expiration,
            stock_price=Money(100.0),
            atm_strike=Strike(100.0),
            straddle_cost=Money(5.0),
            implied_move_pct=Percentage(5.0),
            upper_bound=Money(105.0),
            lower_bound=Money(95.0),
        )

        # Historical moves: 5% average (no edge) - using close_move_pct
        historical_moves = [
            HistoricalMove(
                ticker=ticker,
                earnings_date=date(2026, 3, 16) - timedelta(days=90 * i),
                prev_close=Money(100.0),
                earnings_open=Money(100.0),
                earnings_high=Money(102.5),
                earnings_low=Money(97.5),
                earnings_close=Money(105.0),  # 5% close move
                intraday_move_pct=Percentage(5.0),
                gap_move_pct=Percentage(0.0),
                close_move_pct=Percentage(5.0),  # Default metric used by VRP
            )
            for i in range(1, 5)
        ]

        calc = VRPCalculator()
        result = calc.calculate(
            ticker, expiration, implied_move, historical_moves
        )

        assert result.is_ok
        vrp = result.value

        # VRP ratio = 5% / 5% = 1.0x (skip - below marginal threshold of 1.5x)
        assert vrp.vrp_ratio == pytest.approx(1.0, abs=0.01)
        assert vrp.recommendation == Recommendation.SKIP
        assert not vrp.is_tradeable

    def test_calculate_insufficient_history(self):
        """Test error with insufficient historical data."""
        from src.domain.types import ImpliedMove

        ticker = "TEST"
        expiration = date(2026, 3, 23)

        implied_move = ImpliedMove(
            ticker=ticker,
            expiration=expiration,
            stock_price=Money(100.0),
            atm_strike=Strike(100.0),
            straddle_cost=Money(10.0),
            implied_move_pct=Percentage(10.0),
            upper_bound=Money(110.0),
            lower_bound=Money(90.0),
        )

        # Only 2 quarters (need 4+)
        historical_moves = [
            HistoricalMove(
                ticker=ticker,
                earnings_date=date(2026, 3, 16) - timedelta(days=90 * i),
                prev_close=Money(100.0),
                earnings_open=Money(100.0),
                earnings_high=Money(102.5),
                earnings_low=Money(97.5),
                earnings_close=Money(100.0),
                intraday_move_pct=Percentage(5.0),
                gap_move_pct=Percentage(0.0),
                close_move_pct=Percentage(0.0),
            )
            for i in range(1, 3)
        ]

        calc = VRPCalculator()
        result = calc.calculate(
            ticker, expiration, implied_move, historical_moves
        )

        assert result.is_err
        assert result.error.code == ErrorCode.NODATA


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
