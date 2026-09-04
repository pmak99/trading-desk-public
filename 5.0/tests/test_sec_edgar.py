"""
Tests for SecEdgarClient — NT filing detection via SEC EDGAR free API.

TDD: tests written before implementation.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def client():
    from src.integrations.sec_edgar import SecEdgarClient
    return SecEdgarClient()


def _mock_response(data: dict, status_code: int = 200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = data
    if status_code >= 400:
        import httpx
        mock.raise_for_status.side_effect = httpx.HTTPStatusError(
            message="error", request=MagicMock(), response=mock
        )
    else:
        mock.raise_for_status = MagicMock()
    return mock


NT_FILING_RESPONSE = {
    "hits": {
        "total": {"value": 1, "relation": "eq"},
        "hits": [
            {
                "_source": {
                    "form_type": "NT 10-Q",
                    "file_date": "2026-05-10",
                    "entity_name": "ACME Corp",
                    "period_of_report": "2026-03-31",
                }
            }
        ]
    }
}

NO_FILINGS_RESPONSE = {
    "hits": {
        "total": {"value": 0, "relation": "eq"},
        "hits": []
    }
}


@pytest.mark.asyncio
async def test_get_nt_filings_returns_list_when_filing_exists(client):
    """Should return list of NT filings when found."""
    with patch.object(client._client, "get", new_callable=AsyncMock,
                      return_value=_mock_response(NT_FILING_RESPONSE)):
        result = await client.get_nt_filings("ACME")
    assert len(result) == 1
    assert result[0]["_source"]["form_type"] == "NT 10-Q"


@pytest.mark.asyncio
async def test_get_nt_filings_returns_empty_list_when_none(client):
    """Should return empty list when no NT filings found."""
    with patch.object(client._client, "get", new_callable=AsyncMock,
                      return_value=_mock_response(NO_FILINGS_RESPONSE)):
        result = await client.get_nt_filings("NVDA")
    assert result == []


@pytest.mark.asyncio
async def test_get_nt_filings_returns_empty_on_http_error(client):
    """Should return empty list on HTTP error (fail gracefully)."""
    with patch.object(client._client, "get", new_callable=AsyncMock,
                      return_value=_mock_response({}, status_code=500)):
        result = await client.get_nt_filings("NVDA")
    assert result == []


@pytest.mark.asyncio
async def test_get_nt_filings_returns_empty_on_connection_error(client):
    """Should return empty list on connection failure."""
    import httpx
    with patch.object(client._client, "get", new_callable=AsyncMock,
                      side_effect=httpx.ConnectError("refused")):
        result = await client.get_nt_filings("NVDA")
    assert result == []


def test_check_for_nt_filing_returns_risk_flag_string(client):
    """Should return a risk flag string when NT filings present."""
    filings = NT_FILING_RESPONSE["hits"]["hits"]
    flag = client.build_risk_flag(filings)
    assert flag is not None
    assert "NT" in flag
    assert "10-Q" in flag


def test_check_for_nt_filing_returns_none_when_no_filings(client):
    """Should return None when no NT filings."""
    flag = client.build_risk_flag([])
    assert flag is None
