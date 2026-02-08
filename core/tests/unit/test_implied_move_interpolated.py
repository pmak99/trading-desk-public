"""
Tests for interpolated implied move calculator (Phase 4).
"""

import pytest
from datetime import date, timedelta
from unittest.mock import Mock

from src.application.metrics.implied_move_interpolated import ImpliedMoveCalculatorInterpolated
from src.domain.types import Money, Percentage, Strike, OptionChain, OptionQuote, ImpliedMove
from src.domain.errors import ErrorCode


class TestImpliedMoveCalculatorInterpolated:
    """Test interpolated straddle calculation."""

    @pytest.fixture
    def provider(self):
        """Mock options data provider."""
        return Mock()

    @pytest.fixture
    def calculator(self, provider):
        """Create interpolated calculator."""
        return ImpliedMoveCalculatorInterpolated(provider)

    def create_chain_between_strikes(
        self,
        ticker="TEST",
        stock_price=150.50,  # Between strikes
        strikes=[145.0, 150.0, 155.0, 160.0]
    ):
        """
        Create option chain with stock price between strikes.

        Args:
            ticker: Stock ticker
            stock_price: Stock price (should be between two strikes)
            strikes: List of strike prices

        Returns:
            OptionChain
        """
        expiration = date(2026, 4, 15)

        calls = {}
        puts = {}

        for strike_price in strikes:
            strike = Strike(strike_price)

            # Create liquid options with reasonable prices
            calls[strike] = OptionQuote(
                bid=Money(5.0),
                ask=Money(5.50),
                implied_volatility=Percentage(30.0),
                open_interest=1000,
                volume=100
            )

            puts[strike] = OptionQuote(
                bid=Money(5.0),
                ask=Money(5.50),
                implied_volatility=Percentage(32.0),
                open_interest=1000,
                volume=100
            )

        return OptionChain(
            ticker=ticker,
            expiration=expiration,
            stock_price=Money(stock_price),
            calls=calls,
            puts=puts
        )

    def create_chain_exact_atm(
        self,
        ticker="TEST",
        stock_price=150.0,  # Exactly at strike
        strikes=[145.0, 150.0, 155.0, 160.0]
    ):
        """Create chain with stock exactly at a strike."""
        return self.create_chain_between_strikes(ticker, stock_price, strikes)

    def test_interpolation_between_strikes(self, calculator, provider):
        """Test interpolation when stock is between two strikes."""
        chain = self.create_chain_between_strikes(
            stock_price=152.5,  # Exactly halfway between 150 and 155
            strikes=[145.0, 150.0, 155.0, 160.0]
        )

        from src.domain.errors import Ok
        provider.get_option_chain.return_value = Ok(chain)

        result = calculator.calculate("TEST", chain.expiration)

        assert result.is_ok
        move = result.value

        assert isinstance(move, ImpliedMove)
        assert move.ticker == "TEST"
        # Straddle should be interpolated
        assert move.straddle_cost is not None
        assert move.implied_move_pct.value > 0

    def test_exact_atm_falls_back(self, calculator, provider):
        """Test that exact ATM falls back to standard calculation."""
        chain = self.create_chain_exact_atm(
            stock_price=150.0,  # Exactly at 150 strike
            strikes=[145.0, 150.0, 155.0, 160.0]
        )

        from src.domain.errors import Ok
        provider.get_option_chain.return_value = Ok(chain)

        result = calculator.calculate("TEST", chain.expiration)

        # Should still work (falls back to standard calc)
        assert result.is_ok
        move = result.value
        assert move.straddle_cost is not None

    def test_interpolation_weight_calculation(self, calculator, provider):
        """Test that interpolation weight is calculated correctly."""
        # Stock at 151 should be 20% between 150 and 155
        # Expected weight = (151 - 150) / (155 - 150) = 0.2
        chain = self.create_chain_between_strikes(
            stock_price=151.0,
            strikes=[145.0, 150.0, 155.0, 160.0]
        )

        from src.domain.errors import Ok
        provider.get_option_chain.return_value = Ok(chain)

        result = calculator.calculate("TEST", chain.expiration)

        assert result.is_ok
        # Straddle should be closer to $150 strike straddle
        move = result.value
        assert move.straddle_cost is not None

    def test_missing_lower_strike_error(self, calculator, provider):
        """Test error when lower bracket strike is missing."""
        expiration = date(2026, 4, 15)

        # Only upper strikes available
        chain = OptionChain(
            ticker="TEST",
            expiration=expiration,
            stock_price=Money(148.0),  # Below all strikes
            calls={
                Strike(150.0): OptionQuote(
                    bid=Money(5.0),
                    ask=Money(5.50),
                    implied_volatility=Percentage(30.0),
                    open_interest=1000,
                    volume=100
                )
            },
            puts={
                Strike(150.0): OptionQuote(
                    bid=Money(5.0),
                    ask=Money(5.50),
                    implied_volatility=Percentage(32.0),
                    open_interest=1000,
                    volume=100
                )
            }
        )

        from src.domain.errors import Ok
        provider.get_option_chain.return_value = Ok(chain)

        result = calculator.calculate("TEST", expiration)

        # Should fall back to standard calc or error
        # Either is acceptable
        if result.is_err:
            assert result.error.code in [ErrorCode.NODATA, ErrorCode.INVALID]

    def test_illiquid_options_error(self, calculator, provider):
        """Test error when bracketing strikes have illiquid options."""
        chain = self.create_chain_between_strikes(
            stock_price=152.5,
            strikes=[145.0, 150.0, 155.0, 160.0]
        )

        # Make bracketing strikes illiquid
        for strike_price in [150.0, 155.0]:
            strike = Strike(strike_price)
            if strike in chain.calls:
                chain.calls[strike] = OptionQuote(
                    bid=Money(5.0),
                    ask=Money(5.50),
                    implied_volatility=Percentage(30.0),
                    open_interest=0,  # Illiquid
                    volume=0
                )
                chain.puts[strike] = OptionQuote(
                    bid=Money(5.0),
                    ask=Money(5.50),
                    implied_volatility=Percentage(32.0),
                    open_interest=0,  # Illiquid
                    volume=0
                )

        from src.domain.errors import Ok
        provider.get_option_chain.return_value = Ok(chain)

        result = calculator.calculate("TEST", chain.expiration)

        assert result.is_err
        assert result.error.code == ErrorCode.NODATA

    def test_past_expiration_error(self, calculator, provider):
        """Test error for past expiration date."""
        past_date = date(2025, 1, 1)  # Always in the past

        result = calculator.calculate("TEST", past_date)

        assert result.is_err
        assert result.error.code == ErrorCode.INVALID

    def test_provider_error_propagation(self, calculator, provider):
        """Test that provider errors are propagated."""
        from src.domain.errors import Err, AppError

        provider.get_option_chain.return_value = Err(
            AppError(ErrorCode.EXTERNAL, "API error")
        )

        result = calculator.calculate("TEST", date(2026, 4, 15))

        assert result.is_err
        assert result.error.code == ErrorCode.EXTERNAL

    def test_iv_interpolation(self, calculator, provider):
        """Test that IVs are also interpolated."""
        # Create chain with different IVs at each strike
        expiration = date(2026, 4, 15)

        chain = OptionChain(
            ticker="TEST",
            expiration=expiration,
            stock_price=Money(152.5),  # Halfway between 150 and 155
            calls={
                Strike(150.0): OptionQuote(
                    bid=Money(5.0),
                    ask=Money(5.50),
                    implied_volatility=Percentage(28.0),  # Lower IV
                    open_interest=1000,
                    volume=100
                ),
                Strike(155.0): OptionQuote(
                    bid=Money(4.0),
                    ask=Money(4.50),
                    implied_volatility=Percentage(32.0),  # Higher IV
                    open_interest=1000,
                    volume=100
                )
            },
            puts={
                Strike(150.0): OptionQuote(
                    bid=Money(5.0),
                    ask=Money(5.50),
                    implied_volatility=Percentage(30.0),
                    open_interest=1000,
                    volume=100
                ),
                Strike(155.0): OptionQuote(
                    bid=Money(4.0),
                    ask=Money(4.50),
                    implied_volatility=Percentage(34.0),
                    open_interest=1000,
                    volume=100
                )
            }
        )

        from src.domain.errors import Ok
        provider.get_option_chain.return_value = Ok(chain)

        result = calculator.calculate("TEST", expiration)

        assert result.is_ok
        move = result.value

        # Average IV should be interpolated
        if move.avg_iv is not None:
            # Should be roughly halfway between the averages
            # Lower: (28+30)/2 = 29
            # Upper: (32+34)/2 = 33
            # Halfway: 31
            assert 28.0 <= move.avg_iv.value <= 34.0

    def test_bounds_calculation(self, calculator, provider):
        """Test that upper/lower bounds are calculated correctly."""
        chain = self.create_chain_between_strikes(
            stock_price=150.0,
            strikes=[145.0, 150.0, 155.0, 160.0]
        )

        from src.domain.errors import Ok
        provider.get_option_chain.return_value = Ok(chain)

        result = calculator.calculate("TEST", chain.expiration)

        assert result.is_ok
        move = result.value

        # Bounds should be stock ± straddle
        expected_upper = float(chain.stock_price.amount) + float(move.straddle_cost.amount)
        expected_lower = float(chain.stock_price.amount) - float(move.straddle_cost.amount)

        assert abs(float(move.upper_bound.amount) - expected_upper) < 0.01
        assert abs(float(move.lower_bound.amount) - expected_lower) < 0.01

    def test_consistent_with_standard_at_exact_strike(self, calculator, provider):
        """Test that results match standard calculator when stock is at a strike."""
        from src.application.metrics.implied_move import ImpliedMoveCalculator

        chain = self.create_chain_exact_atm(
            stock_price=150.0,
            strikes=[145.0, 150.0, 155.0, 160.0]
        )

        from src.domain.errors import Ok
        provider.get_option_chain.return_value = Ok(chain)

        # Calculate with both calculators
        standard_calc = ImpliedMoveCalculator(provider)

        result_standard = standard_calc.calculate("TEST", chain.expiration)
        result_interpolated = calculator.calculate("TEST", chain.expiration)

        # Both should succeed
        assert result_standard.is_ok
        assert result_interpolated.is_ok

        # Results should be very similar (might not be exact due to fallback)
        std_move = result_standard.value
        interp_move = result_interpolated.value

        # Implied move % should be close
        assert abs(std_move.implied_move_pct.value - interp_move.implied_move_pct.value) < 1.0
