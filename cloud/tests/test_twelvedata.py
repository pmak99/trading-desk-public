# 5.0/tests/test_twelvedata.py
"""Tests for TwelveDataClient."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from src.integrations.twelvedata import TwelveDataClient


@pytest.fixture
def client():
    return TwelveDataClient(api_key="test_key")


@pytest.fixture
def client_no_key():
    return TwelveDataClient(api_key="")


@pytest.mark.asyncio
async def test_get_stock_history(client):
    """Fetch stock price history."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "values": [
            {"datetime": "2025-01-15", "open": "150.0", "high": "152.0",
             "low": "149.0", "close": "151.0", "volume": "1000000"},
            {"datetime": "2025-01-14", "open": "148.0", "high": "150.0",
             "low": "147.0", "close": "150.0", "volume": "900000"},
        ]
    }

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response

    with patch.object(client, "_get_client", return_value=mock_client):
        result = await client.get_stock_history("AAPL", period="5d")

    assert result is not None
    assert "Close" in result
    assert len(result["Close"]) == 2
    assert result["Close"]["2025-01-15"] == 151.0


@pytest.mark.asyncio
async def test_get_stock_history_empty(client):
    """Handle empty response."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"values": []}

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response

    with patch.object(client, "_get_client", return_value=mock_client):
        result = await client.get_stock_history("INVALID", period="1mo")

    # Empty values returns None
    assert result is None or result == {"Open": {}, "High": {}, "Low": {}, "Close": {}, "Volume": {}}


@pytest.mark.asyncio
async def test_get_current_price(client):
    """Get current stock price."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"price": "175.50"}

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response

    with patch.object(client, "_get_client", return_value=mock_client):
        result = await client.get_current_price("AAPL")

    assert result == 175.50


@pytest.mark.asyncio
async def test_get_current_price_no_data(client):
    """Handle missing price data."""
    mock_response = MagicMock()
    mock_response.json.return_value = {}

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response

    with patch.object(client, "_get_client", return_value=mock_client):
        result = await client.get_current_price("INVALID")

    assert result is None


@pytest.mark.asyncio
async def test_get_quote(client):
    """Get full quote data."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "symbol": "AAPL",
        "close": "175.50",
        "open": "174.00",
        "high": "176.00",
        "low": "173.50",
        "volume": "50000000",
        "previous_close": "173.00",
        "change": "2.50",
        "percent_change": "1.45",
    }

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response

    with patch.object(client, "_get_client", return_value=mock_client):
        result = await client.get_quote("AAPL")

    assert result is not None
    assert result["symbol"] == "AAPL"
    assert result["price"] == 175.50
    assert result["percent_change"] == 1.45


@pytest.mark.asyncio
async def test_api_error_handling(client):
    """Handle API error response."""
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "status": "error",
        "message": "Invalid symbol"
    }

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response

    with patch.object(client, "_get_client", return_value=mock_client):
        result = await client.get_current_price("INVALID_SYMBOL")

    assert result is None


@pytest.mark.asyncio
async def test_no_api_key(client_no_key):
    """Return None when no API key configured."""
    result = await client_no_key.get_current_price("AAPL")
    assert result is None


@pytest.mark.asyncio
async def test_close_client(client):
    """Test client cleanup."""
    mock_client = AsyncMock()
    client._client = mock_client

    await client.close()

    mock_client.aclose.assert_called_once()
    assert client._client is None
