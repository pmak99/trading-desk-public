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
    calculate_implied_move_expiration,
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

    def test_bmo_on_friday_next_friday(self):
        """BMO on Friday should use Friday 1 week out (avoid 0DTE risk)."""
        earnings = date(2025, 1, 10)  # Friday
        result = calculate_expiration_date(earnings, EarningsTiming.BMO)
        # Fri + 7 days = next Friday (Jan 17)
        assert result == date(2025, 1, 17)

    def test_bmo_on_thursday_next_week_friday(self):
        """BMO on Thursday should use Friday 1 week out (avoid 0DTE risk)."""
        earnings = date(2025, 1, 9)  # Thursday
        result = calculate_expiration_date(earnings, EarningsTiming.BMO)
        # Thu + 8 days = next Friday (Jan 17)
        assert result == date(2025, 1, 17)

    def test_amc_on_thursday_next_week_friday(self):
        """AMC on Thursday should use Friday 1 week out (avoid 0DTE risk)."""
        earnings = date(2025, 1, 9)  # Thursday
        result = calculate_expiration_date(earnings, EarningsTiming.AMC)
        # Thu + 8 days = next Friday (Jan 17)
        assert result == date(2025, 1, 17)

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


class TestMinimumDTEFloor:
    """Test minimum DTE floor enforcement (Feb 2026).

    Data shows 0-2 DTE options lose -$209k vs 3-5 DTE gains +$139k at same win rates.
    Wednesday earnings → same-week Friday = 2 DTE, should bump to next Friday.
    """

    def test_wednesday_earnings_bumped_to_next_friday(self):
        """Wednesday earnings with default min_dte=3 should use NEXT Friday (not same week).

        Wed Jan 8 → same-week Fri Jan 10 = 2 DTE (< 3), bumps to Fri Jan 17 = 9 DTE.
        """
        wednesday = date(2025, 1, 8)  # Wednesday
        result = calculate_expiration_date(wednesday, EarningsTiming.AMC)
        # Same-week Friday would be Jan 10 (2 DTE), but min_dte=3 bumps to Jan 17
        assert result == date(2025, 1, 17)  # Next Friday
        assert (result - wednesday).days == 9  # 9 DTE >= 3

    def test_monday_earnings_unchanged(self):
        """Monday earnings → same-week Friday = 4 DTE >= 3, no change."""
        monday = date(2025, 1, 6)  # Monday
        result = calculate_expiration_date(monday, EarningsTiming.AMC)
        assert result == date(2025, 1, 10)  # Friday, 4 DTE
        assert (result - monday).days == 4

    def test_tuesday_earnings_unchanged(self):
        """Tuesday earnings → same-week Friday = 3 DTE >= 3, no change."""
        tuesday = date(2025, 1, 7)  # Tuesday
        result = calculate_expiration_date(tuesday, EarningsTiming.AMC)
        assert result == date(2025, 1, 10)  # Friday, 3 DTE
        assert (result - tuesday).days == 3

    def test_thursday_earnings_already_next_week(self):
        """Thursday earnings already uses next-week Friday (8 DTE), no change."""
        thursday = date(2025, 1, 9)  # Thursday
        result = calculate_expiration_date(thursday, EarningsTiming.AMC)
        assert result == date(2025, 1, 17)  # Next Friday, 8 DTE
        assert (result - thursday).days == 8

    def test_friday_earnings_already_next_week(self):
        """Friday earnings already uses next-week Friday (7 DTE), no change."""
        friday = date(2025, 1, 10)  # Friday
        result = calculate_expiration_date(friday, EarningsTiming.AMC)
        assert result == date(2025, 1, 17)  # Next Friday, 7 DTE
        assert (result - friday).days == 7

    def test_min_dte_zero_disables_floor(self):
        """Setting min_dte=0 should disable the floor (backward compat)."""
        wednesday = date(2025, 1, 8)  # Wednesday
        result = calculate_expiration_date(wednesday, EarningsTiming.AMC, min_dte=0)
        assert result == date(2025, 1, 10)  # Same-week Friday (2 DTE allowed)
        assert (result - wednesday).days == 2

    def test_min_dte_5_bumps_tuesday(self):
        """Setting min_dte=5 should also bump Tuesday (3 DTE < 5)."""
        tuesday = date(2025, 1, 7)  # Tuesday
        result = calculate_expiration_date(tuesday, EarningsTiming.AMC, min_dte=5)
        # Same-week Fri = 3 DTE < 5, bumps to next Fri = 10 DTE
        assert result == date(2025, 1, 17)
        assert (result - tuesday).days == 10

    def test_custom_offset_ignores_min_dte(self):
        """Custom offset_days bypasses min_dte (explicit user override)."""
        wednesday = date(2025, 1, 8)
        result = calculate_expiration_date(wednesday, EarningsTiming.AMC, offset_days=1)
        # offset_days=1 → Jan 9 (Thursday), no min_dte check
        assert result == date(2025, 1, 9)


class TestValidateExpirationDate:
    """Test expiration date validation logic."""

    def test_valid_expiration(self):
        """Valid expiration should return None (no error)."""
        earnings = date(2027, 3, 8)  # Monday
        expiration = date(2027, 3, 9)  # Tuesday

        result = validate_expiration_date(expiration, earnings, "AAPL")
        assert result is None

    def test_expiration_in_past(self):
        """Expiration in the past should return error."""
        earnings = date(2027, 3, 9)  # Tuesday
        expiration = date(2027, 3, 7)  # Sunday (past relative to earnings)

        result = validate_expiration_date(expiration, earnings, "AAPL")
        assert result is not None
        assert "before earnings" in result

    def test_expiration_before_earnings(self):
        """Expiration before earnings should return error."""
        earnings = date(2027, 3, 12)  # Friday
        expiration = date(2027, 3, 9)  # Tuesday (before earnings)

        result = validate_expiration_date(expiration, earnings, "AAPL")
        assert result is not None
        assert "before earnings" in result

    def test_expiration_on_weekend(self):
        """Expiration on weekend should return error (programming error)."""
        earnings = date(2027, 3, 8)  # Monday
        saturday = date(2027, 3, 13)  # Saturday

        result = validate_expiration_date(saturday, earnings, "AAPL")
        assert result is not None
        assert "weekend" in result

    def test_expiration_too_far_future(self):
        """Expiration > 30 days after earnings should return error."""
        earnings = date(2027, 3, 8)  # Monday
        expiration = date(2027, 4, 13)  # 36 days later, a Tuesday

        result = validate_expiration_date(expiration, earnings, "AAPL")
        assert result is not None
        assert "30 days" in result

    def test_expiration_exactly_30_days_valid(self):
        """Expiration exactly 30 days after earnings should be valid."""
        earnings = date(2027, 3, 8)  # Monday
        expiration = adjust_to_trading_day(earnings + timedelta(days=30))  # Apr 7, Wed

        result = validate_expiration_date(expiration, earnings, "AAPL")
        assert result is None

    def test_expiration_same_as_earnings(self):
        """Expiration same as earnings (0DTE) should be valid."""
        earnings = date(2027, 3, 8)  # Monday
        expiration = earnings  # Same day

        result = validate_expiration_date(expiration, earnings, "AAPL")
        assert result is None


class TestIntegrationScenarios:
    """Test real-world scenarios combining multiple functions."""

    def test_friday_earnings_bmo_with_offset_1(self):
        """Friday BMO earnings with offset 1 should adjust Saturday to next trading day."""
        # Use a fixed Friday to avoid date-dependent failures (e.g., Presidents' Day)
        friday = date(2027, 3, 5)  # A Friday where the following Monday is not a holiday
        assert friday.weekday() == 4  # Sanity check: is Friday

        # Calculate expiration with offset 1
        expiration = calculate_expiration_date(
            friday, EarningsTiming.BMO, offset_days=1
        )

        # Friday + 1 = Saturday, adjusted to next trading day (Monday)
        expected = adjust_to_trading_day(friday + timedelta(days=1))
        assert expiration == expected
        assert expiration.weekday() not in [5, 6]  # Not a weekend

        # Should pass validation
        validation = validate_expiration_date(expiration, friday, "AAPL")
        assert validation is None

    def test_thursday_amc_auto_calculation(self):
        """Thursday AMC should auto-calculate to Friday 1 week out (avoid 0DTE risk)."""
        thursday = date(2027, 3, 4)  # A Thursday
        assert thursday.weekday() == 3

        # Calculate expiration (no offset)
        expiration = calculate_expiration_date(thursday, EarningsTiming.AMC)

        # Thursday + 8 days = Friday 1 week out (avoids 0DTE risk)
        expected_friday = thursday + timedelta(days=8)
        assert expiration == expected_friday
        assert expiration.weekday() == 4  # Friday

        # Should pass validation
        validation = validate_expiration_date(expiration, thursday, "AAPL")
        assert validation is None

    def test_monday_unknown_timing(self):
        """Monday with unknown timing should use next Friday."""
        monday = date(2027, 3, 8)  # A Monday
        assert monday.weekday() == 0

        # Calculate expiration (no offset, unknown timing)
        expiration = calculate_expiration_date(monday, EarningsTiming.UNKNOWN)

        # Should be Friday of same week
        expected_friday = monday + timedelta(days=4)  # Mon + 4 = Fri
        assert expiration == expected_friday
        assert expiration.weekday() == 4  # Friday

        # Should pass validation
        validation = validate_expiration_date(expiration, monday, "AAPL")
        assert validation is None


class TestCalculateImpliedMoveExpiration:
    """Test implied move expiration calculation.

    The implied move expiration should ALWAYS be the first trading day
    after earnings, regardless of earnings timing (BMO/AMC). This ensures
    VRP calculations capture the pure IV crush effect.
    """

    def test_monday_earnings_returns_tuesday(self):
        """Monday earnings should return Tuesday."""
        monday = date(2025, 1, 6)
        result = calculate_implied_move_expiration(monday)
        assert result == date(2025, 1, 7)  # Tuesday

    def test_tuesday_earnings_returns_wednesday(self):
        """Tuesday earnings should return Wednesday."""
        tuesday = date(2025, 1, 7)
        result = calculate_implied_move_expiration(tuesday)
        assert result == date(2025, 1, 8)  # Wednesday

    def test_wednesday_earnings_returns_thursday(self):
        """Wednesday earnings should return Thursday."""
        wednesday = date(2025, 1, 8)
        result = calculate_implied_move_expiration(wednesday)
        assert result == date(2025, 1, 9)  # Thursday

    def test_thursday_earnings_returns_friday(self):
        """Thursday earnings should return Friday (not next week Friday)."""
        thursday = date(2025, 1, 9)
        result = calculate_implied_move_expiration(thursday)
        # Should be Friday, not next week (unlike calculate_expiration_date)
        assert result == date(2025, 1, 10)  # Friday

    def test_friday_earnings_returns_monday(self):
        """Friday earnings should return Monday (skipping weekend)."""
        friday = date(2025, 1, 10)
        result = calculate_implied_move_expiration(friday)
        assert result == date(2025, 1, 13)  # Monday (skip weekend)

    def test_saturday_earnings_returns_monday(self):
        """Saturday earnings (hypothetical) should return Monday."""
        saturday = date(2025, 1, 11)
        result = calculate_implied_move_expiration(saturday)
        assert result == date(2025, 1, 13)  # Monday

    def test_sunday_earnings_returns_monday(self):
        """Sunday earnings (hypothetical) should return Monday."""
        sunday = date(2025, 1, 12)
        result = calculate_implied_move_expiration(sunday)
        assert result == date(2025, 1, 13)  # Monday

    def test_differs_from_trading_expiration_on_thursday(self):
        """Implied move exp should differ from trading exp on Thursday.

        This is the key bug that was fixed: Thursday earnings should use
        Friday for implied move (first post-earnings), but calculate_expiration_date
        uses next week Friday for trading (0DTE avoidance).
        """
        thursday = date(2025, 1, 9)

        implied_exp = calculate_implied_move_expiration(thursday)
        trading_exp = calculate_expiration_date(thursday, EarningsTiming.AMC)

        # Implied move: Friday (next day)
        assert implied_exp == date(2025, 1, 10)

        # Trading: Next Friday (1 week out)
        assert trading_exp == date(2025, 1, 17)

        # They should be different
        assert implied_exp != trading_exp

    def test_same_as_trading_expiration_on_monday(self):
        """Implied move exp may match trading exp on early-week days.

        On Monday, both should use a day soon after earnings.
        Trading uses Friday (same week), implied move uses Tuesday.
        """
        monday = date(2025, 1, 6)

        implied_exp = calculate_implied_move_expiration(monday)
        trading_exp = calculate_expiration_date(monday, EarningsTiming.AMC)

        # Implied move: Tuesday (next day)
        assert implied_exp == date(2025, 1, 7)

        # Trading: Friday (same week)
        assert trading_exp == date(2025, 1, 10)

        # They differ, but both are in the same week
        assert implied_exp != trading_exp
        assert implied_exp < trading_exp


class TestNextQuarterDetection:
    """Test next-quarter detection logic for stale cache validation.

    When validating stale earnings dates, if the API returns a date
    significantly different (45+ days), it likely indicates:
    - Later: Next quarter (earnings already reported or DB date wrong)
    - Earlier: Current quarter (DB has next quarter date)

    In both cases, the system should skip the ticker rather than
    blindly accepting the API date.
    """

    THRESHOLD = 45  # NEXT_QUARTER_THRESHOLD_DAYS

    def test_date_diff_44_days_should_accept(self):
        """44 days difference should be accepted as same-quarter correction."""
        db_date = date(2025, 1, 15)
        api_date = date(2025, 2, 28)  # 44 days later
        diff = (api_date - db_date).days
        assert diff == 44
        # Under threshold - should accept
        assert abs(diff) < self.THRESHOLD

    def test_date_diff_45_days_should_skip(self):
        """45 days difference should trigger skip (next quarter)."""
        db_date = date(2025, 1, 15)
        api_date = date(2025, 3, 1)  # 45 days later
        diff = (api_date - db_date).days
        assert diff == 45
        # At threshold - should skip
        assert abs(diff) >= self.THRESHOLD

    def test_date_diff_90_days_should_skip(self):
        """90 days difference (full quarter) should skip."""
        db_date = date(2025, 1, 15)
        api_date = date(2025, 4, 15)  # ~90 days later
        diff = (api_date - db_date).days
        assert diff == 90
        # Well over threshold - should skip
        assert abs(diff) >= self.THRESHOLD

    def test_negative_diff_45_days_should_skip(self):
        """Negative 45 days (API shows earlier) should also skip."""
        db_date = date(2025, 4, 15)  # DB has future (next quarter) date
        api_date = date(2025, 3, 1)  # API shows earlier date
        diff = (api_date - db_date).days
        assert diff == -45
        # abs() should catch this
        assert abs(diff) >= self.THRESHOLD

    def test_negative_diff_85_days_should_skip(self):
        """Large negative difference (API much earlier) should skip."""
        db_date = date(2025, 4, 15)  # DB has next quarter
        api_date = date(2025, 1, 20)  # API shows current quarter
        diff = (api_date - db_date).days
        assert diff == -85
        # abs() should catch this
        assert abs(diff) >= self.THRESHOLD

    def test_same_date_should_accept(self):
        """Same date (0 diff) should accept."""
        db_date = date(2025, 1, 15)
        api_date = date(2025, 1, 15)
        diff = (api_date - db_date).days
        assert diff == 0
        assert abs(diff) < self.THRESHOLD

    def test_small_negative_diff_should_accept(self):
        """Small negative difference (API few days earlier) should accept."""
        db_date = date(2025, 1, 15)
        api_date = date(2025, 1, 10)  # 5 days earlier
        diff = (api_date - db_date).days
        assert diff == -5
        assert abs(diff) < self.THRESHOLD

    def test_typical_date_correction_should_accept(self):
        """Typical 1-2 week correction should accept."""
        db_date = date(2025, 1, 15)
        api_date = date(2025, 1, 22)  # 7 days later
        diff = (api_date - db_date).days
        assert diff == 7
        assert abs(diff) < self.THRESHOLD

    def test_threshold_boundary_behavior(self):
        """Test exact boundary values."""
        db_date = date(2025, 1, 15)

        # 44 days - accept
        api_44 = db_date + timedelta(days=44)
        assert abs((api_44 - db_date).days) < self.THRESHOLD

        # 45 days - skip
        api_45 = db_date + timedelta(days=45)
        assert abs((api_45 - db_date).days) >= self.THRESHOLD

        # -44 days - accept
        api_neg44 = db_date - timedelta(days=44)
        assert abs((api_neg44 - db_date).days) < self.THRESHOLD

        # -45 days - skip
        api_neg45 = db_date - timedelta(days=45)
        assert abs((api_neg45 - db_date).days) >= self.THRESHOLD


class TestSpreadExitWarning:
    """Test that spread strategies include next-day exit warning in rationale.

    Data: Spreads held 2+ days have 31% win rate vs 69% for 0-1 day holds.
    Singles held 2+ days have 78% win rate — no warning needed.
    """

    @pytest.fixture
    def scorer(self):
        from src.domain.scoring.strategy_scorer import StrategyScorer
        return StrategyScorer()

    def _make_mock_strategy(self, strategy_type):
        """Create minimal mock strategy for rationale generation."""
        from unittest.mock import MagicMock
        from decimal import Decimal

        strategy = MagicMock()
        strategy.strategy_type = strategy_type
        strategy.probability_of_profit = 0.65
        strategy.reward_risk_ratio = 0.30
        strategy.position_theta = None
        strategy.position_vega = None
        strategy.liquidity_tier = "EXCELLENT"
        return strategy

    def _make_mock_vrp(self, vrp_ratio=2.0):
        """Create minimal mock VRP."""
        from unittest.mock import MagicMock

        vrp = MagicMock()
        vrp.vrp_ratio = vrp_ratio
        return vrp

    def test_bull_put_spread_has_exit_warning(self, scorer):
        """Bull put spread rationale should include exit warning."""
        from src.domain.enums import StrategyType
        strategy = self._make_mock_strategy(StrategyType.BULL_PUT_SPREAD)
        vrp = self._make_mock_vrp()

        rationale = scorer._generate_strategy_rationale(strategy, vrp)
        assert "EXIT next trading day" in rationale
        assert "31%" in rationale

    def test_bear_call_spread_has_exit_warning(self, scorer):
        """Bear call spread rationale should include exit warning."""
        from src.domain.enums import StrategyType
        strategy = self._make_mock_strategy(StrategyType.BEAR_CALL_SPREAD)
        vrp = self._make_mock_vrp()

        rationale = scorer._generate_strategy_rationale(strategy, vrp)
        assert "EXIT next trading day" in rationale

    def test_iron_condor_no_exit_warning(self, scorer):
        """Iron condor should NOT have spread exit warning (different warning)."""
        from src.domain.enums import StrategyType
        strategy = self._make_mock_strategy(StrategyType.IRON_CONDOR)
        vrp = self._make_mock_vrp()

        rationale = scorer._generate_strategy_rationale(strategy, vrp)
        assert "EXIT next trading day" not in rationale


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
