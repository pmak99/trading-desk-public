import pytest
from unittest.mock import AsyncMock, patch
from src.integrations.perplexity import PerplexityClient, parse_sentiment_response

def test_parse_sentiment_response_bullish():
    """Parse bullish sentiment response."""
    text = """Direction: bullish
Score: 0.7
Catalysts: AI demand surge, Data center growth
Risks: China exposure"""

    result = parse_sentiment_response(text)
    assert result["direction"] == "bullish"
    assert result["score"] == 0.7
    assert "AI demand" in result["tailwinds"]
    assert "China" in result["headwinds"]

def test_parse_sentiment_response_bearish():
    """Parse bearish sentiment response."""
    text = """Direction: bearish
Score: -0.5
Catalysts: Market expansion
Risks: Inventory concerns, Competition"""

    result = parse_sentiment_response(text)
    assert result["direction"] == "bearish"
    assert result["score"] == -0.5

@pytest.mark.asyncio
async def test_get_sentiment():
    """get_sentiment calls API and parses response."""
    client = PerplexityClient(api_key="test-key")

    mock_response = {
        "choices": [{
            "message": {
                "content": "Direction: bullish\nScore: 0.6\nCatalysts: Growth\nRisks: None"
            }
        }]
    }

    with patch.object(client, '_request', new_callable=AsyncMock) as mock:
        mock.return_value = mock_response
        result = await client.get_sentiment("NVDA", "2025-01-15")

        assert result["direction"] == "bullish"
        assert result["score"] == 0.6
