"""Unit tests for earnings scanner."""

import pytest
from datetime import datetime, timedelta
from src.earnings_scanner import EarningsScanner


@pytest.fixture
def scanner():
    """Create scanner instance."""
    return EarningsScanner(tickers=["NVDA", "TSLA", "AAPL"])


def test_scanner_initialization():
    """Test scanner initializes with default tickers."""
    scanner = EarningsScanner()
    assert len(scanner.tickers) > 0
    assert isinstance(scanner.tickers, list)


def test_scanner_custom_tickers():
    """Test scanner with custom ticker list."""
    custom_tickers = ["NVDA", "TSLA"]
    scanner = EarningsScanner(tickers=custom_tickers)
    assert scanner.tickers == custom_tickers


def test_get_earnings_candidates(scanner):
    """Test getting earnings candidates."""
    candidates = scanner.get_earnings_candidates(days_ahead=30)

    assert isinstance(candidates, list)

    if len(candidates) > 0:
        candidate = candidates[0]
        assert 'ticker' in candidate
        assert 'earnings_date' in candidate
        assert 'days_until' in candidate
        assert 'market_cap' in candidate
        assert isinstance(candidate['earnings_date'], datetime)


def test_earnings_sorted_by_date(scanner):
    """Test that earnings are sorted by days_until."""
    candidates = scanner.get_earnings_candidates(days_ahead=30)

    if len(candidates) > 1:
        days_until = [c['days_until'] for c in candidates]
        assert days_until == sorted(days_until)


def test_market_cap_filter():
    """Test market cap filtering."""
    scanner = EarningsScanner(tickers=["NVDA"])

    # High market cap requirement
    candidates_high = scanner.get_earnings_candidates(
        days_ahead=30,
        min_market_cap=100e9  # 100B
    )

    # Low market cap requirement
    candidates_low = scanner.get_earnings_candidates(
        days_ahead=30,
        min_market_cap=1e9  # 1B
    )

    # More candidates with lower requirement
    assert len(candidates_low) >= len(candidates_high)


def test_get_earnings_for_ticker(scanner):
    """Test getting earnings for specific ticker."""
    result = scanner.get_earnings_for_ticker("NVDA")

    if result:  # May be None if no earnings scheduled
        assert result['ticker'] == "NVDA"
        assert 'earnings_date' in result
        assert 'market_cap' in result


def test_invalid_ticker(scanner):
    """Test handling invalid ticker."""
    result = scanner.get_earnings_for_ticker("INVALID_TICKER_XYZ")
    assert result is None


def test_empty_ticker_list():
    """Test with empty ticker list."""
    scanner = EarningsScanner(tickers=[])
    candidates = scanner.get_earnings_candidates()
    assert candidates == []
