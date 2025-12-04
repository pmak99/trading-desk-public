"""
Unit tests for EarningsDateValidator.

Tests the cross-reference system that validates earnings dates from
multiple sources (Yahoo Finance, Alpha Vantage).
"""

import pytest
from datetime import date, datetime, timedelta
from unittest.mock import Mock, patch

from src.application.services.earnings_date_validator import (
    EarningsDateValidator,
    EarningsDateInfo,
    ValidationResult,
    EarningsSource,
)
from src.domain.types import EarningsTiming
from src.domain.errors import Result, AppError, ErrorCode


class TestEarningsDateValidator:
    """Test suite for EarningsDateValidator."""

    @pytest.fixture
    def mock_yahoo_finance(self):
        """Mock Yahoo Finance data source."""
        mock = Mock()
        mock.get_next_earnings_date = Mock()
        return mock

    @pytest.fixture
    def mock_alpha_vantage(self):
        """Mock Alpha Vantage data source."""
        mock = Mock()
        mock.get_earnings_calendar = Mock()
        return mock

    @pytest.fixture
    def validator(self, mock_yahoo_finance, mock_alpha_vantage):
        """Create validator with mocked data sources."""
        return EarningsDateValidator(
            yahoo_finance=mock_yahoo_finance,
            alpha_vantage=mock_alpha_vantage,
            max_date_diff_days=7,
        )

    def test_no_conflict_same_date(self, validator, mock_yahoo_finance, mock_alpha_vantage):
        """Test when both sources agree on the same date."""
        ticker = "AAPL"
        earnings_date = date(2025, 12, 15)
        timing = EarningsTiming.AMC

        # Mock both sources returning same date
        mock_yahoo_finance.get_next_earnings_date.return_value = Result.Ok(
            (earnings_date, timing)
        )
        mock_alpha_vantage.get_earnings_calendar.return_value = Result.Ok(
            [(ticker, earnings_date, timing)]
        )

        result = validator.validate_earnings_date(ticker)

        assert result.is_ok
        validation = result.value
        assert validation.consensus_date == earnings_date
        assert validation.consensus_timing == timing
        assert not validation.has_conflict
        assert len(validation.sources) == 2

    def test_conflict_detected(self, validator, mock_yahoo_finance, mock_alpha_vantage):
        """Test when sources disagree by more than threshold."""
        ticker = "MRVL"
        yf_date = date(2025, 12, 2)
        av_date = date(2025, 12, 10)  # 8 days difference, exceeds 7-day threshold
        timing = EarningsTiming.AMC

        # Mock sources with different dates
        mock_yahoo_finance.get_next_earnings_date.return_value = Result.Ok(
            (yf_date, timing)
        )
        mock_alpha_vantage.get_earnings_calendar.return_value = Result.Ok(
            [(ticker, av_date, timing)]
        )

        result = validator.validate_earnings_date(ticker)

        assert result.is_ok
        validation = result.value
        # Yahoo Finance has higher confidence, should win
        assert validation.consensus_date == yf_date
        assert validation.has_conflict  # Should detect conflict (8 days > 7 day threshold)
        assert len(validation.sources) == 2

    def test_yahoo_finance_priority(self, validator, mock_yahoo_finance, mock_alpha_vantage):
        """Test that Yahoo Finance has higher priority in consensus."""
        ticker = "SNOW"
        yf_date = date(2025, 12, 10)
        av_date = date(2025, 12, 12)
        timing = EarningsTiming.AMC

        # Mock sources with different dates
        mock_yahoo_finance.get_next_earnings_date.return_value = Result.Ok(
            (yf_date, timing)
        )
        mock_alpha_vantage.get_earnings_calendar.return_value = Result.Ok(
            [(ticker, av_date, timing)]
        )

        result = validator.validate_earnings_date(ticker)

        assert result.is_ok
        validation = result.value
        # Yahoo Finance should win due to higher confidence (1.0 vs 0.7)
        assert validation.consensus_date == yf_date

    def test_only_yahoo_finance_available(self, validator, mock_yahoo_finance, mock_alpha_vantage):
        """Test when only Yahoo Finance data is available."""
        ticker = "CRM"
        earnings_date = date(2025, 12, 8)
        timing = EarningsTiming.BMO

        # Yahoo Finance succeeds
        mock_yahoo_finance.get_next_earnings_date.return_value = Result.Ok(
            (earnings_date, timing)
        )
        # Alpha Vantage fails
        mock_alpha_vantage.get_earnings_calendar.return_value = Result.Err(
            AppError(ErrorCode.NODATA, "No data")
        )

        result = validator.validate_earnings_date(ticker)

        assert result.is_ok
        validation = result.value
        assert validation.consensus_date == earnings_date
        assert len(validation.sources) == 1
        assert validation.sources[0].source == EarningsSource.YAHOO_FINANCE

    def test_no_data_from_any_source(self, validator, mock_yahoo_finance, mock_alpha_vantage):
        """Test when no sources return data."""
        ticker = "INVALID"

        # Both sources fail
        mock_yahoo_finance.get_next_earnings_date.return_value = Result.Err(
            AppError(ErrorCode.NODATA, "No data")
        )
        mock_alpha_vantage.get_earnings_calendar.return_value = Result.Err(
            AppError(ErrorCode.NODATA, "No data")
        )

        result = validator.validate_earnings_date(ticker)

        assert result.is_err
        assert "No earnings date found from any source" in result.error.message

    def test_different_timings(self, validator, mock_yahoo_finance, mock_alpha_vantage):
        """Test when sources disagree on timing."""
        ticker = "PATH"
        earnings_date = date(2025, 12, 5)
        yf_timing = EarningsTiming.BMO
        av_timing = EarningsTiming.AMC

        # Mock sources with same date but different timing
        mock_yahoo_finance.get_next_earnings_date.return_value = Result.Ok(
            (earnings_date, yf_timing)
        )
        mock_alpha_vantage.get_earnings_calendar.return_value = Result.Ok(
            [(ticker, earnings_date, av_timing)]
        )

        result = validator.validate_earnings_date(ticker)

        assert result.is_ok
        validation = result.value
        # Yahoo Finance timing should win
        assert validation.consensus_timing == yf_timing

    def test_cache_functionality(self, mock_yahoo_finance):
        """Test that Yahoo Finance caching works correctly."""
        from src.infrastructure.data_sources.yahoo_finance_earnings import (
            YahooFinanceEarnings,
        )

        with patch("src.infrastructure.data_sources.yahoo_finance_earnings.yf") as mock_yf:
            # Setup mock yfinance
            mock_ticker = Mock()
            mock_ticker.calendar = {"Earnings Date": [date(2025, 12, 10)]}
            mock_ticker.earnings_dates = None
            mock_yf.Ticker.return_value = mock_ticker

            fetcher = YahooFinanceEarnings(cache_ttl_hours=1)

            # First call - should hit API
            result1 = fetcher.get_next_earnings_date("AAPL")
            assert result1.is_ok
            assert mock_yf.Ticker.call_count == 1

            # Second call - should use cache
            result2 = fetcher.get_next_earnings_date("AAPL")
            assert result2.is_ok
            assert mock_yf.Ticker.call_count == 1  # Still 1, used cache

            # Results should be identical
            assert result1.value == result2.value

    def test_cache_expiration(self, mock_yahoo_finance):
        """Test that cache expires after TTL."""
        from src.infrastructure.data_sources.yahoo_finance_earnings import (
            YahooFinanceEarnings,
        )

        with patch("src.infrastructure.data_sources.yahoo_finance_earnings.yf") as mock_yf:
            # Setup mock yfinance
            mock_ticker = Mock()
            mock_ticker.calendar = {"Earnings Date": [date(2025, 12, 10)]}
            mock_ticker.earnings_dates = None
            mock_yf.Ticker.return_value = mock_ticker

            # Use 1 second TTL for reliable testing
            fetcher = YahooFinanceEarnings(cache_ttl_hours=1/3600)  # 1 second

            # First call
            result1 = fetcher.get_next_earnings_date("AAPL")
            assert result1.is_ok
            assert mock_yf.Ticker.call_count == 1

            # Wait for cache to expire (1.5 seconds to be safe)
            import time
            time.sleep(1.5)

            # Second call - cache expired, should hit API again
            result2 = fetcher.get_next_earnings_date("AAPL")
            assert result2.is_ok
            assert mock_yf.Ticker.call_count == 2  # Called again

    def test_confidence_weights(self, validator):
        """Test that confidence weights are correct."""
        assert validator.SOURCE_CONFIDENCE[EarningsSource.YAHOO_FINANCE] == 1.0
        assert validator.SOURCE_CONFIDENCE[EarningsSource.EARNINGS_WHISPER] == 0.85
        assert validator.SOURCE_CONFIDENCE[EarningsSource.ALPHA_VANTAGE] == 0.70
        assert validator.SOURCE_CONFIDENCE[EarningsSource.DATABASE] == 0.60

    def test_max_date_diff_threshold(self, validator, mock_yahoo_finance, mock_alpha_vantage):
        """Test that conflict is detected when dates differ beyond threshold."""
        ticker = "TEST"
        yf_date = date(2025, 12, 1)
        av_date = date(2025, 12, 10)  # 9 days difference, > 7 day threshold
        timing = EarningsTiming.AMC

        mock_yahoo_finance.get_next_earnings_date.return_value = Result.Ok(
            (yf_date, timing)
        )
        mock_alpha_vantage.get_earnings_calendar.return_value = Result.Ok(
            [(ticker, av_date, timing)]
        )

        result = validator.validate_earnings_date(ticker)

        assert result.is_ok
        validation = result.value
        assert validation.has_conflict
        # Date difference is 9 days
        date_diff = (validation.sources[1].earnings_date - validation.sources[0].earnings_date).days
        assert abs(date_diff) == 9

    def test_no_conflict_within_threshold(self, validator, mock_yahoo_finance, mock_alpha_vantage):
        """Test that no conflict when dates differ within threshold."""
        ticker = "TEST"
        yf_date = date(2025, 12, 1)
        av_date = date(2025, 12, 3)  # 2 days difference, < 7 day threshold
        timing = EarningsTiming.AMC

        mock_yahoo_finance.get_next_earnings_date.return_value = Result.Ok(
            (yf_date, timing)
        )
        mock_alpha_vantage.get_earnings_calendar.return_value = Result.Ok(
            [(ticker, av_date, timing)]
        )

        result = validator.validate_earnings_date(ticker)

        assert result.is_ok
        validation = result.value
        # Should NOT detect conflict since difference (2 days) <= threshold (7 days)
        assert not validation.has_conflict
        # Yahoo Finance should still win due to higher confidence
        assert validation.consensus_date == yf_date


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
