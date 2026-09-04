"""
Tests for FinraClient — short volume ratio via FINRA regsho daily files.

TDD: tests written before implementation.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date


SAMPLE_REGSHO = """\
Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
20260521|AAPL|10000000|0|20000000|FNSQ
20260521|NVDA|15000000|0|25000000|FNSQ
20260521|MSFT|8000000|0|30000000|FNSQ
"""

SAMPLE_REGSHO_HIGH_SHORT = """\
Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market
20260521|NVDA|15000000|0|20000000|FNSQ
"""


def _mock_http_response(text: str, status_code: int = 200):
    mock = MagicMock()
    mock.status_code = status_code
    mock.text = text
    return mock


@pytest.fixture
def client():
    from src.integrations.finra import FinraClient
    return FinraClient()


@pytest.mark.asyncio
async def test_get_short_ratio_returns_float_for_valid_ticker(client):
    """Should return short ratio (0-1) for a known ticker."""
    mock_response = _mock_http_response(SAMPLE_REGSHO)
    with patch.object(client._client, "get", new_callable=AsyncMock,
                      return_value=mock_response):
        result = await client.get_short_ratio("NVDA", target_date=date(2026, 5, 21))
    assert result is not None
    assert 0.0 < result < 1.0
    assert abs(result - 0.6) < 0.01  # 15M / 25M = 0.6


@pytest.mark.asyncio
async def test_get_short_ratio_returns_none_for_unknown_ticker(client):
    """Should return None when ticker not in file."""
    mock_response = _mock_http_response(SAMPLE_REGSHO)
    with patch.object(client._client, "get", new_callable=AsyncMock,
                      return_value=mock_response):
        result = await client.get_short_ratio("UNKNOWN", target_date=date(2026, 5, 21))
    assert result is None


@pytest.mark.asyncio
async def test_get_short_ratio_returns_none_on_http_error(client):
    """Should return None gracefully on HTTP error."""
    mock_response = _mock_http_response("", status_code=404)
    with patch.object(client._client, "get", new_callable=AsyncMock,
                      return_value=mock_response):
        result = await client.get_short_ratio("NVDA", target_date=date(2026, 5, 21))
    assert result is None


@pytest.mark.asyncio
async def test_get_short_ratio_returns_none_on_connection_error(client):
    """Should return None on network failure."""
    import httpx
    with patch.object(client._client, "get", new_callable=AsyncMock,
                      side_effect=httpx.ConnectError("refused")):
        result = await client.get_short_ratio("NVDA", target_date=date(2026, 5, 21))
    assert result is None


def test_build_risk_flag_returns_flag_when_high_short_ratio(client):
    """Should return a risk flag string for high short ratio (>= 50%)."""
    flag = client.build_risk_flag(0.55)
    assert flag is not None
    assert "short" in flag.lower()
    assert "55" in flag


def test_build_risk_flag_returns_none_for_normal_ratio(client):
    """Should return None when short ratio is below threshold."""
    flag = client.build_risk_flag(0.35)
    assert flag is None


def test_build_risk_flag_returns_none_for_none_input(client):
    """Should return None when ratio is None (data unavailable)."""
    flag = client.build_risk_flag(None)
    assert flag is None
