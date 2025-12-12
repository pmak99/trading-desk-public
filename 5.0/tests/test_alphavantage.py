# 5.0/tests/test_alphavantage.py
import pytest
from unittest.mock import AsyncMock, patch
from src.integrations.alphavantage import AlphaVantageClient


@pytest.fixture
def client():
    return AlphaVantageClient(api_key="test_key")


@pytest.mark.asyncio
async def test_get_earnings_calendar(client):
    """Fetch earnings calendar from Alpha Vantage."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.text = """symbol,name,reportDate,fiscalDateEnding,estimate,currency
AAPL,Apple Inc,2025-01-30,2024-12-31,2.35,USD
MSFT,Microsoft Corp,2025-01-29,2024-12-31,3.12,USD"""
    mock_response.raise_for_status = AsyncMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

        result = await client.get_earnings_calendar()

    assert len(result) == 2
    assert result[0]["symbol"] == "AAPL"
    assert result[0]["report_date"] == "2025-01-30"


@pytest.mark.asyncio
async def test_get_earnings_calendar_by_symbol(client):
    """Filter earnings calendar by symbol."""
    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.text = """symbol,name,reportDate,fiscalDateEnding,estimate,currency
NVDA,NVIDIA Corp,2025-02-26,2025-01-31,0.89,USD"""
    mock_response.raise_for_status = AsyncMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get.return_value = mock_response

        result = await client.get_earnings_calendar(symbol="NVDA")

    assert len(result) == 1
    assert result[0]["symbol"] == "NVDA"


@pytest.mark.asyncio
async def test_parse_csv_response(client):
    """Parse CSV earnings calendar response."""
    csv_text = """symbol,name,reportDate,fiscalDateEnding,estimate,currency
TSLA,Tesla Inc,2025-01-29,2024-12-31,0.72,USD"""

    result = client._parse_earnings_csv(csv_text)

    assert len(result) == 1
    assert result[0]["symbol"] == "TSLA"
    assert result[0]["name"] == "Tesla Inc"
    assert result[0]["report_date"] == "2025-01-29"
    assert result[0]["estimate"] == "0.72"


@pytest.mark.asyncio
async def test_empty_calendar(client):
    """Empty calendar returns empty list."""
    csv_text = """symbol,name,reportDate,fiscalDateEnding,estimate,currency"""

    result = client._parse_earnings_csv(csv_text)

    assert result == []
