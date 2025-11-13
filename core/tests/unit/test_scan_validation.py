"""
Unit tests for scan.py validation functions.

Tests the new validation and date adjustment logic added to prevent
weekend expiration dates and invalid date configurations.
"""

import sys
from datetime import date, timedelta
from pathlib import Path
import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Import functions from scan.py
from scripts.scan import (
    adjust_to_trading_day,
    validate_expiration_date,
    calculate_expiration_date,
    get_next_friday,
)
from src.domain.enums import EarningsTiming


class TestAdjustToTradingDay:
    """Test weekend date adjustment logic."""

    def test_weekday_unchanged(self):
        """Weekdays should pass through unchanged."""
        # Monday through Friday should not change
        monday = date(2025, 1, 6)  # Monday
        tuesday = date(2025, 1, 7)  # Tuesday
        wednesday = date(2025, 1, 8)  # Wednesday
        thursday = date(2025, 1, 9)  # Thursday
        friday = date(2025, 1, 10)  # Friday

        assert adjust_to_trading_day(monday) == monday
        assert adjust_to_trading_day(tuesday) == tuesday
        assert adjust_to_trading_day(wednesday) == wednesday
        assert adjust_to_trading_day(thursday) == thursday
        assert adjust_to_trading_day(friday) == friday

    def test_saturday_moves_to_monday(self):
        """Saturday should move to Monday."""
        saturday = date(2025, 1, 11)  # Saturday
        expected_monday = date(2025, 1, 13)  # Monday
        assert adjust_to_trading_day(saturday) == expected_monday

    def test_sunday_moves_to_monday(self):
        """Sunday should move to Monday."""
        sunday = date(2025, 1, 12)  # Sunday
        expected_monday = date(2025, 1, 13)  # Monday
        assert adjust_to_trading_day(sunday) == expected_monday


class TestGetNextFriday:
    """Test next Friday calculation logic."""

    def test_from_monday(self):
        """From Monday should give Friday of same week."""
        monday = date(2025, 1, 6)
        expected_friday = date(2025, 1, 10)
        assert get_next_friday(monday) == expected_friday

    def test_from_friday(self):
        """From Friday should give NEXT Friday (not same day)."""
        friday = date(2025, 1, 10)
        expected_next_friday = date(2025, 1, 17)
        assert get_next_friday(friday) == expected_next_friday

    def test_from_saturday(self):
        """From Saturday should give Friday of next week."""
        saturday = date(2025, 1, 11)
        expected_friday = date(2025, 1, 17)
        assert get_next_friday(saturday) == expected_friday


class TestCalculateExpirationDate:
    """Test expiration date calculation with different timings."""

    def test_custom_offset_on_weekday(self):
        """Custom offset on weekday should work."""
        earnings = date(2025, 1, 6)  # Monday
        result = calculate_expiration_date(
            earnings, EarningsTiming.BMO, offset_days=1
        )
        # Monday + 1 = Tuesday (weekday)
        assert result == date(2025, 1, 7)

    def test_custom_offset_adjusts_weekend(self):
        """Custom offset resulting in weekend should adjust to Monday."""
        earnings = date(2025, 1, 10)  # Friday
        result = calculate_expiration_date(
            earnings, EarningsTiming.BMO, offset_days=1
        )
        # Friday + 1 = Saturday, should adjust to Monday
        assert result == date(2025, 1, 13)

    def test_bmo_on_friday_same_day(self):
        """BMO on Friday should use same day (0DTE)."""
        earnings = date(2025, 1, 10)  # Friday
        result = calculate_expiration_date(earnings, EarningsTiming.BMO)
        assert result == earnings

    def test_bmo_on_thursday_next_friday(self):
        """BMO on Thursday should use next Friday."""
        earnings = date(2025, 1, 9)  # Thursday
        result = calculate_expiration_date(earnings, EarningsTiming.BMO)
        assert result == date(2025, 1, 10)  # Friday

    def test_amc_on_thursday_next_day(self):
        """AMC on Thursday should use next day (Friday, 1DTE)."""
        earnings = date(2025, 1, 9)  # Thursday
        result = calculate_expiration_date(earnings, EarningsTiming.AMC)
        assert result == date(2025, 1, 10)  # Friday

    def test_amc_on_monday_next_friday(self):
        """AMC on Monday should use next Friday."""
        earnings = date(2025, 1, 6)  # Monday
        result = calculate_expiration_date(earnings, EarningsTiming.AMC)
        assert result == date(2025, 1, 10)  # Friday

    def test_unknown_timing_next_friday(self):
        """Unknown timing should use conservative next Friday."""
        earnings = date(2025, 1, 6)  # Monday
        result = calculate_expiration_date(earnings, EarningsTiming.UNKNOWN)
        assert result == date(2025, 1, 10)  # Friday


class TestValidateExpirationDate:
    """Test expiration date validation logic."""

    def test_valid_expiration(self):
        """Valid expiration should return None (no error)."""
        today = date.today()
        # Find next Monday to ensure it's a weekday
        days_to_monday = (7 - today.weekday()) % 7
        if days_to_monday == 0:
            days_to_monday = 7
        earnings = today + timedelta(days=days_to_monday)  # Next Monday
        expiration = earnings + timedelta(days=1)  # Tuesday

        result = validate_expiration_date(expiration, earnings, "AAPL")
        assert result is None

    def test_expiration_in_past(self):
        """Expiration in the past should return error."""
        today = date.today()
        earnings = today + timedelta(days=1)
        expiration = today - timedelta(days=1)  # Past

        result = validate_expiration_date(expiration, earnings, "AAPL")
        assert result is not None
        assert "in the past" in result

    def test_expiration_before_earnings(self):
        """Expiration before earnings should return error."""
        today = date.today()
        earnings = today + timedelta(days=5)
        expiration = today + timedelta(days=2)  # Before earnings

        result = validate_expiration_date(expiration, earnings, "AAPL")
        assert result is not None
        assert "before earnings" in result

    def test_expiration_on_weekend(self):
        """Expiration on weekend should return error (programming error)."""
        today = date.today()
        earnings = today + timedelta(days=1)

        # Find next Saturday
        days_to_saturday = (5 - today.weekday()) % 7
        if days_to_saturday == 0:
            days_to_saturday = 7
        saturday = today + timedelta(days=days_to_saturday)

        result = validate_expiration_date(saturday, earnings, "AAPL")
        assert result is not None
        assert "weekend" in result

    def test_expiration_too_far_future(self):
        """Expiration > 30 days after earnings should return error."""
        today = date.today()
        earnings = today + timedelta(days=1)
        expiration = earnings + timedelta(days=35)  # 35 days after

        result = validate_expiration_date(expiration, earnings, "AAPL")
        assert result is not None
        assert "30 days" in result

    def test_expiration_exactly_30_days_valid(self):
        """Expiration exactly 30 days after earnings should be valid."""
        today = date.today()
        # Find next Monday
        days_to_monday = (7 - today.weekday()) % 7
        if days_to_monday == 0:
            days_to_monday = 7
        earnings = today + timedelta(days=days_to_monday)  # Next Monday
        expiration_target = earnings + timedelta(days=30)  # 30 days later

        # Adjust if it falls on weekend
        expiration = adjust_to_trading_day(expiration_target)

        result = validate_expiration_date(expiration, earnings, "AAPL")
        assert result is None

    def test_expiration_same_as_earnings(self):
        """Expiration same as earnings (0DTE) should be valid."""
        today = date.today()
        earnings = today + timedelta(days=1)
        expiration = earnings  # Same day

        result = validate_expiration_date(expiration, earnings, "AAPL")
        assert result is None


class TestIntegrationScenarios:
    """Test real-world scenarios combining multiple functions."""

    def test_friday_earnings_bmo_with_offset_1(self):
        """Friday BMO earnings with offset 1 should adjust Saturday to Monday."""
        # Find next Friday
        today = date.today()
        days_to_friday = (4 - today.weekday()) % 7
        if days_to_friday == 0:
            days_to_friday = 7
        friday = today + timedelta(days=days_to_friday)

        # Calculate expiration with offset 1
        expiration = calculate_expiration_date(
            friday, EarningsTiming.BMO, offset_days=1
        )

        # Should be Monday (adjusted from Saturday)
        expected_monday = friday + timedelta(days=3)  # Fri + 3 = Mon
        assert expiration == expected_monday
        assert expiration.weekday() == 0  # Monday

        # Should pass validation
        validation = validate_expiration_date(expiration, friday, "AAPL")
        assert validation is None

    def test_thursday_amc_auto_calculation(self):
        """Thursday AMC should auto-calculate to Friday 1DTE."""
        # Find next Thursday
        today = date.today()
        days_to_thursday = (3 - today.weekday()) % 7
        if days_to_thursday == 0:
            days_to_thursday = 7
        thursday = today + timedelta(days=days_to_thursday)

        # Calculate expiration (no offset)
        expiration = calculate_expiration_date(thursday, EarningsTiming.AMC)

        # Should be Friday (next day)
        expected_friday = thursday + timedelta(days=1)
        assert expiration == expected_friday
        assert expiration.weekday() == 4  # Friday

        # Should pass validation
        validation = validate_expiration_date(expiration, thursday, "AAPL")
        assert validation is None

    def test_monday_unknown_timing(self):
        """Monday with unknown timing should use next Friday."""
        # Find next Monday
        today = date.today()
        days_to_monday = (7 - today.weekday()) % 7
        if days_to_monday == 0:
            days_to_monday = 7
        monday = today + timedelta(days=days_to_monday)

        # Calculate expiration (no offset, unknown timing)
        expiration = calculate_expiration_date(monday, EarningsTiming.UNKNOWN)

        # Should be Friday of same week
        expected_friday = monday + timedelta(days=4)  # Mon + 4 = Fri
        assert expiration == expected_friday
        assert expiration.weekday() == 4  # Friday

        # Should pass validation
        validation = validate_expiration_date(expiration, monday, "AAPL")
        assert validation is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
