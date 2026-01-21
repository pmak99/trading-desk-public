"""
Tests for weekly options detection filter.

Tests both the standalone detection function and its integration
with fetch_real_implied_move.

Note: Weekly options detection is imported from 2.0 (follows "Import 2.0, Don't Copy" pattern).
"""

import pytest
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Import from 2.0 (the source of truth)
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "2.0"))
from src.application.filters.weekly_options import (
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


class TestFilterPathIntegration:
    """Integration tests for full filter path in _analyze_single_ticker."""

    @pytest.mark.asyncio
    async def test_filter_mode_filter_returns_none_for_no_weeklies(self):
        """filter_mode='filter' should return None for tickers without weekly options."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        # Mock settings to require weekly options
        mock_settings = MagicMock()
        mock_settings.require_weekly_options = True
        mock_settings.VRP_DISCOVERY = 1.8

        # Mock tradier client - only 1 Friday (no weeklies)
        mock_tradier = AsyncMock()
        mock_tradier.get_quote.return_value = {"last": 100.0}
        mock_tradier.get_expirations.return_value = ["2026-01-23"]  # Only 1 Friday
        mock_tradier.get_options_chain.return_value = [
            {"strike": 100, "option_type": "call", "bid": 2.0, "ask": 2.2},
            {"strike": 100, "option_type": "put", "bid": 1.8, "ask": 2.0},
        ]

        # Mock VRP cache (no cached data)
        mock_vrp_cache = MagicMock()
        mock_vrp_cache.get_vrp.return_value = None
        mock_vrp_cache.save_vrp = MagicMock()

        # Mock historical repo with intraday_move_pct (what the function actually uses)
        mock_repo = MagicMock()
        mock_repo.get_moves.return_value = [
            {"earnings_date": "2025-10-22", "intraday_move_pct": 3.5, "direction": "UP"},
            {"earnings_date": "2025-07-22", "intraday_move_pct": 2.8, "direction": "DOWN"},
            {"earnings_date": "2025-04-22", "intraday_move_pct": 3.0, "direction": "UP"},
            {"earnings_date": "2025-01-22", "intraday_move_pct": 2.5, "direction": "DOWN"},
        ]

        # Mock sentiment cache
        mock_sentiment_cache = MagicMock()
        mock_sentiment_cache.get_sentiment.return_value = None

        # Patch settings module
        with patch("src.main.settings", mock_settings):
            from src.main import _analyze_single_ticker

            semaphore = asyncio.Semaphore(5)
            result = await _analyze_single_ticker(
                ticker="NOWEEKLY",
                earnings_date="2026-01-22",
                name="No Weekly Corp",
                repo=mock_repo,
                tradier=mock_tradier,
                sentiment_cache=mock_sentiment_cache,
                vrp_cache=mock_vrp_cache,
                semaphore=semaphore,
                filter_mode="filter"
            )

            # Should return None (filtered out)
            assert result is None

    @pytest.mark.asyncio
    async def test_filter_mode_warn_includes_warning(self):
        """filter_mode='warn' should include ticker with warning."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        # Mock settings to require weekly options
        mock_settings = MagicMock()
        mock_settings.require_weekly_options = True
        mock_settings.VRP_DISCOVERY = 1.8
        mock_settings.account_size = 100000
        mock_settings.DEFAULT_POSITION_SIZE = 100

        # Mock tradier client - only 1 Friday (no weeklies)
        mock_tradier = AsyncMock()
        mock_tradier.get_quote.return_value = {"last": 100.0}
        mock_tradier.get_expirations.return_value = ["2026-01-23"]  # Only 1 Friday
        mock_tradier.get_options_chain.return_value = [
            {"strike": 100, "option_type": "call", "bid": 2.0, "ask": 2.2},
            {"strike": 100, "option_type": "put", "bid": 1.8, "ask": 2.0},
        ]

        # Mock VRP cache (no cached data)
        mock_vrp_cache = MagicMock()
        mock_vrp_cache.get_vrp.return_value = None
        mock_vrp_cache.save_vrp = MagicMock()

        # Mock historical repo - low historical move to create high VRP
        mock_repo = MagicMock()
        mock_repo.get_moves.return_value = [
            {"earnings_date": "2025-10-22", "intraday_move_pct": 2.0, "direction": "UP"},
            {"earnings_date": "2025-07-22", "intraday_move_pct": 2.5, "direction": "DOWN"},
            {"earnings_date": "2025-04-22", "intraday_move_pct": 1.8, "direction": "UP"},
            {"earnings_date": "2025-01-22", "intraday_move_pct": 2.2, "direction": "DOWN"},
        ]

        # Mock sentiment cache
        mock_sentiment_cache = MagicMock()
        mock_sentiment_cache.get_sentiment.return_value = None

        with patch("src.main.settings", mock_settings):
            from src.main import _analyze_single_ticker

            semaphore = asyncio.Semaphore(5)
            result = await _analyze_single_ticker(
                ticker="WARNME",
                earnings_date="2026-01-22",
                name="Warn Me Corp",
                repo=mock_repo,
                tradier=mock_tradier,
                sentiment_cache=mock_sentiment_cache,
                vrp_cache=mock_vrp_cache,
                semaphore=semaphore,
                filter_mode="warn"
            )

            # Should return result with warning
            assert result is not None
            assert result["has_weekly_options"] is False
            assert result["weekly_warning"] is not None
            assert "No weekly options" in result["weekly_warning"]

    @pytest.mark.asyncio
    async def test_vrp_cache_stores_weekly_status(self):
        """VRP cache should include weekly options status."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        # Mock settings (weekly filter disabled)
        mock_settings = MagicMock()
        mock_settings.require_weekly_options = False
        mock_settings.VRP_DISCOVERY = 1.8
        mock_settings.account_size = 100000
        mock_settings.DEFAULT_POSITION_SIZE = 100

        # Mock tradier client - 3 Fridays (has weeklies)
        mock_tradier = AsyncMock()
        mock_tradier.get_quote.return_value = {"last": 100.0}
        mock_tradier.get_expirations.return_value = [
            "2026-01-23", "2026-01-30", "2026-02-06"  # 3 Fridays
        ]
        mock_tradier.get_options_chain.return_value = [
            {"strike": 100, "option_type": "call", "bid": 2.0, "ask": 2.2},
            {"strike": 100, "option_type": "put", "bid": 1.8, "ask": 2.0},
        ]

        # Mock VRP cache
        mock_vrp_cache = MagicMock()
        mock_vrp_cache.get_vrp.return_value = None
        mock_vrp_cache.save_vrp = MagicMock()

        # Mock historical repo - low historical move to create high VRP
        mock_repo = MagicMock()
        mock_repo.get_moves.return_value = [
            {"earnings_date": "2025-10-22", "intraday_move_pct": 2.0, "direction": "UP"},
            {"earnings_date": "2025-07-22", "intraday_move_pct": 2.5, "direction": "DOWN"},
            {"earnings_date": "2025-04-22", "intraday_move_pct": 1.8, "direction": "UP"},
            {"earnings_date": "2025-01-22", "intraday_move_pct": 2.2, "direction": "DOWN"},
        ]

        # Mock sentiment cache
        mock_sentiment_cache = MagicMock()
        mock_sentiment_cache.get_sentiment.return_value = None

        with patch("src.main.settings", mock_settings):
            from src.main import _analyze_single_ticker

            semaphore = asyncio.Semaphore(5)
            await _analyze_single_ticker(
                ticker="CACHED",
                earnings_date="2026-01-22",
                name="Cached Corp",
                repo=mock_repo,
                tradier=mock_tradier,
                sentiment_cache=mock_sentiment_cache,
                vrp_cache=mock_vrp_cache,
                semaphore=semaphore,
                filter_mode="filter"
            )

            # Verify save_vrp was called with weekly status
            mock_vrp_cache.save_vrp.assert_called_once()
            call_args = mock_vrp_cache.save_vrp.call_args
            saved_data = call_args[0][2]  # Third positional arg is the data dict

            assert "has_weekly_options" in saved_data
            assert saved_data["has_weekly_options"] is True
            assert "weekly_reason" in saved_data
            assert "Friday" in saved_data["weekly_reason"]
