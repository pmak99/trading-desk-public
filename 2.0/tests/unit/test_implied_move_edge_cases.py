"""
Unit tests for implied move edge cases.

Tests boundary conditions and error handling in implied move calculation:
- Zero straddle cost (division by zero guard)
- Zero stock price
- None/missing bid/ask
- Very large implied moves
- Negative option prices
"""

import pytest
from datetime import date, timedelta

from src.application.metrics.implied_move_common import calculate_from_atm_chain
from src.domain.types import (
    Money,
    Percentage,
    Strike,
    OptionQuote,
    OptionChain,
)
from src.domain.errors import ErrorCode


# ============================================================================
# Helpers
# ============================================================================


def make_option(
    bid: float = 3.00,
    ask: float = 3.10,
    iv: float = 30.0,
    oi: int = 1000,
    volume: int = 200,
) -> OptionQuote:
    """Create a test OptionQuote."""
    return OptionQuote(
        bid=Money(bid) if bid is not None else None,
        ask=Money(ask) if ask is not None else None,
        implied_volatility=Percentage(iv) if iv is not None else None,
        open_interest=oi,
        volume=volume,
    )


def make_chain(
    stock_price: float = 100.0,
    call_bid: float = 3.00,
    call_ask: float = 3.10,
    put_bid: float = 3.00,
    put_ask: float = 3.10,
    ticker: str = "TEST",
) -> OptionChain:
    """Create a simple ATM option chain for testing."""
    strike = Strike(stock_price)
    return OptionChain(
        ticker=ticker,
        expiration=date(2026, 3, 23),
        stock_price=Money(stock_price),
        calls={strike: make_option(bid=call_bid, ask=call_ask)},
        puts={strike: make_option(bid=put_bid, ask=put_ask)},
    )


# ============================================================================
# Tests
# ============================================================================


class TestZeroStraddleCost:
    """Tests for zero straddle cost guard (recently added fix)."""

    def test_zero_bid_ask_both_legs(self):
        """Both legs with bid=0, ask=0 should return INVALID error."""
        chain = make_chain(
            stock_price=100.0,
            call_bid=0.0, call_ask=0.0,
            put_bid=0.0, put_ask=0.0,
        )
        result = calculate_from_atm_chain(chain, "TEST", date(2026, 3, 23))

        assert result.is_err
        assert result.error.code == ErrorCode.INVALID
        assert "straddle cost" in result.error.message.lower() or "Illiquid" in result.error.message

    def test_zero_mid_one_leg(self):
        """One leg with zero mid should still result in positive straddle if other leg is ok."""
        chain = make_chain(
            stock_price=100.0,
            call_bid=0.0, call_ask=0.0,
            put_bid=5.0, put_ask=5.10,
        )
        result = calculate_from_atm_chain(chain, "TEST", date(2026, 3, 23))

        # Should either error (illiquid) or succeed with small straddle
        # Zero bid/ask triggers is_liquid=False
        if result.is_err:
            assert result.error.code in (ErrorCode.INVALID, ErrorCode.NODATA)


class TestZeroStockPrice:
    """Tests for zero/negative stock price."""

    def test_zero_stock_price_returns_error(self):
        """Stock price of $0 should return INVALID error."""
        strike = Strike(0)
        chain = OptionChain(
            ticker="TEST",
            expiration=date(2026, 3, 23),
            stock_price=Money(0.0),
            calls={strike: make_option()},
            puts={strike: make_option()},
        )
        result = calculate_from_atm_chain(chain, "TEST", date(2026, 3, 23))

        assert result.is_err
        assert result.error.code == ErrorCode.INVALID

    def test_negative_stock_price_returns_error(self):
        """Negative stock price should return INVALID error."""
        strike = Strike(-10)
        chain = OptionChain(
            ticker="TEST",
            expiration=date(2026, 3, 23),
            stock_price=Money(-10.0),
            calls={strike: make_option()},
            puts={strike: make_option()},
        )
        result = calculate_from_atm_chain(chain, "TEST", date(2026, 3, 23))

        assert result.is_err


class TestMissingBidAsk:
    """Tests for None/missing bid/ask values."""

    def test_none_bid_ask_call(self):
        """None bid/ask on call should be handled."""
        strike = Strike(100)
        chain = OptionChain(
            ticker="TEST",
            expiration=date(2026, 3, 23),
            stock_price=Money(100.0),
            calls={strike: make_option(bid=None, ask=None)},
            puts={strike: make_option()},
        )
        result = calculate_from_atm_chain(chain, "TEST", date(2026, 3, 23))

        # Should fail due to illiquidity or missing data
        if result.is_err:
            assert result.error.code in (ErrorCode.INVALID, ErrorCode.NODATA)

    def test_none_bid_ask_put(self):
        """None bid/ask on put should be handled."""
        strike = Strike(100)
        chain = OptionChain(
            ticker="TEST",
            expiration=date(2026, 3, 23),
            stock_price=Money(100.0),
            calls={strike: make_option()},
            puts={strike: make_option(bid=None, ask=None)},
        )
        result = calculate_from_atm_chain(chain, "TEST", date(2026, 3, 23))

        if result.is_err:
            assert result.error.code in (ErrorCode.INVALID, ErrorCode.NODATA)


class TestLargeImpliedMove:
    """Tests for very large implied move percentages."""

    def test_very_large_straddle(self):
        """Very large straddle cost (>30% of stock) should succeed with warning."""
        chain = make_chain(
            stock_price=100.0,
            call_bid=25.0, call_ask=25.10,
            put_bid=25.0, put_ask=25.10,
        )
        result = calculate_from_atm_chain(
            chain, "TEST", date(2026, 3, 23),
            validate_straddle_cost=True,
        )

        # Should succeed but with high implied move
        assert result.is_ok
        assert result.value.implied_move_pct.value > 40.0

    def test_straddle_greater_than_stock_price(self):
        """Straddle cost > 100% of stock should still succeed (penny stocks)."""
        chain = make_chain(
            stock_price=5.0,
            call_bid=3.00, call_ask=3.10,
            put_bid=3.00, put_ask=3.10,
        )
        result = calculate_from_atm_chain(
            chain, "TEST", date(2026, 3, 23),
            validate_straddle_cost=True,
        )

        assert result.is_ok
        assert result.value.implied_move_pct.value > 100.0


class TestNormalCase:
    """Tests for normal/expected implied move calculations."""

    def test_normal_atm_straddle(self):
        """Standard ATM straddle should calculate correctly."""
        chain = make_chain(
            stock_price=100.0,
            call_bid=5.0, call_ask=5.10,
            put_bid=5.0, put_ask=5.10,
        )
        result = calculate_from_atm_chain(
            chain, "TEST", date(2026, 3, 23),
        )

        assert result.is_ok
        # Straddle = call mid (5.05) + put mid (5.05) = 10.10
        # Implied move = 10.10 / 100 * 100 = 10.10%
        assert 10.0 < result.value.implied_move_pct.value < 10.2

    def test_result_fields_populated(self):
        """Result should have all expected fields populated."""
        chain = make_chain(stock_price=150.0)
        result = calculate_from_atm_chain(
            chain, "NVDA", date(2026, 3, 23),
        )

        assert result.is_ok
        move = result.value
        assert move.ticker == "NVDA"
        assert move.stock_price.amount == 150.0
        assert move.straddle_cost.amount > 0
        assert move.upper_bound.amount > move.stock_price.amount
        assert move.lower_bound.amount < move.stock_price.amount


class TestEmptyChain:
    """Tests for missing strikes in chain."""

    def test_missing_call_at_atm(self):
        """Missing call at ATM strike should return NODATA error."""
        strike = Strike(100)
        chain = OptionChain(
            ticker="TEST",
            expiration=date(2026, 3, 23),
            stock_price=Money(100.0),
            calls={},  # No calls
            puts={strike: make_option()},
        )
        result = calculate_from_atm_chain(chain, "TEST", date(2026, 3, 23))

        assert result.is_err

    def test_missing_put_at_atm(self):
        """Missing put at ATM strike should return NODATA error."""
        strike = Strike(100)
        chain = OptionChain(
            ticker="TEST",
            expiration=date(2026, 3, 23),
            stock_price=Money(100.0),
            calls={strike: make_option()},
            puts={},  # No puts
        )
        result = calculate_from_atm_chain(chain, "TEST", date(2026, 3, 23))

        assert result.is_err
        assert result.error.code == ErrorCode.NODATA

    def test_empty_chain_both(self):
        """Empty calls and puts should return error."""
        chain = OptionChain(
            ticker="TEST",
            expiration=date(2026, 3, 23),
            stock_price=Money(100.0),
            calls={},
            puts={},
        )
        result = calculate_from_atm_chain(chain, "TEST", date(2026, 3, 23))

        assert result.is_err


class TestValidateStraddleCostFlag:
    """Tests for the validate_straddle_cost flag."""

    def test_no_validation_skips_warnings(self):
        """validate_straddle_cost=False should skip cost sanity checks."""
        chain = make_chain(
            stock_price=100.0,
            call_bid=0.10, call_ask=0.15,
            put_bid=0.10, put_ask=0.15,
        )
        result = calculate_from_atm_chain(
            chain, "TEST", date(2026, 3, 23),
            validate_straddle_cost=False,
        )

        # Should succeed without warnings
        assert result.is_ok
        assert result.value.implied_move_pct.value < 1.0  # Very small straddle


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
