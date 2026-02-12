# cloud/tests/test_telegram.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.integrations.telegram import TelegramSender


@pytest.fixture
def sender():
    return TelegramSender(bot_token="test_token", chat_id="123456")


@pytest.mark.asyncio
async def test_send_message(sender):
    """Send basic message via Telegram."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ok": True, "result": {"message_id": 1}}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

        result = await sender.send_message("Test message")

    assert result is True


@pytest.mark.asyncio
async def test_send_alert(sender):
    """Send alert with emoji and formatting."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ok": True, "result": {"message_id": 2}}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

        result = await sender.send_alert(
            ticker="NVDA",
            score=85,
            vrp=7.5,
            implied_move=8.2,
        )

    assert result is True


@pytest.mark.asyncio
async def test_send_digest(sender):
    """Send daily digest with multiple tickers."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"ok": True, "result": {"message_id": 3}}
    mock_response.raise_for_status = MagicMock()

    tickers = [
        {"symbol": "NVDA", "score": 85, "vrp": 7.5},
        {"symbol": "AMD", "score": 72, "vrp": 5.2},
    ]

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=mock_response)

        result = await sender.send_digest("2025-01-15", tickers)

    assert result is True


@pytest.mark.asyncio
async def test_format_alert_message(sender):
    """Format alert message correctly."""
    message = sender._format_alert(
        ticker="AAPL",
        score=78,
        vrp=5.5,
        implied_move=6.0,
    )

    assert "AAPL" in message
    assert "78" in message
    assert "5.5" in message


@pytest.mark.asyncio
async def test_send_message_failure(sender):
    """Handle API failure gracefully."""
    mock_response = AsyncMock()
    mock_response.status_code = 400
    mock_response.json.return_value = {"ok": False, "description": "Bad Request"}
    mock_response.raise_for_status.side_effect = Exception("Bad Request")

    with patch("httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.post.return_value = mock_response

        result = await sender.send_message("Test")

    assert result is False
