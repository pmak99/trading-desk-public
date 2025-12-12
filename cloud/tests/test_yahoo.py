# 5.0/tests/test_yahoo.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.integrations.yahoo import YahooFinanceClient


@pytest.fixture
def client():
    return YahooFinanceClient()


@pytest.mark.asyncio
async def test_get_stock_history(client):
    """Fetch stock price history."""
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = MagicMock()
    mock_ticker.history.return_value.to_dict.return_value = {
        "Close": {0: 150.0, 1: 152.0, 2: 149.0}
    }

    with patch("yfinance.Ticker", return_value=mock_ticker):
        result = await client.get_stock_history("AAPL", period="5d")

    assert "Close" in result
    assert len(result["Close"]) == 3


@pytest.mark.asyncio
async def test_get_current_price(client):
    """Get current stock price."""
    mock_ticker = MagicMock()
    mock_ticker.info = {"regularMarketPrice": 175.50}

    with patch("yfinance.Ticker", return_value=mock_ticker):
        result = await client.get_current_price("AAPL")

    assert result == 175.50


@pytest.mark.asyncio
async def test_get_current_price_fallback(client):
    """Use previousClose if regularMarketPrice unavailable."""
    mock_ticker = MagicMock()
    mock_ticker.info = {"previousClose": 174.00}

    with patch("yfinance.Ticker", return_value=mock_ticker):
        result = await client.get_current_price("AAPL")

    assert result == 174.00


@pytest.mark.asyncio
async def test_get_earnings_dates(client):
    """Fetch earnings dates for symbol."""
    mock_ticker = MagicMock()
    mock_calendar = MagicMock()
    mock_calendar.to_dict.return_value = {
        "Earnings Date": {0: "2025-01-30"}
    }
    mock_ticker.calendar = mock_calendar

    with patch("yfinance.Ticker", return_value=mock_ticker):
        result = await client.get_earnings_info("AAPL")

    assert "calendar" in result
