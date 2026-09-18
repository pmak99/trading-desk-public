
from datetime import date, timedelta
from datetime import datetime as dt
from unittest.mock import MagicMock, patch

from src.domain.errors import AppError, Err, ErrorCode, Ok


def test_sync_falls_back_to_yahoo_when_av_fails():
    """When the bulk calendar fails, the sync checks each stored date against Yahoo.

    It writes nothing: NVDA agrees, and AAPL's disagreement is a single-source
    change, so it is counted for review rather than saved (2026-09-13).
    """
    from scripts.sync_earnings_calendar import SyncStats, sync_earnings_calendar
    from src.domain.types import EarningsTiming

    mock_av = MagicMock()
    mock_av.get_earnings_calendar.return_value = Err(
        AppError(ErrorCode.EXTERNAL, "AV rate limit hit")
    )

    mock_yahoo = MagicMock()
    nvda_date = date.today() + timedelta(days=10)
    aapl_date = date.today() + timedelta(days=20)
    mock_yahoo.get_next_earnings_date.return_value = Ok(
        (nvda_date, EarningsTiming.AMC)
    )

    mock_validator = MagicMock()
    mock_repo = MagicMock()

    fake_db_dates = {
        "NVDA": (nvda_date, EarningsTiming.AMC, dt.now(), None),
        "AAPL": (aapl_date, EarningsTiming.AMC, dt.now(), None),
    }

    with patch("scripts.sync_earnings_calendar.YahooFinanceEarnings", return_value=mock_yahoo), \
         patch("scripts.sync_earnings_calendar.get_database_dates", return_value=fake_db_dates), \
         patch("scripts.sync_earnings_calendar.get_focused_tickers", return_value={"NVDA", "AAPL"}):
        stats = sync_earnings_calendar(
            validator=mock_validator,
            earnings_repo=mock_repo,
            finnhub=mock_av,
            horizon="3month",
            dry_run=True,
        )

    assert isinstance(stats, SyncStats)
    assert stats.unchanged_dates == 1          # NVDA
    assert stats.conflicts_detected == 1       # AAPL: Yahoo alone disagrees
    assert stats.new_dates == 0 and stats.updated_dates == 0
    mock_repo.save_earnings_event.assert_not_called()
    assert mock_yahoo.get_next_earnings_date.call_count == 2  # called for NVDA and AAPL
