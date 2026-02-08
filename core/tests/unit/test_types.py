"""
Unit tests for domain types.
"""

import pytest
from decimal import Decimal
from datetime import date, timedelta
from src.domain.types import (
    Money,
    Percentage,
    Strike,
    OptionQuote,
    OptionChain,
)
from src.domain.enums import EarningsTiming, Recommendation


class TestMoney:
    """Tests for Money value object."""

    def test_create_from_float(self):
        m = Money(100.50)
        assert m.amount == Decimal("100.50")

    def test_create_from_decimal(self):
        m = Money(Decimal("100.50"))
        assert m.amount == Decimal("100.50")

    def test_create_from_string(self):
        m = Money("100.50")
        assert m.amount == Decimal("100.50")

    def test_addition(self):
        m1 = Money(100.0)
        m2 = Money(50.0)
        result = m1 + m2
        assert result.amount == Decimal("150.0")

    def test_subtraction(self):
        m1 = Money(100.0)
        m2 = Money(30.0)
        result = m1 - m2
        assert result.amount == Decimal("70.0")

    def test_multiplication(self):
        m = Money(100.0)
        result = m * 2
        assert result.amount == Decimal("200.0")

    def test_division(self):
        m = Money(100.0)
        result = m / 2
        assert result.amount == Decimal("50.0")

    def test_comparison(self):
        m1 = Money(100.0)
        m2 = Money(50.0)
        m3 = Money(100.0)

        assert m1 > m2
        assert m2 < m1
        assert m1 >= m3
        assert m1 <= m3

    def test_str_representation(self):
        m = Money(100.50)
        assert str(m) == "$100.50"

    def test_immutability(self):
        m = Money(100.0)
        with pytest.raises(AttributeError):
            m.amount = Decimal("200.0")


class TestPercentage:
    """Tests for Percentage value object."""

    def test_create_valid(self):
        p = Percentage(5.0)
        assert p.value == 5.0

    def test_create_negative(self):
        p = Percentage(-10.0)
        assert p.value == -10.0

    def test_create_large(self):
        p = Percentage(500.0)
        assert p.value == 500.0

    def test_create_invalid_low(self):
        with pytest.raises(ValueError):
            Percentage(-101.0)

    def test_create_invalid_high(self):
        with pytest.raises(ValueError):
            Percentage(1001.0)

    def test_to_decimal(self):
        p = Percentage(5.0)
        assert p.to_decimal() == Decimal("0.05")

    def test_str_representation(self):
        p = Percentage(5.5)
        assert str(p) == "5.50%"

    def test_immutability(self):
        p = Percentage(5.0)
        with pytest.raises(AttributeError):
            p.value = 10.0


class TestStrike:
    """Tests for Strike value object."""

    def test_create_from_float(self):
        s = Strike(100.0)
        assert s.price == Decimal("100.0")

    def test_create_from_decimal(self):
        s = Strike(Decimal("100.50"))
        assert s.price == Decimal("100.50")

    def test_hashable(self):
        s1 = Strike(100.0)
        s2 = Strike(100.0)
        s3 = Strike(105.0)

        strike_set = {s1, s2, s3}
        assert len(strike_set) == 2  # s1 and s2 should be same

    def test_equality(self):
        s1 = Strike(100.0)
        s2 = Strike(100.0)
        s3 = Strike(105.0)

        assert s1 == s2
        assert s1 != s3

    def test_ordering(self):
        s1 = Strike(100.0)
        s2 = Strike(105.0)

        assert s1 < s2
        assert s2 > s1

    def test_str_representation(self):
        s = Strike(100.50)
        assert str(s) == "$100.50"


class TestOptionQuote:
    """Tests for OptionQuote."""

    def test_create_basic(self):
        quote = OptionQuote(
            bid=Money(2.50), ask=Money(2.60), implied_volatility=Percentage(35.0)
        )

        assert quote.bid.amount == Decimal("2.50")
        assert quote.ask.amount == Decimal("2.60")
        assert quote.implied_volatility.value == 35.0

    def test_mid_calculation(self):
        quote = OptionQuote(bid=Money(2.50), ask=Money(2.60))
        assert quote.mid.amount == Decimal("2.55")

    def test_spread_calculation(self):
        quote = OptionQuote(bid=Money(2.50), ask=Money(2.60))
        assert quote.spread.amount == Decimal("0.10")

    def test_spread_pct_calculation(self):
        quote = OptionQuote(bid=Money(2.50), ask=Money(2.60))
        # (2.60 - 2.50) / 2.55 * 100 = 3.92%
        assert abs(quote.spread_pct - 3.92) < 0.01

    def test_is_liquid_true(self):
        quote = OptionQuote(
            bid=Money(2.50),
            ask=Money(2.60),
            open_interest=100,
            volume=10,
            implied_volatility=Percentage(35.0),
        )
        assert quote.is_liquid

    def test_is_liquid_false_wide_spread(self):
        quote = OptionQuote(
            bid=Money(1.0),
            ask=Money(2.0),  # 66% spread
            open_interest=100,
            volume=10,
        )
        assert not quote.is_liquid

    def test_is_liquid_false_no_oi(self):
        quote = OptionQuote(
            bid=Money(2.50), ask=Money(2.60), open_interest=0, volume=0
        )
        assert not quote.is_liquid


class TestOptionChain:
    """Tests for OptionChain."""

    def test_create_basic(self, sample_option_chain):
        chain = sample_option_chain
        assert chain.ticker == "AAPL"
        assert chain.stock_price.amount == Decimal("100.0")
        assert len(chain.calls) == 3
        assert len(chain.puts) == 3

    def test_atm_strike(self, sample_option_chain):
        chain = sample_option_chain
        atm = chain.atm_strike()
        assert atm.price == Decimal("100.0")

    def test_atm_strike_between_strikes(self):
        """Test ATM when stock price is between strikes."""
        chain = OptionChain(
            ticker="TEST",
            expiration=date(2026, 3, 23),
            stock_price=Money(102.5),
            calls={
                Strike(100.0): OptionQuote(bid=Money(5.0), ask=Money(5.1)),
                Strike(105.0): OptionQuote(bid=Money(3.0), ask=Money(3.1)),
            },
            puts={
                Strike(100.0): OptionQuote(bid=Money(3.0), ask=Money(3.1)),
                Strike(105.0): OptionQuote(bid=Money(5.0), ask=Money(5.1)),
            },
        )

        atm = chain.atm_strike()
        # Should pick 100 or 105, whichever is closer
        # 102.5 is 2.5 from 100 and 2.5 from 105, so either is valid
        assert atm.price in [Decimal("100.0"), Decimal("105.0")]

    def test_get_straddle(self, sample_option_chain):
        chain = sample_option_chain
        strike = Strike(100.0)
        call, put = chain.get_straddle(strike)

        assert call.bid.amount == Decimal("2.50")
        assert put.bid.amount == Decimal("2.50")

    def test_strikes_property(self, sample_option_chain):
        chain = sample_option_chain
        strikes = chain.strikes

        assert len(strikes) == 3
        assert strikes[0].price == Decimal("95.0")
        assert strikes[1].price == Decimal("100.0")
        assert strikes[2].price == Decimal("105.0")

    def test_strikes_near_atm(self, sample_option_chain):
        chain = sample_option_chain
        near = chain.strikes_near_atm(percent_range=5.0)

        # Stock at 100, 5% range = 95-105
        assert len(near) == 3

    def test_empty_chain_raises(self):
        chain = OptionChain(
            ticker="TEST",
            expiration=date(2026, 3, 16),
            stock_price=Money(100.0),
            calls={},
            puts={},
        )

        with pytest.raises(ValueError):
            chain.atm_strike()


class TestEnums:
    """Tests for enumerations."""

    def test_earnings_timing(self):
        assert EarningsTiming.BMO.value == "BMO"
        assert EarningsTiming.AMC.value == "AMC"

    def test_recommendation(self):
        assert Recommendation.EXCELLENT.value == "excellent"
        assert Recommendation.SKIP.value == "skip"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
