"""
Unit tests for timezone utilities.

Tests timezone handling, market hours, and DST transitions.
"""

import pytest
from unittest.mock import patch, Mock
from datetime import datetime, timedelta
import pytz

from src.core.timezone_utils import (
    get_eastern_now,
    to_eastern,
    get_market_date,
    is_market_hours,
    is_after_hours,
    EASTERN
)


class TestTimezoneUtils:
    """Test timezone utility functions."""

    def test_eastern_timezone_constant(self):
        """Test EASTERN constant is US/Eastern."""
        assert EASTERN.zone == 'US/Eastern'

    def test_get_eastern_now_returns_aware_datetime(self):
        """Test get_eastern_now() returns timezone-aware datetime."""
        result = get_eastern_now()

        assert result.tzinfo is not None
        assert result.tzinfo.zone == 'US/Eastern' or str(result.tzinfo) in ['EST', 'EDT']

    def test_get_eastern_now_is_eastern_time(self):
        """Test get_eastern_now() returns Eastern time, not local time."""
        result = get_eastern_now()

        # Compare with explicitly created Eastern time
        now_utc = datetime.now(pytz.UTC)
        now_et = now_utc.astimezone(EASTERN)

        # Should be within a few seconds
        diff = abs((result - now_et).total_seconds())
        assert diff < 5, "Time difference too large"

    def test_to_eastern_with_aware_datetime(self):
        """Test to_eastern() converts timezone-aware datetime."""
        # Create UTC datetime
        utc_time = datetime(2024, 7, 15, 14, 30, 0, tzinfo=pytz.UTC)

        result = to_eastern(utc_time)

        assert result.tzinfo is not None
        assert result.tzinfo.zone == 'US/Eastern' or str(result.tzinfo) in ['EDT']
        # July 15 is EDT (UTC-4), so 14:30 UTC = 10:30 EDT
        assert result.hour == 10
        assert result.minute == 30

    def test_to_eastern_with_naive_datetime(self):
        """Test to_eastern() treats naive datetime as UTC."""
        # Create naive datetime
        naive_time = datetime(2024, 1, 15, 14, 30, 0)

        result = to_eastern(naive_time)

        assert result.tzinfo is not None
        assert result.tzinfo.zone == 'US/Eastern' or str(result.tzinfo) in ['EST']
        # January 15 is EST (UTC-5), so 14:30 UTC = 09:30 EST
        assert result.hour == 9
        assert result.minute == 30

    def test_get_market_date_format(self):
        """Test get_market_date() returns YYYY-MM-DD format."""
        result = get_market_date()

        # Should be in YYYY-MM-DD format
        assert len(result) == 10
        assert result[4] == '-'
        assert result[7] == '-'

        # Should be parseable as date
        parsed = datetime.strptime(result, '%Y-%m-%d')
        assert parsed is not None

    def test_get_market_date_uses_eastern_time(self):
        """Test get_market_date() uses Eastern time, not local time."""
        with patch('src.core.timezone_utils.datetime') as mock_datetime:
            # Mock Eastern time to be 2024-07-15 23:30 (before midnight)
            mock_et = Mock()
            mock_et.strftime.return_value = '2024-07-15'
            mock_datetime.now.return_value = mock_et

            result = get_market_date()

            # Should use Eastern timezone
            mock_datetime.now.assert_called_once_with(EASTERN)
            assert result == '2024-07-15'

    @pytest.mark.parametrize("hour,minute,weekday,expected", [
        (9, 30, 0, True),   # Monday 9:30 AM - market open
        (9, 29, 0, False),  # Monday 9:29 AM - before open
        (12, 0, 2, True),   # Wednesday noon - market hours
        (16, 0, 4, True),   # Friday 4:00 PM - market close (inclusive)
        (16, 1, 4, False),  # Friday 4:01 PM - after close
        (10, 0, 5, False),  # Saturday - closed
        (10, 0, 6, False),  # Sunday - closed
    ])
    def test_is_market_hours(self, hour, minute, weekday, expected):
        """Test is_market_hours() with various times."""
        with patch('src.core.timezone_utils.get_eastern_now') as mock_now:
            # Create mock datetime in Eastern time
            mock_et = EASTERN.localize(datetime(2024, 7, 15, hour, minute, 0))
            # Adjust to correct weekday (0=Mon, 6=Sun)
            days_diff = weekday - mock_et.weekday()
            mock_et = EASTERN.localize(
                datetime(2024, 7, 15, hour, minute, 0) + timedelta(days=days_diff)
            )
            mock_now.return_value = mock_et

            result = is_market_hours()

            assert result == expected, f"Failed for {hour}:{minute:02d} on weekday {weekday}"

    @pytest.mark.parametrize("hour,minute,weekday,expected", [
        (16, 0, 0, True),   # Monday 4:00 PM - after-hours start
        (17, 30, 2, True),  # Wednesday 5:30 PM - after-hours
        (20, 0, 4, True),   # Friday 8:00 PM - after-hours end (inclusive)
        (20, 1, 4, False),  # Friday 8:01 PM - after after-hours
        (15, 59, 3, False), # Thursday 3:59 PM - before after-hours
        (18, 0, 5, False),  # Saturday - closed
        (18, 0, 6, False),  # Sunday - closed
    ])
    def test_is_after_hours(self, hour, minute, weekday, expected):
        """Test is_after_hours() with various times."""
        with patch('src.core.timezone_utils.get_eastern_now') as mock_now:
            # Create mock datetime in Eastern time
            mock_et = EASTERN.localize(datetime(2024, 7, 15, hour, minute, 0))
            # Adjust to correct weekday
            days_diff = weekday - mock_et.weekday()
            mock_et = EASTERN.localize(
                datetime(2024, 7, 15, hour, minute, 0) + timedelta(days=days_diff)
            )
            mock_now.return_value = mock_et

            result = is_after_hours()

            assert result == expected, f"Failed for {hour}:{minute:02d} on weekday {weekday}"

    def test_dst_transition_handling(self):
        """Test that DST transitions are handled correctly."""
        # Winter (EST - UTC-5)
        winter_utc = datetime(2024, 1, 15, 17, 0, 0, tzinfo=pytz.UTC)
        winter_et = to_eastern(winter_utc)

        # Summer (EDT - UTC-4)
        summer_utc = datetime(2024, 7, 15, 17, 0, 0, tzinfo=pytz.UTC)
        summer_et = to_eastern(summer_utc)

        # Verify offsets are different
        winter_offset = winter_et.strftime('%z')
        summer_offset = summer_et.strftime('%z')

        assert winter_offset == '-0500', "Winter should be EST (UTC-5)"
        assert summer_offset == '-0400', "Summer should be EDT (UTC-4)"

        # Verify times are correct
        assert winter_et.hour == 12, "17:00 UTC in winter = 12:00 EST"
        assert summer_et.hour == 13, "17:00 UTC in summer = 13:00 EDT"

    def test_market_date_stability(self):
        """Test that get_market_date() returns same value across rapid calls."""
        date1 = get_market_date()
        date2 = get_market_date()
        date3 = get_market_date()

        assert date1 == date2 == date3, "Market date should be stable"

    def test_timezone_aware_comparison(self):
        """Test that Eastern times can be compared correctly."""
        now_et = get_eastern_now()
        later_et = now_et + timedelta(hours=1)

        assert later_et > now_et, "Timezone-aware comparison should work"
        assert (later_et - now_et).total_seconds() == 3600, "Time difference should be correct"

    def test_boundary_conditions(self):
        """Test boundary conditions for market hours."""
        with patch('src.core.timezone_utils.get_eastern_now') as mock_now:
            # Test exact market open: 9:30 AM Monday
            mock_now.return_value = EASTERN.localize(datetime(2024, 7, 15, 9, 30, 0))
            assert is_market_hours() is True, "9:30 AM should be market hours"

            # Test exact market close: 4:00 PM Monday
            mock_now.return_value = EASTERN.localize(datetime(2024, 7, 15, 16, 0, 0))
            assert is_market_hours() is True, "4:00 PM should be market hours"

            # Test one second after close
            mock_now.return_value = EASTERN.localize(datetime(2024, 7, 15, 16, 0, 1))
            assert is_market_hours() is False, "4:00:01 PM should not be market hours"

    def test_weekend_handling(self):
        """Test that weekends are correctly identified as non-market time."""
        with patch('src.core.timezone_utils.get_eastern_now') as mock_now:
            # Saturday
            mock_now.return_value = EASTERN.localize(datetime(2024, 7, 13, 12, 0, 0))
            assert is_market_hours() is False, "Saturday should not be market hours"
            assert is_after_hours() is False, "Saturday should not be after-hours"

            # Sunday
            mock_now.return_value = EASTERN.localize(datetime(2024, 7, 14, 12, 0, 0))
            assert is_market_hours() is False, "Sunday should not be market hours"
            assert is_after_hours() is False, "Sunday should not be after-hours"
