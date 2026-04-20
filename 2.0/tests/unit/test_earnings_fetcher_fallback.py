import sys
from pathlib import Path

sys.path.insert(0, str(Path("/Users/prashant/PycharmProjects/Trading Desk/2.0")))

import pytest
from unittest.mock import MagicMock, patch
from datetime import date
from src.domain.errors import Result, AppError, ErrorCode


def test_sync_falls_back_to_yahoo_when_av_fails():
    """When AV returns error, sync_earnings_calendar uses Yahoo Finance fallback without errors."""
    from scripts.sync_earnings_calendar import sync_earnings_calendar, SyncStats
    from src.domain.types import EarningsTiming

    mock_av = MagicMock()
    mock_av.get_earnings_calendar.return_value = Result.Err(
        AppError(ErrorCode.EXTERNAL, "AV rate limit hit")
    )

    mock_yahoo = MagicMock()
    mock_yahoo.get_next_earnings_date.return_value = Result.Ok(
        (date(2026, 5, 1), EarningsTiming.AMC)
    )

    mock_validator = MagicMock()
    mock_repo = MagicMock()

    fake_db_dates = {
        "NVDA": {date(2026, 5, 1)},
        "AAPL": {date(2026, 5, 15)},
    }

    with patch("scripts.sync_earnings_calendar.YahooFinanceEarnings", return_value=mock_yahoo), \
         patch("scripts.sync_earnings_calendar.get_database_dates", return_value=fake_db_dates):
        stats = sync_earnings_calendar(
            validator=mock_validator,
            earnings_repo=mock_repo,
            alpha_vantage=mock_av,
            db_path=":memory:",
            horizon="3month",
            dry_run=True,
        )

    assert isinstance(stats, SyncStats)
    assert stats.errors == 0
    assert mock_yahoo.get_next_earnings_date.call_count == 2  # called for NVDA and AAPL
