"""Tests for centralized ticker validation."""

import pytest

from src.domain.ticker import (
    validate_ticker,
    normalize_ticker,
    safe_normalize_ticker,
    resolve_alias,
    InvalidTickerError,
    TICKER_ALIASES,
)


class TestValidateTicker:
    """Tests for validate_ticker function."""

    def test_valid_simple_ticker(self):
        """Standard 1-4 letter tickers are valid."""
        assert validate_ticker("AAPL") is True
        assert validate_ticker("IBM") is True
        assert validate_ticker("A") is True
        assert validate_ticker("NVDA") is True

    def test_valid_ticker_with_suffix(self):
        """Tickers with .XX suffix are valid."""
        assert validate_ticker("BRK.B") is True
        assert validate_ticker("BRK.A") is True

    def test_invalid_empty_ticker(self):
        """Empty ticker is invalid."""
        assert validate_ticker("") is False
        assert validate_ticker(None) is False

    def test_invalid_lowercase_ticker(self):
        """Lowercase tickers are invalid (caller should uppercase first)."""
        assert validate_ticker("aapl") is False
        assert validate_ticker("Aapl") is False

    def test_invalid_too_long(self):
        """Tickers over 5 chars are invalid."""
        assert validate_ticker("ABCDEF") is False

    def test_invalid_numbers(self):
        """Tickers with numbers are invalid."""
        assert validate_ticker("ABC1") is False
        assert validate_ticker("123") is False

    def test_invalid_special_chars(self):
        """Tickers with special chars (except .) are invalid."""
        assert validate_ticker("AB-C") is False
        assert validate_ticker("AB_C") is False


class TestNormalizeTicker:
    """Tests for normalize_ticker function."""

    def test_normalize_uppercase(self):
        """Converts lowercase to uppercase."""
        assert normalize_ticker("aapl") == "AAPL"
        assert normalize_ticker("Nvda") == "NVDA"

    def test_normalize_strips_whitespace(self):
        """Strips leading/trailing whitespace."""
        assert normalize_ticker("  AAPL  ") == "AAPL"
        assert normalize_ticker("\tNVDA\n") == "NVDA"

    def test_normalize_company_alias(self):
        """Resolves company name aliases."""
        assert normalize_ticker("NIKE") == "NKE"
        assert normalize_ticker("nike") == "NKE"
        assert normalize_ticker("GOOGLE") == "GOOGL"
        assert normalize_ticker("FACEBOOK") == "META"
        assert normalize_ticker("BERKSHIRE") == "BRK.B"

    def test_normalize_already_valid(self):
        """Valid tickers pass through unchanged."""
        assert normalize_ticker("AAPL") == "AAPL"
        assert normalize_ticker("BRK.B") == "BRK.B"

    def test_normalize_invalid_raises(self):
        """Invalid tickers raise InvalidTickerError."""
        with pytest.raises(InvalidTickerError):
            normalize_ticker("")

        with pytest.raises(InvalidTickerError):
            normalize_ticker("ABC123")

        with pytest.raises(InvalidTickerError):
            normalize_ticker("TOOLONGNAME")

    def test_invalid_ticker_error_contains_ticker(self):
        """InvalidTickerError includes the problematic ticker."""
        with pytest.raises(InvalidTickerError) as exc_info:
            normalize_ticker("BADTICKER123")
        assert "BADTICKER123" in str(exc_info.value)


class TestSafeNormalizeTicker:
    """Tests for safe_normalize_ticker function."""

    def test_valid_returns_ticker_and_no_error(self):
        """Valid tickers return (ticker, None)."""
        ticker, error = safe_normalize_ticker("AAPL")
        assert ticker == "AAPL"
        assert error is None

    def test_invalid_returns_none_and_error(self):
        """Invalid tickers return (None, error_message)."""
        ticker, error = safe_normalize_ticker("BADTICKER123")
        assert ticker is None
        assert error is not None
        assert "BADTICKER123" in error


class TestResolveAlias:
    """Tests for resolve_alias function."""

    def test_known_alias(self):
        """Known aliases return correct ticker."""
        assert resolve_alias("NIKE") == "NKE"
        assert resolve_alias("nike") == "NKE"  # Case-insensitive
        assert resolve_alias("Google") == "GOOGL"

    def test_unknown_alias(self):
        """Unknown names return None."""
        assert resolve_alias("AAPL") is None
        assert resolve_alias("RANDOMCOMPANY") is None


class TestTickerAliases:
    """Tests for TICKER_ALIASES constant."""

    def test_common_aliases_present(self):
        """Common company names are mapped."""
        assert "NIKE" in TICKER_ALIASES
        assert "GOOGLE" in TICKER_ALIASES
        assert "FACEBOOK" in TICKER_ALIASES
        assert "BERKSHIRE" in TICKER_ALIASES

    def test_alias_values_are_valid_tickers(self):
        """All alias values are valid ticker symbols."""
        for company, ticker in TICKER_ALIASES.items():
            assert validate_ticker(ticker), f"{company} maps to invalid ticker {ticker}"
