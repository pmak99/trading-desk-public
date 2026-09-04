import pytest
import httpx
from unittest.mock import AsyncMock, patch
from src.integrations.tradier import TradierClient

@pytest.fixture
def tradier():
    return TradierClient(api_key="test-key")

@pytest.mark.asyncio
async def test_get_quote(tradier):
    """get_quote returns price data."""
    mock_response = {
        "quotes": {
            "quote": {
                "symbol": "NVDA",
                "last": 135.50,
                "bid": 135.45,
                "ask": 135.55,
            }
        }
    }

    with patch.object(tradier, '_request', new_callable=AsyncMock) as mock:
        mock.return_value = mock_response
        result = await tradier.get_quote("NVDA")

        assert result["symbol"] == "NVDA"
        assert result["last"] == 135.50

@pytest.mark.asyncio
async def test_get_options_chain(tradier):
    """get_options_chain returns options data."""
    mock_response = {
        "options": {
            "option": [
                {
                    "symbol": "NVDA250117C00140000",
                    "strike": 140.0,
                    "option_type": "call",
                    "bid": 5.20,
                    "ask": 5.40,
                    "open_interest": 1500,
                    "greeks": {"delta": 0.45, "theta": -0.08, "vega": 0.25}
                }
            ]
        }
    }

    with patch.object(tradier, '_request', new_callable=AsyncMock) as mock:
        mock.return_value = mock_response
        result = await tradier.get_options_chain("NVDA", "2025-01-17")

        assert len(result) == 1
        assert result[0]["strike"] == 140.0
