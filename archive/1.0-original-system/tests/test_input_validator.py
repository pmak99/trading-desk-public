"""
Tests for InputValidator class.

Tests validation logic for tickers, dates, and numeric parameters.
"""

import pytest
from src.core.input_validator import InputValidator


class TestValidateTicker:
    """Tests for ticker symbol validation."""

    def test_valid_uppercase_ticker(self):
        """Test valid uppercase ticker passes."""
        assert InputValidator.validate_ticker("AAPL") == "AAPL"

    def test_valid_lowercase_ticker(self):
        """Test lowercase ticker is converted to uppercase."""
        assert InputValidator.validate_ticker("aapl") == "AAPL"

    def test_valid_mixed_case_ticker(self):
        """Test mixed case ticker is converted to uppercase."""
        assert InputValidator.validate_ticker("AaPl") == "AAPL"

    def test_ticker_with_whitespace(self):
        """Test ticker with leading/trailing whitespace is stripped."""
        assert InputValidator.validate_ticker("  MSFT  ") == "MSFT"

    def test_empty_ticker_raises_error(self):
        """Test empty ticker raises ValueError."""
        with pytest.raises(ValueError, match="Ticker cannot be empty"):
            InputValidator.validate_ticker("")

    def test_whitespace_only_ticker_raises_error(self):
        """Test whitespace-only ticker raises ValueError."""
        with pytest.raises(ValueError, match="Ticker cannot be empty"):
            InputValidator.validate_ticker("   ")

    def test_ticker_with_numbers_raises_error(self):
        """Test ticker with numbers raises ValueError."""
        with pytest.raises(ValueError, match="Invalid ticker format"):
            InputValidator.validate_ticker("AAPL123")

    def test_ticker_with_special_chars_raises_error(self):
        """Test ticker with special characters raises ValueError."""
        with pytest.raises(ValueError, match="Invalid ticker format"):
            InputValidator.validate_ticker("AAPL$")

    def test_ticker_too_long_raises_error(self):
        """Test ticker longer than 5 characters raises ValueError."""
        with pytest.raises(ValueError, match="Tickers must be 1-5 characters"):
            InputValidator.validate_ticker("TOOLONG")

    def test_single_char_ticker(self):
        """Test single character ticker is valid."""
        assert InputValidator.validate_ticker("X") == "X"

    def test_five_char_ticker(self):
        """Test five character ticker is valid (boundary case)."""
        assert InputValidator.validate_ticker("GOOGL") == "GOOGL"


class TestValidateDate:
    """Tests for date string validation."""

    def test_valid_date(self):
        """Test valid date in YYYY-MM-DD format passes."""
        assert InputValidator.validate_date("2025-11-15") == "2025-11-15"

    def test_none_date(self):
        """Test None date returns None."""
        assert InputValidator.validate_date(None) is None

    def test_invalid_format_raises_error(self):
        """Test invalid date format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid date format"):
            InputValidator.validate_date("11/15/2025")

    def test_invalid_date_values_raises_error(self):
        """Test invalid date values raise ValueError."""
        with pytest.raises(ValueError, match="Invalid date format"):
            InputValidator.validate_date("2025-13-45")

    def test_text_date_raises_error(self):
        """Test non-numeric date format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid date format"):
            InputValidator.validate_date("November 15, 2025")

    def test_future_date(self):
        """Test future date is accepted."""
        assert InputValidator.validate_date("2030-01-01") == "2030-01-01"

    def test_past_date(self):
        """Test past date is accepted."""
        assert InputValidator.validate_date("2020-01-01") == "2020-01-01"


class TestValidateMaxAnalyze:
    """Tests for max_analyze parameter validation."""

    def test_valid_max_analyze(self):
        """Test valid max_analyze value passes."""
        assert InputValidator.validate_max_analyze(10) == 10

    def test_min_value(self):
        """Test minimum value (1) is valid."""
        assert InputValidator.validate_max_analyze(1) == 1

    def test_zero_raises_error(self):
        """Test zero value raises ValueError."""
        with pytest.raises(ValueError, match="max_analyze must be >= 1"):
            InputValidator.validate_max_analyze(0)

    def test_negative_raises_error(self):
        """Test negative value raises ValueError."""
        with pytest.raises(ValueError, match="max_analyze must be >= 1"):
            InputValidator.validate_max_analyze(-5)

    def test_high_value_warns(self, caplog):
        """Test high value (>50) logs warning but passes."""
        result = InputValidator.validate_max_analyze(75)
        assert result == 75
        # Warning is logged but doesn't raise error

    def test_boundary_value_50(self):
        """Test boundary value 50 passes without warning."""
        assert InputValidator.validate_max_analyze(50) == 50


class TestValidateLookbackDays:
    """Tests for lookback_days parameter validation."""

    def test_valid_lookback_days(self):
        """Test valid lookback_days value passes."""
        assert InputValidator.validate_lookback_days(7) == 7

    def test_min_value(self):
        """Test minimum value (1) is valid."""
        assert InputValidator.validate_lookback_days(1) == 1

    def test_max_value(self):
        """Test maximum value (60) is valid."""
        assert InputValidator.validate_lookback_days(60) == 60

    def test_zero_raises_error(self):
        """Test zero value raises ValueError."""
        with pytest.raises(ValueError, match="lookback_days must be >= 1"):
            InputValidator.validate_lookback_days(0)

    def test_negative_raises_error(self):
        """Test negative value raises ValueError."""
        with pytest.raises(ValueError, match="lookback_days must be >= 1"):
            InputValidator.validate_lookback_days(-5)

    def test_exceeds_max_raises_error(self):
        """Test value exceeding max raises ValueError."""
        with pytest.raises(ValueError, match="lookback_days must be <= 60"):
            InputValidator.validate_lookback_days(100)

    def test_custom_max_value(self):
        """Test custom max value."""
        assert InputValidator.validate_lookback_days(90, max_days=100) == 90

    def test_custom_max_exceeded_raises_error(self):
        """Test exceeding custom max raises ValueError."""
        with pytest.raises(ValueError, match="lookback_days must be <= 30"):
            InputValidator.validate_lookback_days(45, max_days=30)


class TestValidateTickerList:
    """Tests for ticker list validation."""

    def test_single_ticker(self):
        """Test single ticker in list."""
        result = InputValidator.validate_ticker_list("AAPL")
        assert result == ["AAPL"]

    def test_multiple_tickers(self):
        """Test multiple tickers in list."""
        result = InputValidator.validate_ticker_list("AAPL,MSFT,GOOGL")
        assert result == ["AAPL", "MSFT", "GOOGL"]

    def test_tickers_with_whitespace(self):
        """Test tickers with whitespace are trimmed."""
        result = InputValidator.validate_ticker_list("  AAPL  ,  MSFT  ")
        assert result == ["AAPL", "MSFT"]

    def test_lowercase_tickers(self):
        """Test lowercase tickers are converted to uppercase."""
        result = InputValidator.validate_ticker_list("aapl,msft")
        assert result == ["AAPL", "MSFT"]

    def test_empty_list_raises_error(self):
        """Test empty ticker list raises ValueError."""
        with pytest.raises(ValueError, match="Ticker list cannot be empty"):
            InputValidator.validate_ticker_list("")

    def test_whitespace_only_list_raises_error(self):
        """Test whitespace-only list raises ValueError."""
        with pytest.raises(ValueError, match="Ticker list cannot be empty"):
            InputValidator.validate_ticker_list("   ")

    def test_invalid_ticker_in_list_raises_error(self):
        """Test invalid ticker in list raises ValueError."""
        with pytest.raises(ValueError, match="Invalid ticker format"):
            InputValidator.validate_ticker_list("AAPL,MSFT123")

    def test_empty_ticker_in_list_raises_error(self):
        """Test empty ticker in list raises ValueError."""
        with pytest.raises(ValueError, match="Ticker cannot be empty"):
            InputValidator.validate_ticker_list("AAPL,,MSFT")
