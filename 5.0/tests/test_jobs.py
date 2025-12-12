# 5.0/tests/test_jobs.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.jobs.handlers import JobRunner


@pytest.fixture
def runner():
    """Create JobRunner with mocked dependencies."""
    with patch("src.jobs.handlers.settings") as mock_settings:
        mock_settings.TRADIER_API_KEY = "test_key"
        mock_settings.ALPHA_VANTAGE_KEY = "test_key"
        mock_settings.TELEGRAM_BOT_TOKEN = "test_token"
        mock_settings.TELEGRAM_CHAT_ID = "123456"
        mock_settings.DB_PATH = ":memory:"
        mock_settings.PERPLEXITY_API_KEY = "test_key"

        return JobRunner()


@pytest.mark.asyncio
async def test_run_unknown_job(runner):
    """Unknown job returns error status."""
    result = await runner.run("nonexistent-job")

    assert result["status"] == "error"
    assert "Unknown job" in result["error"]


@pytest.mark.asyncio
async def test_run_pre_market_prep(runner):
    """Pre-market prep job fetches earnings calendar."""
    with patch.object(runner, "_pre_market_prep", new_callable=AsyncMock) as mock_job:
        mock_job.return_value = {"status": "success", "tickers_found": 5}

        result = await runner.run("pre-market-prep")

    assert result["status"] == "success"
    mock_job.assert_called_once()


@pytest.mark.asyncio
async def test_run_sentiment_scan(runner):
    """Sentiment scan analyzes tickers."""
    with patch.object(runner, "_sentiment_scan", new_callable=AsyncMock) as mock_job:
        mock_job.return_value = {"status": "success", "analyzed": 3}

        result = await runner.run("sentiment-scan")

    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_run_morning_digest(runner):
    """Morning digest sends Telegram summary."""
    with patch.object(runner, "_morning_digest", new_callable=AsyncMock) as mock_job:
        mock_job.return_value = {"status": "success", "sent": True}

        result = await runner.run("morning-digest")

    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_run_outcome_recorder(runner):
    """Outcome recorder captures post-earnings moves."""
    with patch.object(runner, "_outcome_recorder", new_callable=AsyncMock) as mock_job:
        mock_job.return_value = {"status": "success", "recorded": 2}

        result = await runner.run("outcome-recorder")

    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_run_calendar_sync(runner):
    """Calendar sync refreshes earnings dates."""
    with patch.object(runner, "_calendar_sync", new_callable=AsyncMock) as mock_job:
        mock_job.return_value = {"status": "success", "synced": 100}

        result = await runner.run("calendar-sync")

    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_job_error_handling(runner):
    """Job errors are caught and returned."""
    with patch.object(runner, "_pre_market_prep", new_callable=AsyncMock) as mock_job:
        mock_job.side_effect = Exception("Test error")

        result = await runner.run("pre-market-prep")

    assert result["status"] == "error"
    assert "Test error" in result["error"]
