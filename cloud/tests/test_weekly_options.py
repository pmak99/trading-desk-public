"""
Tests for weekly options detection filter.

Tests both the standalone detection function and its integration
with fetch_real_implied_move.
"""

import pytest
from datetime import datetime, timedelta

from src.domain.weekly_options import (
    has_weekly_options,
    WEEKLY_DETECTION_WINDOW_DAYS,
    WEEKLY_DETECTION_MIN_FRIDAYS,
)


class TestHasWeeklyOptions:
    """Test suite for has_weekly_options function."""

    def test_three_fridays_returns_true(self):
        """3 Fridays in 21 days should return True."""
        # 2026-01-21 is a Wednesday
        reference = "2026-01-21"
        # Next 3 Fridays: 2026-01-23, 2026-01-30, 2026-02-06
        expirations = ["2026-01-23", "2026-01-30", "2026-02-06"]

        has_weeklies, reason = has_weekly_options(expirations, reference)

        assert has_weeklies is True
        assert "3 Friday expirations" in reason

    def test_two_fridays_returns_true(self):
        """2 Fridays in 21 days should return True (minimum threshold)."""
        reference = "2026-01-21"
        # Next 2 Fridays: 2026-01-23, 2026-01-30
        expirations = ["2026-01-23", "2026-01-30"]

        has_weeklies, reason = has_weekly_options(expirations, reference)

        assert has_weeklies is True
        assert "2 Friday expirations" in reason

    def test_one_friday_returns_false(self):
        """1 Friday in 21 days should return False."""
        reference = "2026-01-21"
        # Only 1 Friday within the window
        expirations = ["2026-01-23"]

        has_weeklies, reason = has_weekly_options(expirations, reference)

        assert has_weeklies is False
        assert "Only 1 Friday" in reason
        assert "need 2+" in reason

    def test_empty_expirations_returns_false(self):
        """Empty expirations list should return False."""
        reference = "2026-01-21"
        expirations = []

        has_weeklies, reason = has_weekly_options(expirations, reference)

        assert has_weeklies is False
        assert "No expirations available" in reason

    def test_all_past_expirations_returns_false(self):
        """All past expirations should return False."""
        reference = "2026-01-21"
        # All in the past
        expirations = ["2026-01-10", "2026-01-17", "2026-01-03"]

        has_weeklies, reason = has_weekly_options(expirations, reference)

        assert has_weeklies is False
        assert "Only 0 Friday" in reason

    def test_expirations_beyond_window_ignored(self):
        """Expirations beyond 21-day window should be ignored."""
        reference = "2026-01-21"
        # 2026-01-23 is within window (Friday)
        # 2026-02-20 is beyond window (Friday, 30 days out)
        expirations = ["2026-01-23", "2026-02-20"]

        has_weeklies, reason = has_weekly_options(expirations, reference)

        # Only 1 Friday within window
        assert has_weeklies is False
        assert "Only 1 Friday" in reason

    def test_permissive_on_api_error(self):
        """On API error (None expirations), should return False with clear reason."""
        # This tests the error path - the function returns False for empty list
        # But callers should handle errors and default to True (permissive)
        has_weeklies, reason = has_weekly_options([], "2026-01-21")
        assert has_weeklies is False
        assert "No expirations" in reason


class TestConstants:
    """Test suite for module constants."""

    def test_detection_window_days(self):
        """Window should be 21 days."""
        assert WEEKLY_DETECTION_WINDOW_DAYS == 21

    def test_minimum_fridays(self):
        """Minimum Fridays should be 2."""
        assert WEEKLY_DETECTION_MIN_FRIDAYS == 2


class TestImpliedMoveIntegration:
    """Test weekly options fields in fetch_real_implied_move result."""

    @pytest.mark.asyncio
    async def test_implied_move_result_includes_weekly_fields(self):
        """fetch_real_implied_move result should include weekly options fields."""
        from unittest.mock import AsyncMock, MagicMock

        # Create mock tradier client
        tradier = AsyncMock()
        tradier.get_quote.return_value = {"last": 100.0}
        tradier.get_expirations.return_value = [
            "2026-01-23", "2026-01-30", "2026-02-06"  # 3 Fridays
        ]
        tradier.get_options_chain.return_value = [
            {"strike": 100, "option_type": "call", "bid": 2.0, "ask": 2.2},
            {"strike": 100, "option_type": "put", "bid": 1.8, "ask": 2.0},
        ]

        from src.domain.implied_move import fetch_real_implied_move

        result = await fetch_real_implied_move(
            tradier, "AAPL", "2026-01-22"
        )

        # Verify weekly options fields are present
        assert "has_weekly_options" in result
        assert "weekly_reason" in result
        assert "expirations" in result

        # Verify values (3 Fridays = has weeklies)
        assert result["has_weekly_options"] is True
        assert "3 Friday" in result["weekly_reason"]
        assert len(result["expirations"]) == 3

    @pytest.mark.asyncio
    async def test_implied_move_no_weeklies(self):
        """fetch_real_implied_move should detect when no weekly options."""
        from unittest.mock import AsyncMock

        tradier = AsyncMock()
        tradier.get_quote.return_value = {"last": 100.0}
        # Only 1 Friday expiration (monthly only)
        tradier.get_expirations.return_value = ["2026-01-23"]
        tradier.get_options_chain.return_value = [
            {"strike": 100, "option_type": "call", "bid": 2.0, "ask": 2.2},
            {"strike": 100, "option_type": "put", "bid": 1.8, "ask": 2.0},
        ]

        from src.domain.implied_move import fetch_real_implied_move

        result = await fetch_real_implied_move(
            tradier, "XYZ", "2026-01-22"
        )

        assert result["has_weekly_options"] is False
        assert "Only 1 Friday" in result["weekly_reason"]

    @pytest.mark.asyncio
    async def test_implied_move_defaults_permissive_on_error(self):
        """When API fails, weekly options should default to True (permissive)."""
        from unittest.mock import AsyncMock

        tradier = AsyncMock()
        tradier.get_quote.return_value = None  # No price

        from src.domain.implied_move import fetch_real_implied_move

        result = await fetch_real_implied_move(
            tradier, "FAIL", "2026-01-22"
        )

        # Default is True (permissive - don't block trades on API error)
        assert result["has_weekly_options"] is True
