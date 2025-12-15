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
async def test_run_market_open_refresh(runner):
    """Market open refresh updates prices for today's earnings."""
    with patch.object(runner, "_market_open_refresh", new_callable=AsyncMock) as mock_job:
        mock_job.return_value = {"status": "success", "refreshed": 10, "significant_moves": 2}

        result = await runner.run("market-open-refresh")

    assert result["status"] == "success"
    mock_job.assert_called_once()


@pytest.mark.asyncio
async def test_run_pre_trade_refresh(runner):
    """Pre-trade refresh validates VRP before trade window."""
    with patch.object(runner, "_pre_trade_refresh", new_callable=AsyncMock) as mock_job:
        mock_job.return_value = {"status": "success", "candidates": 5, "top_tickers": ["AAPL", "NVDA"]}

        result = await runner.run("pre-trade-refresh")

    assert result["status"] == "success"
    mock_job.assert_called_once()


@pytest.mark.asyncio
async def test_run_after_hours_check(runner):
    """After hours check monitors earnings moves."""
    with patch.object(runner, "_after_hours_check", new_callable=AsyncMock) as mock_job:
        mock_job.return_value = {"status": "success", "checked": 8, "reported": 3}

        result = await runner.run("after-hours-check")

    assert result["status"] == "success"
    mock_job.assert_called_once()


@pytest.mark.asyncio
async def test_run_evening_summary(runner):
    """Evening summary sends daily notification."""
    with patch.object(runner, "_evening_summary", new_callable=AsyncMock) as mock_job:
        mock_job.return_value = {"status": "success", "sent": True}

        result = await runner.run("evening-summary")

    assert result["status"] == "success"
    mock_job.assert_called_once()


@pytest.mark.asyncio
async def test_run_weekly_backfill(runner):
    """Weekly backfill records historical moves."""
    with patch.object(runner, "_weekly_backfill", new_callable=AsyncMock) as mock_job:
        mock_job.return_value = {"status": "success", "backfilled": 15, "skipped_duplicate": 3, "errors": 0}

        result = await runner.run("weekly-backfill")

    assert result["status"] == "success"
    mock_job.assert_called_once()


@pytest.mark.asyncio
async def test_run_weekly_backup(runner):
    """Weekly backup uploads database to GCS."""
    with patch.object(runner, "_weekly_backup", new_callable=AsyncMock) as mock_job:
        mock_job.return_value = {"status": "success", "backed_up": True, "blob": "backups/ivcrush_20251215.db"}

        result = await runner.run("weekly-backup")

    assert result["status"] == "success"
    mock_job.assert_called_once()


@pytest.mark.asyncio
async def test_run_weekly_cleanup(runner):
    """Weekly cleanup clears expired cache."""
    with patch.object(runner, "_weekly_cleanup", new_callable=AsyncMock) as mock_job:
        mock_job.return_value = {"status": "success", "cleared": 42}

        result = await runner.run("weekly-cleanup")

    assert result["status"] == "success"
    mock_job.assert_called_once()


@pytest.mark.asyncio
async def test_job_error_handling(runner):
    """Job errors are caught and returned."""
    with patch.object(runner, "_pre_market_prep", new_callable=AsyncMock) as mock_job:
        mock_job.side_effect = Exception("Test error")

        result = await runner.run("pre-market-prep")

    assert result["status"] == "error"
    assert "Test error" in result["error"]


@pytest.mark.asyncio
async def test_all_jobs_registered():
    """All 12 scheduled jobs are registered in handler_map."""
    runner = JobRunner()

    expected_jobs = [
        "pre-market-prep",
        "sentiment-scan",
        "morning-digest",
        "market-open-refresh",
        "pre-trade-refresh",
        "after-hours-check",
        "outcome-recorder",
        "evening-summary",
        "weekly-backfill",
        "weekly-backup",
        "weekly-cleanup",
        "calendar-sync",
    ]

    # Run each job with mocked handler to verify registration
    for job_name in expected_jobs:
        # Just verify it doesn't return "Unknown job" error
        with patch.object(runner, f"_{job_name.replace('-', '_')}", new_callable=AsyncMock) as mock_job:
            mock_job.return_value = {"status": "success"}
            result = await runner.run(job_name)
            assert result["status"] == "success", f"Job {job_name} not registered correctly"
