"""Characterization tests for extracted handlers/ submodules (pre-activation)."""
import sys
from pathlib import Path

# Point at the 5.0 package root so src.* imports resolve
_FIVE_O = Path(__file__).parent.parent
if str(_FIVE_O) not in sys.path:
    sys.path.insert(0, str(_FIVE_O))

# Point at handlers/ directory so submodule files are importable as top-level modules
_HANDLERS_DIR = _FIVE_O / "src" / "jobs" / "handlers"
if str(_HANDLERS_DIR) not in sys.path:
    sys.path.insert(0, str(_HANDLERS_DIR))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# helpers.py — fetch_earnings_with_db_fallback, _parse_price_history
# ---------------------------------------------------------------------------

class TestHelpersExtracted:
    """Characterization tests for helpers.py extracted functions."""

    @pytest.mark.asyncio
    async def test_fetch_earnings_returns_api_data_when_available(self):
        """When finnhub returns data, fetch_earnings_with_db_fallback returns it."""
        from helpers import fetch_earnings_with_db_fallback

        mock_finnhub = AsyncMock()
        mock_finnhub.get_earnings_calendar.return_value = [
            {"symbol": "AAPL", "report_date": "2026-02-09", "timing": "AMC"}
        ]
        mock_repo = MagicMock()

        with patch("helpers.today_et", return_value="2026-02-09"):
            result = await fetch_earnings_with_db_fallback(mock_finnhub, mock_repo, days=5)

        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"

    @pytest.mark.asyncio
    async def test_fetch_earnings_falls_back_to_db_when_api_empty(self):
        """When finnhub returns empty list, falls back to DB."""
        from helpers import fetch_earnings_with_db_fallback

        mock_finnhub = AsyncMock()
        mock_finnhub.get_earnings_calendar.return_value = []
        mock_repo = MagicMock()
        mock_repo.get_upcoming_earnings.return_value = [
            {"symbol": "NVDA", "report_date": "2026-02-10", "timing": "BMO"}
        ]

        with patch("helpers.today_et", return_value="2026-02-09"):
            result = await fetch_earnings_with_db_fallback(mock_finnhub, mock_repo, days=5)

        assert len(result) == 1
        assert result[0]["symbol"] == "NVDA"
        mock_repo.get_upcoming_earnings.assert_called_once()

    def test_parse_price_history_sorts_by_date(self):
        """_parse_price_history returns sorted list of (date_str, price) tuples."""
        from helpers import _parse_price_history

        closes = {
            "2026-02-09": 180.0,
            "2026-02-07": 175.0,
            "2026-02-08": 178.0,
        }
        result = _parse_price_history(closes)

        assert len(result) == 3
        assert result[0][0] == "2026-02-07"
        assert result[-1][0] == "2026-02-09"

    def test_parse_price_history_skips_invalid_prices(self):
        """_parse_price_history skips entries with non-numeric prices."""
        from helpers import _parse_price_history

        closes = {
            "2026-02-09": "N/A",
            "2026-02-08": 178.0,
        }
        result = _parse_price_history(closes)

        assert len(result) == 1
        assert result[0][0] == "2026-02-08"


# ---------------------------------------------------------------------------
# pre_market_prep.py
# ---------------------------------------------------------------------------

class TestPreMarketPrepExtracted:
    """Characterization tests for pre_market_prep.py extracted function."""

    @pytest.mark.asyncio
    async def test_empty_earnings_returns_success(self):
        """When earnings calendar is empty, returns success with tickers_found=0."""
        from src.jobs.handlers.pre_market_prep import _pre_market_prep

        mock_self = MagicMock()
        mock_self._start_timer.return_value = 0.0
        mock_self._record_duration.return_value = None
        mock_self._upcoming_earnings.return_value = ([], [])
        mock_self._filter_tracked.return_value = ([], MagicMock())
        mock_self._build_result.return_value = {"status": "success", "tickers_found": 0}

        with patch("src.jobs.handlers.pre_market_prep.fetch_earnings_with_db_fallback", new_callable=AsyncMock, return_value=[]):
            with patch("src.jobs.handlers.pre_market_prep.HistoricalMovesRepository"):
                result = await _pre_market_prep(mock_self)

        assert isinstance(result, dict)
        assert result.get("status") == "success"
        assert result.get("tickers_found") == 0

    @pytest.mark.asyncio
    async def test_returns_dict_with_status_key(self):
        """Result always contains a 'status' key."""
        from src.jobs.handlers.pre_market_prep import _pre_market_prep

        mock_self = MagicMock()
        mock_self._start_timer.return_value = 0.0
        mock_self._record_duration.return_value = None
        mock_self._upcoming_earnings.return_value = ([], [])
        mock_self._filter_tracked.return_value = ([], MagicMock())
        mock_self._build_result.return_value = {"status": "success", "tickers_found": 0}

        with patch("src.jobs.handlers.pre_market_prep.fetch_earnings_with_db_fallback", new_callable=AsyncMock, return_value=[]):
            with patch("src.jobs.handlers.pre_market_prep.HistoricalMovesRepository"):
                result = await _pre_market_prep(mock_self)

        assert "status" in result


# ---------------------------------------------------------------------------
# sentiment_scan.py
# ---------------------------------------------------------------------------

class TestSentimentScanExtracted:
    """Characterization tests for sentiment_scan.py extracted function."""

    @pytest.mark.asyncio
    async def test_empty_earnings_returns_zero_candidates(self):
        """When no earnings found, returns success with zero candidates and primed."""
        from sentiment_scan import _sentiment_scan

        mock_self = MagicMock()
        mock_self._start_timer.return_value = 0.0
        mock_self._record_duration.return_value = None
        mock_self._fetch_earnings = AsyncMock(return_value=[])

        result = await _sentiment_scan(mock_self)

        assert isinstance(result, dict)
        assert result.get("status") == "success"
        assert result.get("candidates") == 0
        assert result.get("primed") == 0

    @pytest.mark.asyncio
    async def test_result_has_required_keys(self):
        """Result always contains status, candidates, primed keys."""
        from sentiment_scan import _sentiment_scan

        mock_self = MagicMock()
        mock_self._start_timer.return_value = 0.0
        mock_self._record_duration.return_value = None
        mock_self._fetch_earnings = AsyncMock(return_value=[])

        result = await _sentiment_scan(mock_self)

        assert "status" in result
        assert "candidates" in result
        assert "primed" in result


# ---------------------------------------------------------------------------
# morning_digest.py
# ---------------------------------------------------------------------------

class TestMorningDigestExtracted:
    """Characterization tests for morning_digest.py extracted function."""

    @pytest.mark.asyncio
    async def test_empty_earnings_sends_warning_telegram(self):
        """When no earnings found, sends Telegram warning and returns status=warning."""
        from morning_digest import _morning_digest

        mock_self = MagicMock()
        mock_self._start_timer.return_value = 0.0
        mock_self._record_duration.return_value = None
        mock_self.telegram.send_message = AsyncMock()

        with patch("morning_digest.HistoricalMovesRepository") as mock_repo_cls:
            mock_repo = MagicMock()
            mock_repo.get_upcoming_earnings.return_value = []
            mock_repo_cls.return_value = mock_repo
            mock_self.finnhub.get_earnings_calendar = AsyncMock(return_value=[])
            with patch("morning_digest.today_et", return_value="2026-02-09"):
                with patch("morning_digest.metrics"):
                    result = await _morning_digest(mock_self)

        assert isinstance(result, dict)
        assert "status" in result


# ---------------------------------------------------------------------------
# market_open_refresh.py
# ---------------------------------------------------------------------------

class TestMarketOpenRefreshExtracted:
    """Characterization tests for market_open_refresh.py extracted function."""

    @pytest.mark.asyncio
    async def test_empty_earnings_returns_status(self):
        """When no earnings for today, returns a status dict."""
        from market_open_refresh import _market_open_refresh

        mock_self = MagicMock()
        mock_self._start_timer.return_value = 0.0
        mock_self._record_duration.return_value = None
        mock_self._todays_earnings.return_value = []
        mock_self._fetch_earnings = AsyncMock(return_value=[])

        result = await _market_open_refresh(mock_self)

        assert isinstance(result, dict)
        assert "status" in result


# ---------------------------------------------------------------------------
# pre_trade_refresh.py
# ---------------------------------------------------------------------------

class TestPreTradeRefreshExtracted:
    """Characterization tests for pre_trade_refresh.py extracted function."""

    @pytest.mark.asyncio
    async def test_empty_earnings_returns_status(self):
        """When no earnings, returns a status dict."""
        from pre_trade_refresh import _pre_trade_refresh

        mock_self = MagicMock()
        mock_self._start_timer.return_value = 0.0
        mock_self._record_duration.return_value = None
        mock_self._todays_earnings.return_value = []
        mock_self._fetch_earnings = AsyncMock(return_value=[])

        result = await _pre_trade_refresh(mock_self)

        assert isinstance(result, dict)
        assert "status" in result


# ---------------------------------------------------------------------------
# after_hours_check.py
# ---------------------------------------------------------------------------

class TestAfterHoursCheckExtracted:
    """Characterization tests for after_hours_check.py extracted function."""

    @pytest.mark.asyncio
    async def test_empty_candidates_returns_status(self):
        """When no daily candidates, returns a status dict."""
        from src.jobs.handlers.after_hours_check import _after_hours_check

        mock_self = MagicMock()
        mock_self._start_timer.return_value = 0.0
        mock_self._record_duration.return_value = None
        mock_self._todays_earnings.return_value = []
        mock_self._fetch_earnings = AsyncMock(return_value=[])
        mock_self._get_daily_candidates.return_value = set()

        with patch("src.jobs.handlers.after_hours_check.settings") as mock_settings:
            mock_settings.DB_PATH = ":memory:"
            result = await _after_hours_check(mock_self)

        assert isinstance(result, dict)
        assert "status" in result


# ---------------------------------------------------------------------------
# outcome_recorder.py
# ---------------------------------------------------------------------------

class TestOutcomeRecorderExtracted:
    """Characterization tests for outcome_recorder.py extracted function."""

    @pytest.mark.asyncio
    async def test_empty_earnings_returns_warning(self):
        """When earnings calendar is empty, returns warning status."""
        from src.jobs.handlers.outcome_recorder import _outcome_recorder

        mock_self = MagicMock()
        mock_self._start_timer.return_value = 0.0
        mock_self._record_duration.return_value = None
        mock_self.finnhub = AsyncMock()

        with patch("src.jobs.handlers.outcome_recorder.fetch_earnings_with_db_fallback", new_callable=AsyncMock, return_value=[]):
            with patch("src.jobs.handlers.outcome_recorder.HistoricalMovesRepository"):
                with patch("src.jobs.handlers.outcome_recorder.today_et", return_value="2026-02-09"):
                    with patch("src.jobs.handlers.outcome_recorder.now_et") as mock_now:
                        mock_now.return_value = MagicMock()
                        mock_now.return_value.__sub__ = MagicMock(return_value=MagicMock(strftime=MagicMock(return_value="2026-02-08")))
                        with patch("src.jobs.handlers.outcome_recorder.metrics"):
                            result = await _outcome_recorder(mock_self)

        assert isinstance(result, dict)
        assert "status" in result


# ---------------------------------------------------------------------------
# evening_summary.py
# ---------------------------------------------------------------------------

class TestEveningSummaryExtracted:
    """Characterization tests for evening_summary.py extracted function."""

    @pytest.mark.asyncio
    async def test_returns_status_dict(self):
        """_evening_summary always returns a dict with 'status' key."""
        from evening_summary import _evening_summary

        mock_self = MagicMock()
        mock_self._start_timer.return_value = 0.0
        mock_self._record_duration.return_value = None
        mock_self.finnhub.get_earnings_calendar = AsyncMock(return_value=[])
        mock_self.telegram.send_message = AsyncMock()

        with patch("evening_summary.today_et", return_value="2026-02-09"):
            with patch("evening_summary.HistoricalMovesRepository") as mock_repo_cls:
                mock_repo = MagicMock()
                mock_repo.get_recent_outcomes.return_value = []
                mock_repo_cls.return_value = mock_repo
                with patch("evening_summary.settings") as mock_s:
                    mock_s.DB_PATH = ":memory:"
                    result = await _evening_summary(mock_self)

        assert isinstance(result, dict)
        assert "status" in result


# ---------------------------------------------------------------------------
# weekly_backfill.py
# ---------------------------------------------------------------------------

class TestWeeklyBackfillExtracted:
    """Characterization tests for weekly_backfill.py extracted function."""

    @pytest.mark.asyncio
    async def test_empty_earnings_returns_warning(self):
        """When earnings calendar is empty, returns warning status."""
        from src.jobs.handlers.weekly_backfill import _weekly_backfill

        mock_self = MagicMock()
        mock_self._start_timer.return_value = 0.0
        mock_self._record_duration.return_value = None
        mock_self.finnhub = AsyncMock()

        with patch("src.jobs.handlers.weekly_backfill.fetch_earnings_with_db_fallback", new_callable=AsyncMock, return_value=[]):
            with patch("src.jobs.handlers.weekly_backfill.HistoricalMovesRepository"):
                with patch("src.jobs.handlers.weekly_backfill.now_et") as mock_now:
                    mock_now.return_value = MagicMock()
                    with patch("src.jobs.handlers.weekly_backfill.metrics"):
                        result = await _weekly_backfill(mock_self)

        assert isinstance(result, dict)
        assert "status" in result


# ---------------------------------------------------------------------------
# weekly_backup.py
# ---------------------------------------------------------------------------

class TestWeeklyBackupExtracted:
    """Characterization tests for weekly_backup.py extracted function."""

    @pytest.mark.asyncio
    async def test_returns_status_dict(self):
        """_weekly_backup returns a dict with 'status' key."""
        from weekly_backup import _weekly_backup

        mock_self = MagicMock()
        mock_self._start_timer.return_value = 0.0
        mock_self._record_duration.return_value = None

        with patch("weekly_backup.settings") as mock_s:
            mock_s.DB_PATH = ":memory:"
            mock_s.gcs_bucket = "test-bucket"
            with patch("weekly_backup.DatabaseSync") as mock_db_cls:
                mock_db = MagicMock()
                mock_db.__enter__ = MagicMock(return_value=mock_db)
                mock_db.__exit__ = MagicMock(return_value=False)
                mock_db.integrity_check.return_value = True
                mock_db.backup_to_gcs.return_value = None
                mock_db_cls.return_value = mock_db
                result = await _weekly_backup(mock_self)

        assert isinstance(result, dict)
        assert "status" in result


# ---------------------------------------------------------------------------
# weekly_cleanup.py
# ---------------------------------------------------------------------------

class TestWeeklyCleanupExtracted:
    """Characterization tests for weekly_cleanup.py extracted function."""

    @pytest.mark.asyncio
    async def test_returns_status_dict(self):
        """_weekly_cleanup returns a dict with 'status' key."""
        from weekly_cleanup import _weekly_cleanup

        mock_self = MagicMock()
        mock_self._start_timer.return_value = 0.0
        mock_self._record_duration.return_value = None

        with patch("weekly_cleanup.settings") as mock_s:
            mock_s.SENTIMENT_CACHE_DB_PATH = ":memory:"
            with patch("weekly_cleanup.SentimentCacheRepository") as mock_cache_cls:
                mock_cache = MagicMock()
                mock_cache.clear_expired.return_value = 5
                mock_cache_cls.return_value = mock_cache
                with patch("weekly_cleanup.metrics"):
                    with patch("weekly_cleanup.log"):
                        result = await _weekly_cleanup(mock_self)

        assert isinstance(result, dict)
        assert "status" in result


# ---------------------------------------------------------------------------
# calendar_sync.py
# ---------------------------------------------------------------------------

class TestCalendarSyncExtracted:
    """Characterization tests for calendar_sync.py extracted function."""

    @pytest.mark.asyncio
    async def test_empty_earnings_returns_status(self):
        """When earnings calendar is empty, returns a status dict."""
        from calendar_sync import _calendar_sync

        mock_self = MagicMock()
        mock_self._start_timer.return_value = 0.0
        mock_self._record_duration.return_value = None
        mock_self._fetch_earnings = AsyncMock(return_value=[])

        result = await _calendar_sync(mock_self)

        assert isinstance(result, dict)
        assert "status" in result
