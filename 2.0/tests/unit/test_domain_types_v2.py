"""
Test suite for domain types (Money, Percentage, Strike, etc.).

Tests the fundamental value objects used throughout the 2.0 system.
"""

import pytest
from decimal import Decimal
from src.domain.types import Money, Percentage, Strike


class TestMoney:
    """Test Money value object."""

    def test_money_creation_from_float(self):
        """Money can be created from float."""
        m = Money(100.50)
        assert m.amount == Decimal("100.50")

    def test_money_creation_from_decimal(self):
        """Money can be created from Decimal."""
        m = Money(Decimal("100.50"))
        assert m.amount == Decimal("100.50")

    def test_money_creation_from_string(self):
        """Money can be created from string."""
        m = Money("100.50")
        assert m.amount == Decimal("100.50")

    def test_money_addition(self):
        """Money supports addition."""
        m1 = Money(100.00)
        m2 = Money(50.00)
        result = m1 + m2
        assert result.amount == Decimal("150.00")

    def test_money_subtraction(self):
        """Money supports subtraction."""
        m1 = Money(100.00)
        m2 = Money(50.00)
        result = m1 - m2
        assert result.amount == Decimal("50.00")

    def test_money_multiplication(self):
        """Money supports multiplication by scalar."""
        m = Money(100.00)
        result = m * 1.5
        assert result.amount == Decimal("150.00")

    def test_money_division(self):
        """Money supports division by scalar."""
        m = Money(100.00)
        result = m / 2.0
        assert result.amount == Decimal("50.00")

    def test_money_comparison_less_than(self):
        """Money supports less than comparison."""
        m1 = Money(50.00)
        m2 = Money(100.00)
        assert m1 < m2

    def test_money_comparison_greater_than(self):
        """Money supports greater than comparison."""
        m1 = Money(100.00)
        m2 = Money(50.00)
        assert m1 > m2

    def test_money_string_representation(self):
        """Money has proper string representation."""
        m = Money(100.50)
        assert str(m) == "$100.50"

    def test_money_immutable(self):
        """Money is immutable (frozen)."""
        m = Money(100.00)
        with pytest.raises(AttributeError):
            m.amount = Decimal("200.00")


class TestPercentage:
    """Test Percentage value object."""

    def test_percentage_creation_valid(self):
        """Percentage can be created with valid value."""
        p = Percentage(5.0)
        assert p.value == 5.0

    def test_percentage_creation_negative(self):
        """Percentage allows negative values (for losses)."""
        p = Percentage(-50.0)
        assert p.value == -50.0

    def test_percentage_creation_zero(self):
        """Percentage allows zero."""
        p = Percentage(0.0)
        assert p.value == 0.0

    def test_percentage_creation_too_low(self):
        """Percentage rejects values below -100%."""
        with pytest.raises(ValueError):
            Percentage(-101.0)

    def test_percentage_creation_too_high(self):
        """Percentage rejects values above 1000%."""
        with pytest.raises(ValueError):
            Percentage(1001.0)

    def test_percentage_to_decimal(self):
        """Percentage converts to decimal multiplier."""
        p = Percentage(5.0)
        assert p.to_decimal() == Decimal("0.05")

    def test_percentage_string_representation(self):
        """Percentage has proper string representation."""
        p = Percentage(5.25)
        assert str(p) == "5.25%"

    def test_percentage_immutable(self):
        """Percentage is immutable (frozen)."""
        p = Percentage(5.0)
        with pytest.raises(AttributeError):
            p.value = 10.0


class TestStrike:
    """Test Strike value object."""

    def test_strike_creation_from_float(self):
        """Strike can be created from float."""
        s = Strike(100.00)
        assert s.price == Decimal("100.00")

    def test_strike_creation_from_decimal(self):
        """Strike can be created from Decimal."""
        s = Strike(Decimal("100.00"))
        assert s.price == Decimal("100.00")

    def test_strike_creation_from_string(self):
        """Strike can be created from string."""
        s = Strike("100.00")
        assert s.price == Decimal("100.00")

    def test_strike_comparison_less_than(self):
        """Strike supports less than comparison."""
        s1 = Strike(95.00)
        s2 = Strike(100.00)
        assert s1 < s2

    def test_strike_equality(self):
        """Strikes with same price are equal."""
        s1 = Strike(100.00)
        s2 = Strike(100.00)
        assert s1 == s2

    def test_strike_hashable(self):
        """Strike is hashable (can be dict key)."""
        s = Strike(100.00)
        d = {s: "value"}
        assert d[Strike(100.00)] == "value"

    def test_strike_string_representation(self):
        """Strike has proper string representation."""
        s = Strike(100.50)
        assert str(s) == "$100.50"

    def test_strike_immutable(self):
        """Strike is immutable (frozen)."""
        s = Strike(100.00)
        with pytest.raises(AttributeError):
            s.price = Decimal("200.00")
