"""Tests for Finnhub API client."""

import pytest
from unittest.mock import AsyncMock, patch

from src.integrations.finnhub import FinnhubClient


@pytest.fixture
def client():
    return FinnhubClient(api_key="test-key")


@pytest.mark.asyncio
async def test_get_recommendations_success(client):
    """Parse analyst recommendation data correctly."""
    mock_data = [
        {
            "strongBuy": 10, "buy": 15, "hold": 5, "sell": 2, "strongSell": 1,
            "period": "2026-02-01", "symbol": "NVDA",
        }
    ]
    client._request = AsyncMock(return_value=mock_data)

    result = await client.get_recommendations("NVDA")

    assert result["strongBuy"] == 10
    assert result["buy"] == 15
    assert result["hold"] == 5
    assert result["sell"] == 2
    assert result["strongSell"] == 1
    assert result["period"] == "2026-02-01"


@pytest.mark.asyncio
async def test_get_recommendations_empty(client):
    """Empty response returns error dict."""
    client._request = AsyncMock(return_value=[])

    result = await client.get_recommendations("ZZZZZ")

    assert "error" in result


@pytest.mark.asyncio
async def test_get_company_news_success(client):
    """Parse news articles and enforce limit."""
    mock_data = [
        {"headline": f"Article {i}", "summary": f"Summary {i}", "source": "Reuters", "datetime": 1234567890}
        for i in range(20)
    ]
    client._request = AsyncMock(return_value=mock_data)

    result = await client.get_company_news("NVDA", "2026-02-01", "2026-02-17", limit=5)

    assert len(result) == 5
    assert result[0]["headline"] == "Article 0"
    assert result[0]["source"] == "Reuters"


@pytest.mark.asyncio
async def test_get_company_news_error(client):
    """API error returns empty list."""
    client._request = AsyncMock(return_value={"error": "API error: 403"})

    result = await client.get_company_news("NVDA", "2026-02-01", "2026-02-17")

    assert result == []


@pytest.mark.asyncio
async def test_api_error_returns_error_dict(client):
    """HTTP errors handled gracefully."""
    client._request = AsyncMock(return_value={"error": "API error: 500"})

    result = await client.get_recommendations("NVDA")

    assert "error" in result
    assert "500" in result["error"]


@pytest.mark.asyncio
async def test_get_recommendations_not_list(client):
    """Non-list response returns error dict."""
    client._request = AsyncMock(return_value={"some": "object"})

    result = await client.get_recommendations("NVDA")

    assert "error" in result


@pytest.mark.asyncio
async def test_get_company_news_not_list(client):
    """Non-list response returns empty list."""
    client._request = AsyncMock(return_value="invalid")

    result = await client.get_company_news("NVDA", "2026-02-01", "2026-02-17")

    assert result == []


@pytest.mark.asyncio
async def test_missing_api_key():
    """Empty API key still creates client without error."""
    client = FinnhubClient(api_key="")
    # Client is created, but requests will fail at API level
    assert client.api_key == ""


# ── Earnings calendar ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_earnings_calendar_returns_symbol_and_report_date(client):
    """Calendar response maps Finnhub fields to expected keys."""
    client._request = AsyncMock(return_value={
        "earningsCalendar": [
            {"date": "2026-06-01", "symbol": "AAPL", "hour": "amc", "epsEstimate": 1.5},
            {"date": "2026-06-02", "symbol": "MSFT", "hour": "bmo", "epsEstimate": 2.1},
        ]
    })

    result = await client.get_earnings_calendar()

    assert len(result) == 2
    assert result[0]["symbol"] == "AAPL"
    assert result[0]["report_date"] == "2026-06-01"
    assert result[1]["symbol"] == "MSFT"
    assert result[1]["report_date"] == "2026-06-02"


@pytest.mark.asyncio
async def test_get_earnings_calendar_empty_returns_empty_list(client):
    """Empty earningsCalendar key returns empty list."""
    client._request = AsyncMock(return_value={"earningsCalendar": []})

    result = await client.get_earnings_calendar()

    assert result == []


@pytest.mark.asyncio
async def test_get_earnings_calendar_api_error_returns_empty_list(client):
    """API error returns empty list, not an exception."""
    client._request = AsyncMock(return_value={"error": "API error: 403"})

    result = await client.get_earnings_calendar()

    assert result == []


@pytest.mark.asyncio
async def test_get_earnings_calendar_missing_key_returns_empty_list(client):
    """Response without earningsCalendar key returns empty list."""
    client._request = AsyncMock(return_value={})

    result = await client.get_earnings_calendar()

    assert result == []


@pytest.mark.asyncio
async def test_get_earnings_calendar_symbol_filter(client):
    """symbol param is passed through to the API request."""
    client._request = AsyncMock(return_value={"earningsCalendar": []})

    await client.get_earnings_calendar(symbol="NVDA")

    call_args = client._request.call_args
    assert call_args[0][1]["symbol"] == "NVDA"


@pytest.mark.asyncio
async def test_get_earnings_for_date_returns_matching_day(client):
    """get_earnings_for_date passes identical from/to dates."""
    client._request = AsyncMock(return_value={"earningsCalendar": [
        {"date": "2026-06-05", "symbol": "AAPL", "hour": "amc", "epsEstimate": None},
    ]})

    result = await client.get_earnings_for_date("2026-06-05")

    assert len(result) == 1
    assert result[0]["symbol"] == "AAPL"
    assert result[0]["report_date"] == "2026-06-05"
