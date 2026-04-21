"""
Comprehensive tests for job handlers in 5.0/src/jobs/handlers.py.

Tests the actual business logic inside each handler, not just that mocks are called.
Covers:
- fetch_earnings_with_db_fallback (API success, API failure, both fail)
- _parse_price_history helper
- _pre_market_prep (earnings pipeline, VRP evaluation, rate limiting)
- _sentiment_scan (priming flow)
- _morning_digest (empty calendar, opportunities, Telegram sending)
- _outcome_recorder (BMO/AMC timing, duplicate skipping)
- _weekly_backfill (timing-aware backfill, duplicate detection)
- _weekly_backup (integrity check, GCS upload)
- _weekly_cleanup (cache clearing)
- _calendar_sync (upsert + GCS upload)
- BaseJobHandler shared methods (_build_result, _rate_limit_tick, _get_historical_pcts)
"""

import asyncio
import tempfile
import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
from typing import Dict, Any, List

import pytz
import pytest

# Shared timezone for tests
ET = pytz.timezone("US/Eastern")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_settings():
    """Patch settings with test values for all handler tests."""
    with patch("src.jobs.handlers.settings") as s:
        s.tradier_api_key = "test_tradier_key"
        s.alpha_vantage_key = "test_av_key"
        s.perplexity_api_key = "test_pplx_key"
        s.telegram_bot_token = "test_bot_token"
        s.telegram_chat_id = "123456"
        s.twelve_data_key = "test_12d_key"
        s.DB_PATH = ":memory:"
        s.VRP_DISCOVERY = 1.8
        s.require_weekly_options = True
        s.gcs_bucket = "test-bucket"
        yield s


@pytest.fixture
def runner(mock_settings):
    """Create a JobRunner with all external clients mocked."""
    from src.jobs.handlers import JobRunner

    jr = JobRunner()

    # Mock all lazy-initialized clients
    jr._tradier = AsyncMock()
    jr._alphavantage = AsyncMock()
    jr._perplexity = AsyncMock()
    jr._telegram = AsyncMock()
    jr._yahoo = AsyncMock()
    jr._twelvedata = AsyncMock()

    return jr


def _make_earnings(symbols: List[str], report_date: str = "2026-02-09",
                   timing: str = "") -> List[Dict[str, Any]]:
    """Helper to build earnings list for tests."""
    return [
        {"symbol": s, "report_date": report_date, "timing": timing}
        for s in symbols
    ]


# ---------------------------------------------------------------------------
# fetch_earnings_with_db_fallback
# ---------------------------------------------------------------------------

class TestFetchEarningsWithDbFallback:
    """Tests for the top-level fetch_earnings_with_db_fallback function."""

    @pytest.mark.asyncio
    async def test_api_success_returns_api_data(self):
        """When Alpha Vantage returns data, use it directly."""
        from src.jobs.handlers import fetch_earnings_with_db_fallback

        av = AsyncMock()
        av.get_earnings_calendar.return_value = [
            {"symbol": "AAPL", "report_date": "2026-02-09", "timing": "AMC"},
        ]
        repo = MagicMock()

        result = await fetch_earnings_with_db_fallback(av, repo, days=5)

        assert len(result) == 1
        assert result[0]["symbol"] == "AAPL"
        repo.get_upcoming_earnings.assert_not_called()

    @pytest.mark.asyncio
    async def test_api_empty_falls_back_to_db(self):
        """When Alpha Vantage returns empty list, fall back to DB."""
        from src.jobs.handlers import fetch_earnings_with_db_fallback

        av = AsyncMock()
        av.get_earnings_calendar.return_value = []
        repo = MagicMock()
        repo.get_upcoming_earnings.return_value = [
            {"symbol": "MSFT", "report_date": "2026-02-09", "timing": "BMO"},
        ]

        with patch("src.jobs.handlers.today_et", return_value="2026-02-07"):
            result = await fetch_earnings_with_db_fallback(av, repo, days=5)

        assert len(result) == 1
        assert result[0]["symbol"] == "MSFT"
        repo.get_upcoming_earnings.assert_called_once_with("2026-02-07", 5)

    @pytest.mark.asyncio
    async def test_api_none_falls_back_to_db(self):
        """When Alpha Vantage returns None, fall back to DB."""
        from src.jobs.handlers import fetch_earnings_with_db_fallback

        av = AsyncMock()
        av.get_earnings_calendar.return_value = None
        repo = MagicMock()
        repo.get_upcoming_earnings.return_value = [
            {"symbol": "GOOG", "report_date": "2026-02-10", "timing": "AMC"},
        ]

        with patch("src.jobs.handlers.today_et", return_value="2026-02-07"):
            result = await fetch_earnings_with_db_fallback(av, repo, days=3)

        assert result[0]["symbol"] == "GOOG"
        repo.get_upcoming_earnings.assert_called_once_with("2026-02-07", 3)

    @pytest.mark.asyncio
    async def test_api_exception_falls_back_to_db(self):
        """When Alpha Vantage raises exception, fall back to DB."""
        from src.jobs.handlers import fetch_earnings_with_db_fallback

        av = AsyncMock()
        av.get_earnings_calendar.side_effect = ConnectionError("API down")
        repo = MagicMock()
        repo.get_upcoming_earnings.return_value = [
            {"symbol": "TSLA", "report_date": "2026-02-09", "timing": "AMC"},
        ]

        with patch("src.jobs.handlers.today_et", return_value="2026-02-07"):
            result = await fetch_earnings_with_db_fallback(av, repo, days=5)

        assert len(result) == 1
        assert result[0]["symbol"] == "TSLA"

    @pytest.mark.asyncio
    async def test_both_fail_returns_db_result(self):
        """When API errors and DB returns empty, result is empty list."""
        from src.jobs.handlers import fetch_earnings_with_db_fallback

        av = AsyncMock()
        av.get_earnings_calendar.side_effect = RuntimeError("API error")
        repo = MagicMock()
        repo.get_upcoming_earnings.return_value = []

        with patch("src.jobs.handlers.today_et", return_value="2026-02-07"):
            result = await fetch_earnings_with_db_fallback(av, repo, days=5)

        assert result == []

    @pytest.mark.asyncio
    async def test_days_parameter_passed_to_db(self):
        """The days parameter is correctly forwarded to DB fallback."""
        from src.jobs.handlers import fetch_earnings_with_db_fallback

        av = AsyncMock()
        av.get_earnings_calendar.return_value = []
        repo = MagicMock()
        repo.get_upcoming_earnings.return_value = []

        with patch("src.jobs.handlers.today_et", return_value="2026-02-07"):
            await fetch_earnings_with_db_fallback(av, repo, days=14)

        repo.get_upcoming_earnings.assert_called_once_with("2026-02-07", 14)


# ---------------------------------------------------------------------------
# _parse_price_history
# ---------------------------------------------------------------------------

class TestParsePriceHistory:
    """Tests for the _parse_price_history helper function."""

    def test_datetime_keys(self):
        """Handles datetime objects as keys."""
        from src.jobs.handlers import _parse_price_history

        closes = {
            datetime(2026, 2, 5): 150.0,
            datetime(2026, 2, 6): 152.5,
            datetime(2026, 2, 7): 148.3,
        }
        result = _parse_price_history(closes)

        assert len(result) == 3
        assert result[0] == ("2026-02-05", 150.0)
        assert result[1] == ("2026-02-06", 152.5)
        assert result[2] == ("2026-02-07", 148.3)

    def test_string_keys(self):
        """Handles string timestamps as keys."""
        from src.jobs.handlers import _parse_price_history

        closes = {
            "2026-02-05T16:00:00": 150.0,
            "2026-02-06T16:00:00": 155.0,
        }
        result = _parse_price_history(closes)

        assert len(result) == 2
        assert result[0][0] == "2026-02-05"
        assert result[1][0] == "2026-02-06"

    def test_none_prices_skipped(self):
        """None prices are filtered out."""
        from src.jobs.handlers import _parse_price_history

        closes = {
            "2026-02-05": 150.0,
            "2026-02-06": None,
            "2026-02-07": 148.0,
        }
        result = _parse_price_history(closes)

        assert len(result) == 2
        symbols = [r[0] for r in result]
        assert "2026-02-06" not in symbols

    def test_sorted_output(self):
        """Output is sorted by date ascending."""
        from src.jobs.handlers import _parse_price_history

        closes = {
            "2026-02-07": 148.0,
            "2026-02-05": 150.0,
            "2026-02-06": 152.0,
        }
        result = _parse_price_history(closes)

        dates = [r[0] for r in result]
        assert dates == ["2026-02-05", "2026-02-06", "2026-02-07"]

    def test_empty_dict(self):
        """Empty input returns empty list."""
        from src.jobs.handlers import _parse_price_history

        assert _parse_price_history({}) == []

    def test_integer_keys(self):
        """Handles integer/numeric keys via str()."""
        from src.jobs.handlers import _parse_price_history

        closes = {1738713600: 100.0}  # Unix timestamp
        result = _parse_price_history(closes)

        assert len(result) == 1
        assert result[0][1] == 100.0

    def test_invalid_price_skipped(self):
        """Non-numeric prices that can't be converted are skipped."""
        from src.jobs.handlers import _parse_price_history

        closes = {
            "2026-02-05": "not_a_number",
            "2026-02-06": 150.0,
        }
        result = _parse_price_history(closes)

        assert len(result) == 1
        assert result[0][0] == "2026-02-06"


# ---------------------------------------------------------------------------
# BaseJobHandler shared methods
# ---------------------------------------------------------------------------

class TestBaseJobHandler:
    """Tests for shared BaseJobHandler methods."""

    def test_build_result_basic(self):
        """_build_result creates a success dict with kwargs."""
        from src.jobs.base import BaseJobHandler

        result = BaseJobHandler._build_result(
            tickers_found=5,
            earnings_dates=["2026-02-09"],
        )

        assert result["status"] == "success"
        assert result["tickers_found"] == 5
        assert result["earnings_dates"] == ["2026-02-09"]

    def test_build_result_with_failed_tickers(self):
        """_build_result includes failed_tickers when present."""
        from src.jobs.base import BaseJobHandler

        with patch("src.jobs.base.metrics"):
            result = BaseJobHandler._build_result(
                failed_tickers=["AAPL", "MSFT"],
                job_name="test_job",
                refreshed=3,
            )

        assert result["failed_tickers"] == ["AAPL", "MSFT"]
        assert result["refreshed"] == 3

    def test_build_result_with_telegram_error(self):
        """_build_result includes telegram_error when present."""
        from src.jobs.base import BaseJobHandler

        result = BaseJobHandler._build_result(
            telegram_error="Connection timeout",
            sent=False,
        )

        assert result["telegram_error"] == "Connection timeout"
        assert result["sent"] is False

    def test_build_result_no_failed_tickers_when_none(self):
        """_build_result omits failed_tickers when None."""
        from src.jobs.base import BaseJobHandler

        result = BaseJobHandler._build_result(refreshed=5)

        assert "failed_tickers" not in result
        assert "telegram_error" not in result

    @pytest.mark.asyncio
    async def test_rate_limit_tick_triggers_at_batch_boundary(self):
        """Rate limiting sleeps at batch boundaries."""
        from src.jobs.base import BaseJobHandler

        with patch("src.jobs.base.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await BaseJobHandler._rate_limit_tick(5, batch_size=5, delay=0.5)
            mock_sleep.assert_called_once_with(0.5)

    @pytest.mark.asyncio
    async def test_rate_limit_tick_no_sleep_between_batches(self):
        """Rate limiting does not sleep between batch boundaries."""
        from src.jobs.base import BaseJobHandler

        with patch("src.jobs.base.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await BaseJobHandler._rate_limit_tick(3, batch_size=5, delay=0.5)
            mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_rate_limit_tick_zero_calls_no_sleep(self):
        """Zero API calls should not trigger sleep."""
        from src.jobs.base import BaseJobHandler

        with patch("src.jobs.base.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await BaseJobHandler._rate_limit_tick(0, batch_size=5, delay=0.5)
            mock_sleep.assert_not_called()

    def test_get_historical_pcts_with_data(self):
        """Returns historical percentages and average when sufficient data."""
        from src.jobs.base import BaseJobHandler

        repo = MagicMock()
        repo.get_moves.return_value = [
            {"intraday_move_pct": 3.0},
            {"intraday_move_pct": -5.0},
            {"intraday_move_pct": 4.0},
            {"intraday_move_pct": -2.0},
        ]

        pcts, avg = BaseJobHandler._get_historical_pcts(repo, "AAPL")

        assert pcts == [3.0, 5.0, 4.0, 2.0]
        assert avg == pytest.approx(3.5)

    def test_get_historical_pcts_insufficient_data(self):
        """Returns (None, None) when fewer than min_moves."""
        from src.jobs.base import BaseJobHandler

        repo = MagicMock()
        repo.get_moves.return_value = [
            {"intraday_move_pct": 3.0},
            {"intraday_move_pct": -5.0},
        ]

        pcts, avg = BaseJobHandler._get_historical_pcts(repo, "TINY", min_moves=4)

        assert pcts is None
        assert avg is None

    def test_get_historical_pcts_zero_moves_filtered(self):
        """Moves with zero or None intraday_move_pct are filtered out."""
        from src.jobs.base import BaseJobHandler

        repo = MagicMock()
        repo.get_moves.return_value = [
            {"intraday_move_pct": 3.0},
            {"intraday_move_pct": 0},
            {"intraday_move_pct": None},
            {"intraday_move_pct": 2.0},
            {"intraday_move_pct": 4.0},
            {"intraday_move_pct": 1.0},
        ]

        pcts, avg = BaseJobHandler._get_historical_pcts(repo, "AAPL")

        # 0 and None are falsy, filtered out by `if m.get("intraday_move_pct")`
        assert pcts == [3.0, 2.0, 4.0, 1.0]
        assert avg == pytest.approx(2.5)

    def test_filter_to_tracked_tickers(self):
        """filter_to_tracked_tickers keeps only whitelisted symbols."""
        from src.jobs.base import filter_to_tracked_tickers

        earnings = [
            {"symbol": "AAPL", "report_date": "2026-02-09"},
            {"symbol": "CUIRF", "report_date": "2026-02-09"},
            {"symbol": "MSFT", "report_date": "2026-02-09"},
        ]
        tracked = {"AAPL", "MSFT", "TSLA"}

        result = filter_to_tracked_tickers(earnings, tracked)

        assert len(result) == 2
        assert all(e["symbol"] in tracked for e in result)

    def test_save_and_get_daily_candidates(self, tmp_path):
        """Daily candidates round-trip: save then retrieve."""
        from src.jobs.base import BaseJobHandler

        db_path = str(tmp_path / "test.db")
        BaseJobHandler._save_daily_candidates(db_path, "2026-02-25", ["AAPL", "CRM"], "morning_digest")
        BaseJobHandler._save_daily_candidates(db_path, "2026-02-25", ["CRM", "SNOW"], "pre_trade_refresh")

        result = BaseJobHandler._get_daily_candidates(db_path, "2026-02-25")
        assert result == {"AAPL", "CRM", "SNOW"}

    def test_get_daily_candidates_empty_date(self, tmp_path):
        """No candidates for a date returns empty set."""
        from src.jobs.base import BaseJobHandler

        db_path = str(tmp_path / "test.db")
        BaseJobHandler._save_daily_candidates(db_path, "2026-02-25", ["AAPL"], "morning_digest")

        result = BaseJobHandler._get_daily_candidates(db_path, "2026-02-26")
        assert result == set()

    def test_get_daily_candidates_no_table(self, tmp_path):
        """Returns empty set when table doesn't exist yet."""
        import sqlite3
        from src.jobs.base import BaseJobHandler

        db_path = str(tmp_path / "empty.db")
        sqlite3.connect(db_path).close()  # Create empty DB

        result = BaseJobHandler._get_daily_candidates(db_path, "2026-02-25")
        assert result == set()

    def test_save_daily_candidates_idempotent(self, tmp_path):
        """Saving same ticker+date+source twice doesn't duplicate."""
        from src.jobs.base import BaseJobHandler

        db_path = str(tmp_path / "test.db")
        BaseJobHandler._save_daily_candidates(db_path, "2026-02-25", ["AAPL"], "morning_digest")
        BaseJobHandler._save_daily_candidates(db_path, "2026-02-25", ["AAPL"], "morning_digest")

        result = BaseJobHandler._get_daily_candidates(db_path, "2026-02-25")
        assert result == {"AAPL"}


def test_job_runner_accepts_twelvedata_client():
    """JobRunner must accept a pre-built TwelveDataClient to enable proper lifecycle management."""
    from src.jobs.handlers import JobRunner
    from unittest.mock import MagicMock
    mock_client = MagicMock()
    runner = JobRunner(twelvedata_client=mock_client)
    assert runner._twelvedata is mock_client


# ---------------------------------------------------------------------------
# _pre_market_prep
# ---------------------------------------------------------------------------

class TestPreMarketPrep:
    """Tests for the _pre_market_prep handler."""

    @pytest.mark.asyncio
    async def test_empty_calendar_returns_success(self, runner, mock_settings):
        """Empty earnings calendar returns success (not warning) — warning blocks all downstream jobs."""
        with patch("src.jobs.handlers.fetch_earnings_with_db_fallback", new_callable=AsyncMock) as mock_fallback, \
             patch("src.jobs.handlers.HistoricalMovesRepository"):
            mock_fallback.return_value = []
            result = await runner._pre_market_prep()

        assert result["status"] == "success"
        assert result["tickers_found"] == 0

    @pytest.mark.asyncio
    async def test_processes_tracked_tickers_only(self, runner, mock_settings):
        """Only tickers present in historical_moves are processed."""
        today = "2026-02-09"
        earnings = _make_earnings(["AAPL", "CUIRF", "NVDA"], report_date=today)

        mock_repo = MagicMock()
        mock_repo.get_tracked_tickers.return_value = {"AAPL", "NVDA"}
        mock_repo.get_average_move.return_value = 4.5

        runner._tradier.get_quote.return_value = {"last": 180.0}

        with patch("src.jobs.handlers.fetch_earnings_with_db_fallback", new_callable=AsyncMock, return_value=earnings), \
             patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.handlers.now_et") as mock_now, \
             patch("src.jobs.base.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.base.today_et", return_value=today), \
             patch("src.jobs.base.now_et") as mock_base_now, \
             patch("src.jobs.base.settings", mock_settings):
            mock_now.return_value = MagicMock(strftime=MagicMock(return_value=today))
            mock_base_now.return_value = ET.localize(datetime(2026, 2, 9, 10, 0, 0))

            result = await runner._pre_market_prep()

        assert result["status"] == "success"
        # CUIRF should be filtered out
        assert result["tickers_found"] == 2

    @pytest.mark.asyncio
    async def test_no_price_skips_ticker(self, runner, mock_settings):
        """Tickers without price data are skipped."""
        today = "2026-02-09"
        earnings = _make_earnings(["AAPL"], report_date=today)

        mock_repo = MagicMock()
        mock_repo.get_tracked_tickers.return_value = {"AAPL"}
        mock_repo.get_average_move.return_value = 4.5

        # Return None price
        runner._tradier.get_quote.return_value = {"last": None, "close": None, "prevclose": None}

        with patch("src.jobs.handlers.fetch_earnings_with_db_fallback", new_callable=AsyncMock, return_value=earnings), \
             patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.handlers.now_et") as mock_now, \
             patch("src.jobs.base.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.base.today_et", return_value=today), \
             patch("src.jobs.base.now_et") as mock_base_now, \
             patch("src.jobs.base.settings", mock_settings):
            mock_now.return_value = MagicMock(strftime=MagicMock(return_value=today))
            mock_base_now.return_value = ET.localize(datetime(2026, 2, 9, 10, 0, 0))

            result = await runner._pre_market_prep()

        assert result["tickers_found"] == 0

    @pytest.mark.asyncio
    async def test_no_historical_skips_ticker(self, runner, mock_settings):
        """Tickers without historical average move are skipped."""
        today = "2026-02-09"
        earnings = _make_earnings(["AAPL"], report_date=today)

        mock_repo = MagicMock()
        mock_repo.get_tracked_tickers.return_value = {"AAPL"}
        mock_repo.get_average_move.return_value = None  # No historical data

        with patch("src.jobs.handlers.fetch_earnings_with_db_fallback", new_callable=AsyncMock, return_value=earnings), \
             patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.handlers.now_et") as mock_now, \
             patch("src.jobs.base.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.base.today_et", return_value=today), \
             patch("src.jobs.base.now_et") as mock_base_now, \
             patch("src.jobs.base.settings", mock_settings):
            mock_now.return_value = MagicMock(strftime=MagicMock(return_value=today))
            mock_base_now.return_value = ET.localize(datetime(2026, 2, 9, 10, 0, 0))

            result = await runner._pre_market_prep()

        assert result["tickers_found"] == 0
        runner._tradier.get_quote.assert_not_called()

    @pytest.mark.asyncio
    async def test_ticker_exception_tracked_as_failure(self, runner, mock_settings):
        """When a ticker raises an exception, it's tracked in failed_tickers."""
        today = "2026-02-09"
        earnings = _make_earnings(["AAPL", "NVDA"], report_date=today)

        mock_repo = MagicMock()
        mock_repo.get_tracked_tickers.return_value = {"AAPL", "NVDA"}
        mock_repo.get_average_move.return_value = 4.5

        # First ticker succeeds, second raises
        runner._tradier.get_quote.side_effect = [
            {"last": 180.0},
            Exception("Tradier timeout"),
        ]

        with patch("src.jobs.handlers.fetch_earnings_with_db_fallback", new_callable=AsyncMock, return_value=earnings), \
             patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.handlers.now_et") as mock_now, \
             patch("src.jobs.base.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.base.today_et", return_value=today), \
             patch("src.jobs.base.now_et") as mock_base_now, \
             patch("src.jobs.base.settings", mock_settings):
            mock_now.return_value = MagicMock(strftime=MagicMock(return_value=today))
            mock_base_now.return_value = ET.localize(datetime(2026, 2, 9, 10, 0, 0))

            result = await runner._pre_market_prep()

        assert result["tickers_found"] == 1
        assert "NVDA" in result.get("failed_tickers", [])


# ---------------------------------------------------------------------------
# _sentiment_scan
# ---------------------------------------------------------------------------

class TestSentimentScan:
    """Tests for the _sentiment_scan handler."""

    @pytest.mark.asyncio
    async def test_empty_calendar_returns_success(self, runner, mock_settings):
        """Empty calendar returns success (not warning) with zero candidates."""
        runner._alphavantage.get_earnings_calendar.return_value = []

        result = await runner._sentiment_scan()

        assert result["status"] == "success"
        assert result["candidates"] == 0
        assert result["primed"] == 0

    @pytest.mark.asyncio
    async def test_already_cached_tickers_skipped(self, runner, mock_settings):
        """Tickers with existing cached sentiment are skipped."""
        today = "2026-02-09"
        runner._alphavantage.get_earnings_calendar.return_value = _make_earnings(
            ["AAPL"], report_date=today
        )

        mock_repo = MagicMock()
        mock_repo.get_tracked_tickers.return_value = {"AAPL"}

        mock_cache = MagicMock()
        # Already cached
        mock_cache.get_sentiment.return_value = {"score": 0.6, "direction": "NEUTRAL"}

        with patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.handlers.now_et") as mock_now, \
             patch("src.jobs.base.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.handlers.SentimentCacheRepository", return_value=mock_cache), \
             patch("src.jobs.base.today_et", return_value=today), \
             patch("src.jobs.base.now_et") as mock_base_now, \
             patch("src.jobs.base.settings", mock_settings):
            mock_now.return_value = MagicMock(strftime=MagicMock(return_value=today))
            mock_base_now.return_value = ET.localize(datetime(2026, 2, 9, 10, 0, 0))

            result = await runner._sentiment_scan()

        assert result["candidates"] == 0
        assert result["primed"] == 0
        runner._perplexity.get_sentiment.assert_not_called()

    @pytest.mark.asyncio
    async def test_low_vrp_tickers_not_primed(self, runner, mock_settings):
        """Tickers with VRP below VRP_DISCOVERY are not added to candidates."""
        today = "2026-02-09"
        runner._alphavantage.get_earnings_calendar.return_value = _make_earnings(
            ["AAPL"], report_date=today
        )

        mock_repo = MagicMock()
        mock_repo.get_tracked_tickers.return_value = {"AAPL"}
        mock_repo.get_moves.return_value = [
            {"intraday_move_pct": 3.0},
            {"intraday_move_pct": 5.0},
            {"intraday_move_pct": 4.0},
            {"intraday_move_pct": 2.0},
        ]

        mock_cache = MagicMock()
        mock_cache.get_sentiment.return_value = None

        # VRP below discovery threshold (1.8)
        mock_im_result = {
            "implied_move_pct": 4.0,
            "used_real_data": True,
            "price": 150.0,
            "has_weekly_options": True,
        }

        with patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.handlers.now_et") as mock_now, \
             patch("src.jobs.base.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.handlers.SentimentCacheRepository", return_value=mock_cache), \
             patch("src.jobs.handlers.calculate_vrp", return_value={"vrp_ratio": 1.1, "tier": "SKIP"}), \
             patch("src.jobs.handlers.fetch_real_implied_move", new_callable=AsyncMock, return_value=mock_im_result), \
             patch("src.jobs.handlers.get_implied_move_with_fallback", return_value=(4.0, True)), \
             patch("src.jobs.base.today_et", return_value=today), \
             patch("src.jobs.base.now_et") as mock_base_now, \
             patch("src.jobs.base.settings", mock_settings), \
             patch("src.jobs.base.fetch_real_implied_move", new_callable=AsyncMock, return_value=mock_im_result), \
             patch("src.jobs.base.get_implied_move_with_fallback", return_value=(4.0, True)), \
             patch("src.jobs.base.calculate_vrp", return_value={"vrp_ratio": 1.1, "tier": "SKIP"}):
            mock_now.return_value = MagicMock(strftime=MagicMock(return_value=today))
            mock_base_now.return_value = ET.localize(datetime(2026, 2, 9, 10, 0, 0))

            result = await runner._sentiment_scan()

        assert result["candidates"] == 0
        assert result["primed"] == 0


# ---------------------------------------------------------------------------
# _morning_digest
# ---------------------------------------------------------------------------

class TestMorningDigest:
    """Tests for the _morning_digest handler."""

    @pytest.mark.asyncio
    async def test_empty_calendar_sends_warning_telegram(self, runner, mock_settings):
        """Empty earnings calendar (DB + AV both empty) sends Telegram warning."""
        runner._alphavantage.get_earnings_calendar.return_value = []
        runner._telegram.send_message.return_value = True

        mock_repo = MagicMock()
        mock_repo.get_upcoming_earnings.return_value = []

        with patch("src.jobs.handlers.today_et", return_value="2026-02-09"), \
             patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo):
            result = await runner._morning_digest()

        assert result["status"] == "warning"
        assert result["opportunities"] == 0
        assert result["sent"] is False
        # Should attempt to send a warning message
        runner._telegram.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_opportunities_skips_telegram(self, runner, mock_settings):
        """When no tickers pass VRP filter, no digest is sent."""
        today = "2026-02-09"

        mock_repo = MagicMock()
        mock_repo.get_upcoming_earnings.return_value = _make_earnings(["AAPL"], report_date=today)
        mock_repo.get_tracked_tickers.return_value = {"AAPL"}
        mock_repo.get_moves.return_value = [
            {"intraday_move_pct": 3.0},
            {"intraday_move_pct": 5.0},
            {"intraday_move_pct": 4.0},
            {"intraday_move_pct": 2.0},
        ]

        mock_cache = MagicMock()
        mock_cache.get_sentiment.return_value = None

        # Low VRP - below discovery threshold
        mock_im_result = {
            "implied_move_pct": 4.0,
            "used_real_data": True,
            "price": 150.0,
            "has_weekly_options": True,
            "expiration": "2026-02-14",
        }

        real_now = ET.localize(datetime(2026, 2, 9, 7, 30, 0))

        with patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.handlers.now_et", return_value=real_now), \
             patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.base.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.handlers.SentimentCacheRepository", return_value=mock_cache), \
             patch("src.jobs.handlers.calculate_vrp", return_value={"vrp_ratio": 1.1, "tier": "SKIP"}), \
             patch("src.jobs.handlers.fetch_real_implied_move", new_callable=AsyncMock, return_value=mock_im_result), \
             patch("src.jobs.handlers.get_implied_move_with_fallback", return_value=(4.0, True)), \
             patch("src.jobs.base.today_et", return_value=today), \
             patch("src.jobs.base.now_et", return_value=real_now), \
             patch("src.jobs.base.settings", mock_settings), \
             patch("src.jobs.base.fetch_real_implied_move", new_callable=AsyncMock, return_value=mock_im_result), \
             patch("src.jobs.base.get_implied_move_with_fallback", return_value=(4.0, True)), \
             patch("src.jobs.base.calculate_vrp", return_value={"vrp_ratio": 1.1, "tier": "SKIP"}):

            result = await runner._morning_digest()

        assert result["status"] == "success"
        assert result["opportunities"] == 0
        # No digest sent when no opportunities
        runner._telegram.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_telegram_error_tracked_in_result(self, runner, mock_settings):
        """Telegram send failure is captured in result, not raised."""
        today = "2026-02-09"

        mock_repo = MagicMock()
        mock_repo.get_upcoming_earnings.return_value = _make_earnings(["AAPL"], report_date=today)
        mock_repo.get_tracked_tickers.return_value = {"AAPL"}
        mock_repo.get_moves.return_value = [
            {"intraday_move_pct": 3.0},
            {"intraday_move_pct": 5.0},
            {"intraday_move_pct": 4.0},
            {"intraday_move_pct": 2.0},
        ]

        mock_cache = MagicMock()
        mock_cache.get_sentiment.return_value = None

        mock_im_result = {
            "implied_move_pct": 8.0,
            "used_real_data": True,
            "price": 150.0,
            "has_weekly_options": True,
            "expiration": "2026-02-14",
        }

        runner._telegram.send_message.side_effect = ConnectionError("Telegram timeout")

        real_now = ET.localize(datetime(2026, 2, 9, 7, 30, 0))

        with patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.handlers.now_et", return_value=real_now), \
             patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.base.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.handlers.SentimentCacheRepository", return_value=mock_cache), \
             patch("src.jobs.handlers.calculate_vrp", return_value={"vrp_ratio": 2.5, "tier": "EXCELLENT"}), \
             patch("src.jobs.handlers.calculate_score", return_value={"total_score": 80}), \
             patch("src.jobs.handlers.apply_sentiment_modifier", return_value=82), \
             patch("src.jobs.handlers.generate_strategies", return_value=[]), \
             patch("src.jobs.handlers.get_direction", return_value="NEUTRAL"), \
             patch("src.jobs.handlers.fetch_real_implied_move", new_callable=AsyncMock, return_value=mock_im_result), \
             patch("src.jobs.handlers.get_implied_move_with_fallback", return_value=(8.0, True)), \
             patch("src.jobs.handlers.format_digest", return_value="<b>Test Digest</b>"), \
             patch("src.jobs.base.today_et", return_value=today), \
             patch("src.jobs.base.now_et", return_value=real_now), \
             patch("src.jobs.base.settings", mock_settings), \
             patch("src.jobs.base.fetch_real_implied_move", new_callable=AsyncMock, return_value=mock_im_result), \
             patch("src.jobs.base.get_implied_move_with_fallback", return_value=(8.0, True)), \
             patch("src.jobs.base.calculate_vrp", return_value={"vrp_ratio": 2.5, "tier": "EXCELLENT"}):

            result = await runner._morning_digest()

        assert result["status"] == "success"
        assert "telegram_error" in result
        assert "Telegram timeout" in result["telegram_error"]


# ---------------------------------------------------------------------------
# _after_hours_check
# ---------------------------------------------------------------------------

class TestAfterHoursCheck:
    """Tests for the _after_hours_check handler."""

    @pytest.mark.asyncio
    async def test_no_qualified_candidates_returns_early(self, runner, mock_settings):
        """Returns early when no tickers were qualified by digest/pre-trade."""
        with patch("src.jobs.handlers.today_et", return_value="2026-02-25"), \
             patch("src.jobs.base.BaseJobHandler._get_daily_candidates", return_value=set()):

            result = await runner._after_hours_check()

        assert result["status"] == "success"
        assert result["checked"] == 0
        assert "No qualified candidates" in result["note"]
        # Should NOT call earnings API
        runner._alphavantage.get_earnings_calendar.assert_not_called()

    @pytest.mark.asyncio
    async def test_filters_to_qualified_tickers_only(self, runner, mock_settings):
        """Only tickers from daily_candidates are checked, micro-caps excluded."""
        today = "2026-02-25"
        runner._alphavantage.get_earnings_calendar.return_value = [
            {"symbol": "CRM", "report_date": today},
            {"symbol": "EDXC", "report_date": today},  # micro-cap, not in candidates
            {"symbol": "ENSV", "report_date": today},  # micro-cap, not in candidates
        ]

        # CRM qualified in morning digest; EDXC and ENSV did not
        qualified = {"CRM", "SNOW"}

        mock_repo = MagicMock()
        mock_repo.get_moves.return_value = [
            {"intraday_move_pct": 5.0},
            {"intraday_move_pct": 3.0},
            {"intraday_move_pct": 4.0},
            {"intraday_move_pct": 6.0},
        ]

        runner._tradier.get_quote = AsyncMock(return_value={"last": 205.0})
        runner._twelvedata.get_stock_history = AsyncMock(return_value={
            "Close": {"2026-02-25": 200.0}
        })

        with patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.base.today_et", return_value=today), \
             patch("src.jobs.base.BaseJobHandler._get_daily_candidates", return_value=qualified), \
             patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.base.settings", mock_settings):

            result = await runner._after_hours_check()

        # Only CRM should be checked (SNOW not in today's earnings, EDXC/ENSV not qualified)
        assert result["checked"] == 1
        assert result["reported"] == 1

    @pytest.mark.asyncio
    async def test_empty_calendar_returns_warning(self, runner, mock_settings):
        """Empty earnings calendar returns warning."""
        runner._alphavantage.get_earnings_calendar.return_value = []

        with patch("src.jobs.handlers.today_et", return_value="2026-02-25"), \
             patch("src.jobs.base.BaseJobHandler._get_daily_candidates", return_value={"CRM"}):

            result = await runner._after_hours_check()

        assert result["status"] == "warning"


# ---------------------------------------------------------------------------
# _outcome_recorder
# ---------------------------------------------------------------------------

class TestOutcomeRecorder:
    """Tests for the _outcome_recorder handler."""

    @pytest.mark.asyncio
    async def test_empty_calendar_returns_warning(self, runner, mock_settings):
        """Empty earnings calendar returns warning."""
        runner._alphavantage.get_earnings_calendar.return_value = []

        mock_repo = MagicMock()
        mock_repo.get_upcoming_earnings.return_value = []

        real_now = ET.localize(datetime(2026, 2, 9, 19, 0, 0))

        with patch("src.jobs.handlers.today_et", return_value="2026-02-09"), \
             patch("src.jobs.handlers.now_et", return_value=real_now), \
             patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo):

            result = await runner._outcome_recorder()

        assert result["status"] == "warning"
        assert result["recorded"] == 0

    @pytest.mark.asyncio
    async def test_bmo_timing_uses_correct_dates(self, runner, mock_settings):
        """BMO earnings uses prev_day_close -> earnings_day_close."""
        today = "2026-02-09"

        runner._alphavantage.get_earnings_calendar.return_value = [
            {"symbol": "AAPL", "report_date": today, "timing": "BMO"},
        ]

        mock_repo = MagicMock()
        mock_repo.get_upcoming_earnings.return_value = []
        mock_repo.get_moves.return_value = []  # No existing record

        # TwelveData returns price history
        runner._twelvedata.get_stock_history.return_value = {
            "Close": {
                "2026-02-07": 148.0,
                "2026-02-08": 150.0,   # yesterday (reference for BMO)
                "2026-02-09": 155.0,   # today (reaction)
            }
        }

        real_now = ET.localize(datetime(2026, 2, 9, 19, 0, 0))

        with patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.handlers.now_et", return_value=real_now), \
             patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo):

            result = await runner._outcome_recorder()

        assert result["status"] == "success"
        assert result["recorded"] == 1

        # Verify save_move was called with correct data
        save_call = mock_repo.save_move.call_args[0][0]
        assert save_call["ticker"] == "AAPL"
        assert save_call["earnings_date"] == today
        # Move = (155 - 150) / 150 * 100 = 3.3333
        assert abs(save_call["gap_move_pct"] - 3.3333) < 0.01

    @pytest.mark.asyncio
    async def test_duplicate_earnings_skipped(self, runner, mock_settings):
        """Already recorded earnings are skipped."""
        today = "2026-02-09"

        runner._alphavantage.get_earnings_calendar.return_value = [
            {"symbol": "AAPL", "report_date": today, "timing": "BMO"},
        ]

        mock_repo = MagicMock()
        mock_repo.get_upcoming_earnings.return_value = []
        # Already has this earnings recorded
        mock_repo.get_moves.return_value = [
            {"earnings_date": today, "intraday_move_pct": 3.5},
        ]

        real_now = ET.localize(datetime(2026, 2, 9, 19, 0, 0))

        with patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.handlers.now_et", return_value=real_now), \
             patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo):

            result = await runner._outcome_recorder()

        assert result["recorded"] == 0
        assert result["skipped_duplicate"] == 1
        mock_repo.save_move.assert_not_called()

    @pytest.mark.asyncio
    async def test_amc_yesterday_recorded_today(self, runner, mock_settings):
        """Yesterday's AMC earnings are recorded with today's reaction."""
        today = "2026-02-09"
        yesterday = "2026-02-08"

        runner._alphavantage.get_earnings_calendar.return_value = [
            {"symbol": "MSFT", "report_date": yesterday, "timing": "AMC"},
        ]

        mock_repo = MagicMock()
        mock_repo.get_upcoming_earnings.return_value = []
        mock_repo.get_moves.return_value = []

        runner._twelvedata.get_stock_history.return_value = {
            "Close": {
                "2026-02-07": 400.0,
                "2026-02-08": 405.0,   # yesterday (reference for AMC)
                "2026-02-09": 410.0,   # today (reaction)
            }
        }

        real_now = ET.localize(datetime(2026, 2, 9, 19, 0, 0))

        with patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.handlers.now_et", return_value=real_now), \
             patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo):

            result = await runner._outcome_recorder()

        assert result["recorded"] == 1

        save_call = mock_repo.save_move.call_args[0][0]
        assert save_call["ticker"] == "MSFT"
        assert save_call["earnings_date"] == yesterday
        # Move = (410 - 405) / 405 * 100 = 1.2346
        assert abs(save_call["gap_move_pct"] - 1.2346) < 0.01

    @pytest.mark.asyncio
    async def test_invalid_ticker_filtered_out(self, runner, mock_settings):
        """Invalid ticker formats (preferred stocks, warrants) are filtered."""
        today = "2026-02-09"

        runner._alphavantage.get_earnings_calendar.return_value = [
            {"symbol": "COF-PI", "report_date": today, "timing": "BMO"},  # Preferred stock
            {"symbol": "ACHR+", "report_date": today, "timing": "BMO"},   # Warrant
        ]

        mock_repo = MagicMock()
        mock_repo.get_upcoming_earnings.return_value = []

        real_now = ET.localize(datetime(2026, 2, 9, 19, 0, 0))

        with patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.handlers.now_et", return_value=real_now), \
             patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo):

            result = await runner._outcome_recorder()

        assert result["recorded"] == 0
        mock_repo.save_move.assert_not_called()

    @pytest.mark.asyncio
    async def test_insufficient_price_data_skipped(self, runner, mock_settings):
        """Tickers with fewer than 2 price data points are skipped."""
        today = "2026-02-09"

        runner._alphavantage.get_earnings_calendar.return_value = [
            {"symbol": "AAPL", "report_date": today, "timing": "BMO"},
        ]

        mock_repo = MagicMock()
        mock_repo.get_upcoming_earnings.return_value = []
        mock_repo.get_moves.return_value = []

        # Only one data point
        runner._twelvedata.get_stock_history.return_value = {
            "Close": {"2026-02-09": 155.0}
        }

        real_now = ET.localize(datetime(2026, 2, 9, 19, 0, 0))

        with patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.handlers.now_et", return_value=real_now), \
             patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo):

            result = await runner._outcome_recorder()

        assert result["recorded"] == 0


# ---------------------------------------------------------------------------
# _weekly_backup
# ---------------------------------------------------------------------------

class TestWeeklyBackup:
    """Tests for the _weekly_backup handler.

    Note: _weekly_backup uses local imports (from pathlib import Path) inside the
    method body, so patching src.jobs.handlers.Path does NOT work. We must use real
    temp files and mock sqlite3 at the module level for integrity check interception.
    """

    @pytest.mark.asyncio
    async def test_no_db_file_returns_success_not_backed_up(self, runner, mock_settings):
        """When DB file doesn't exist, returns success with backed_up=False."""
        mock_settings.DB_PATH = "/nonexistent/path/ivcrush.db"

        result = await runner._weekly_backup()

        assert result["status"] == "success"
        assert result["backed_up"] is False

    @pytest.mark.asyncio
    async def test_integrity_check_failure_returns_error(self, runner, mock_settings):
        """Database integrity check failure returns error status."""
        # Create a real temp file so Path.exists() returns True
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name

        try:
            mock_settings.DB_PATH = tmp_path

            with patch("src.jobs.handlers.sqlite3") as mock_sqlite:
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_cursor.fetchone.return_value = ("corrupt page found",)
                mock_conn.execute.return_value = mock_cursor
                mock_sqlite.connect.return_value = mock_conn

                result = await runner._weekly_backup()

            assert result["status"] == "error"
            assert "integrity check failed" in result["error"]
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_successful_backup_returns_blob_name(self, runner, mock_settings):
        """Successful backup returns blob name and backed_up=True."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name

        try:
            mock_settings.DB_PATH = tmp_path

            real_now = ET.localize(datetime(2026, 2, 7, 3, 0, 0))

            with patch("src.jobs.handlers.sqlite3") as mock_sqlite, \
                 patch("src.jobs.handlers.now_et", return_value=real_now), \
                 patch("src.jobs.handlers.DatabaseSync") as mock_sync_cls, \
                 patch("src.jobs.handlers.shutil"):
                # Integrity passes
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_cursor.fetchone.return_value = ("ok",)
                mock_conn.execute.return_value = mock_cursor
                mock_sqlite.connect.return_value = mock_conn

                # Upload succeeds
                mock_sync = MagicMock()
                mock_sync.upload.return_value = True
                mock_sync.local_path = "/tmp/sync_path"
                mock_sync_cls.return_value = mock_sync

                result = await runner._weekly_backup()

            assert result["status"] == "success"
            assert result["backed_up"] is True
            assert "backups/" in result["blob"]
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_upload_conflict_still_returns_success(self, runner, mock_settings):
        """Upload conflict (DatabaseSyncConflictError) is logged as warn but still returns success."""
        from src.core.database import DatabaseSyncConflictError

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tmp_path = f.name

        try:
            mock_settings.DB_PATH = tmp_path

            real_now = ET.localize(datetime(2026, 2, 7, 3, 0, 0))

            with patch("src.jobs.handlers.sqlite3") as mock_sqlite, \
                 patch("src.jobs.handlers.now_et", return_value=real_now), \
                 patch("src.jobs.handlers.DatabaseSync") as mock_sync_cls, \
                 patch("src.jobs.handlers.shutil"):
                mock_conn = MagicMock()
                mock_cursor = MagicMock()
                mock_cursor.fetchone.return_value = ("ok",)
                mock_conn.execute.return_value = mock_cursor
                mock_sqlite.connect.return_value = mock_conn

                mock_sync = MagicMock()
                mock_sync.upload.side_effect = DatabaseSyncConflictError("conflict")
                mock_sync.local_path = "/tmp/sync_path"
                mock_sync_cls.return_value = mock_sync

                result = await runner._weekly_backup()

            assert result["status"] == "success"
            assert result["backed_up"] is True
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# _weekly_cleanup
# ---------------------------------------------------------------------------

class TestWeeklyCleanup:
    """Tests for the _weekly_cleanup handler."""

    @pytest.mark.asyncio
    async def test_clears_expired_cache(self, runner, mock_settings):
        """Calls clear_expired and returns count."""
        mock_cache = MagicMock()
        mock_cache.clear_expired.return_value = 42

        with patch("src.jobs.handlers.SentimentCacheRepository", return_value=mock_cache):
            result = await runner._weekly_cleanup()

        assert result["status"] == "success"
        assert result["cleared"] == 42

    @pytest.mark.asyncio
    async def test_cleanup_exception_returns_error(self, runner, mock_settings):
        """Cache clearing exceptions are caught and returned."""
        mock_cache = MagicMock()
        mock_cache.clear_expired.side_effect = Exception("DB locked")

        with patch("src.jobs.handlers.SentimentCacheRepository", return_value=mock_cache):
            result = await runner._weekly_cleanup()

        assert result["status"] == "error"
        assert "DB locked" in result["error"]


# ---------------------------------------------------------------------------
# _calendar_sync
# ---------------------------------------------------------------------------

class TestCalendarSync:
    """Tests for the _calendar_sync handler."""

    @pytest.mark.asyncio
    async def test_empty_calendar_returns_warning(self, runner, mock_settings):
        """Empty calendar returns warning status."""
        runner._alphavantage.get_earnings_calendar.return_value = []

        with patch("src.jobs.base.settings", mock_settings):
            result = await runner._calendar_sync()

        assert result["status"] == "warning"
        assert result["synced"] == 0

    @pytest.mark.asyncio
    async def test_upserts_and_uploads_to_gcs(self, runner, mock_settings):
        """Calendar sync upserts earnings and uploads to GCS."""
        earnings_data = _make_earnings(["AAPL", "MSFT", "NVDA"], report_date="2026-03-15")
        runner._alphavantage.get_earnings_calendar.return_value = earnings_data

        mock_repo = MagicMock()
        mock_repo.upsert_earnings_calendar.return_value = 3

        mock_sync = MagicMock()
        mock_sync.upload.return_value = True
        mock_sync.local_path = "/tmp/sync_path"

        with patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.handlers.DatabaseSync", return_value=mock_sync), \
             patch("src.jobs.handlers.shutil"), \
             patch("src.jobs.handlers.Path"), \
             patch("src.jobs.base.settings", mock_settings):

            result = await runner._calendar_sync()

        assert result["status"] == "success"
        assert result["fetched"] == 3
        assert result["synced"] == 3
        assert result["gcs_uploaded"] is True

    @pytest.mark.asyncio
    async def test_gcs_upload_failure_still_succeeds(self, runner, mock_settings):
        """GCS upload failure does not fail the whole job."""
        earnings_data = _make_earnings(["AAPL"], report_date="2026-03-15")
        runner._alphavantage.get_earnings_calendar.return_value = earnings_data

        mock_repo = MagicMock()
        mock_repo.upsert_earnings_calendar.return_value = 1

        with patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.handlers.DatabaseSync", side_effect=Exception("GCS error")), \
             patch("src.jobs.handlers.Path"), \
             patch("src.jobs.handlers.shutil"), \
             patch("src.jobs.base.settings", mock_settings):

            result = await runner._calendar_sync()

        assert result["status"] == "success"
        assert result["synced"] == 1
        assert result["gcs_uploaded"] is False


# ---------------------------------------------------------------------------
# JobRunner.run dispatch
# ---------------------------------------------------------------------------

class TestJobRunnerDispatch:
    """Tests for the JobRunner.run dispatch mechanism."""

    @pytest.mark.asyncio
    async def test_unknown_job_returns_error(self, runner):
        """Unknown job name returns error with descriptive message."""
        result = await runner.run("nonexistent-job")

        assert result["status"] == "error"
        assert "Unknown job: nonexistent-job" in result["error"]

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, runner):
        """Job timeout is caught and reported."""
        async def slow_handler():
            await asyncio.sleep(10)

        with patch.object(runner, "_pre_market_prep", side_effect=slow_handler):
            # Override JOB_TIMEOUT_SECONDS to a very small value for the test
            with patch("src.jobs.handlers.asyncio.wait_for", side_effect=asyncio.TimeoutError):
                result = await runner.run("pre-market-prep")

        assert result["status"] == "error"
        assert "timed out" in result["error"]

    @pytest.mark.asyncio
    async def test_exception_returns_error_with_message(self, runner):
        """Handler exceptions are caught and their message returned."""
        with patch.object(runner, "_morning_digest", new_callable=AsyncMock) as mock_job:
            mock_job.side_effect = ValueError("Database connection failed")

            result = await runner.run("morning-digest")

        assert result["status"] == "error"
        assert "Database connection failed" in result["error"]

    @pytest.mark.asyncio
    async def test_all_12_jobs_registered(self, runner):
        """All 12 scheduled jobs are present in the handler map."""
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

        for job_name in expected_jobs:
            handler_attr = f"_{job_name.replace('-', '_')}"
            with patch.object(runner, handler_attr, new_callable=AsyncMock) as mock_job:
                mock_job.return_value = {"status": "success"}
                result = await runner.run(job_name)
                assert result["status"] == "success", f"Job {job_name} not registered"


# ---------------------------------------------------------------------------
# _weekly_backfill
# ---------------------------------------------------------------------------

class TestWeeklyBackfill:
    """Tests for the _weekly_backfill handler."""

    @pytest.mark.asyncio
    async def test_empty_calendar_returns_warning(self, runner, mock_settings):
        """Empty earnings returns warning."""
        runner._alphavantage.get_earnings_calendar.return_value = []

        mock_repo = MagicMock()
        mock_repo.get_upcoming_earnings.return_value = []

        real_now = ET.localize(datetime(2026, 2, 9, 4, 0, 0))

        with patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.handlers.today_et", return_value="2026-02-09"), \
             patch("src.jobs.handlers.now_et", return_value=real_now):
            result = await runner._weekly_backfill()

        assert result["status"] == "warning"
        assert result["backfilled"] == 0

    @pytest.mark.asyncio
    async def test_bmo_backfill_uses_prev_day_reference(self, runner, mock_settings):
        """BMO earnings use prev_day_close as reference for move calculation."""
        # Earnings from 3 days ago
        earnings_date = "2026-02-06"
        runner._alphavantage.get_earnings_calendar.return_value = [
            {"symbol": "AAPL", "report_date": earnings_date, "timing": "BMO"},
        ]

        mock_repo = MagicMock()
        mock_repo.get_upcoming_earnings.return_value = []
        mock_repo.get_moves.return_value = []  # No existing record

        # Price data around earnings
        runner._twelvedata.get_stock_history.return_value = {
            "Close": {
                "2026-02-05": 148.0,  # prev day (reference for BMO)
                "2026-02-06": 155.0,  # earnings day (reaction)
                "2026-02-07": 154.0,
            }
        }

        # now_et() returns Feb 9 (3 days after earnings)
        mock_now_val = ET.localize(datetime(2026, 2, 9, 19, 0, 0))

        with patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.handlers.now_et", return_value=mock_now_val), \
             patch("src.jobs.handlers.MARKET_TZ", ET), \
             patch("src.jobs.handlers.today_et", return_value="2026-02-09"):
            result = await runner._weekly_backfill()

        assert result["backfilled"] == 1

        save_call = mock_repo.save_move.call_args[0][0]
        # BMO: reference = prev day (148), reaction = earnings day (155)
        # Move = (155 - 148) / 148 * 100 = 4.7297
        assert abs(save_call["gap_move_pct"] - 4.7297) < 0.01

    @pytest.mark.asyncio
    async def test_amc_backfill_uses_next_day_reaction(self, runner, mock_settings):
        """AMC earnings use next_day as reaction day."""
        earnings_date = "2026-02-06"
        runner._alphavantage.get_earnings_calendar.return_value = [
            {"symbol": "MSFT", "report_date": earnings_date, "timing": "AMC"},
        ]

        mock_repo = MagicMock()
        mock_repo.get_upcoming_earnings.return_value = []
        mock_repo.get_moves.return_value = []

        runner._twelvedata.get_stock_history.return_value = {
            "Close": {
                "2026-02-05": 400.0,
                "2026-02-06": 405.0,  # earnings day (reference for AMC)
                "2026-02-07": 415.0,  # next day (reaction)
            }
        }

        mock_now_val = ET.localize(datetime(2026, 2, 9, 4, 0, 0))

        with patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.handlers.now_et", return_value=mock_now_val), \
             patch("src.jobs.handlers.MARKET_TZ", ET), \
             patch("src.jobs.handlers.today_et", return_value="2026-02-09"):
            result = await runner._weekly_backfill()

        assert result["backfilled"] == 1

        save_call = mock_repo.save_move.call_args[0][0]
        # AMC: reference = earnings day (405), reaction = next day (415)
        # Move = (415 - 405) / 405 * 100 = 2.4691
        assert abs(save_call["gap_move_pct"] - 2.4691) < 0.01

    @pytest.mark.asyncio
    async def test_duplicate_backfill_skipped(self, runner, mock_settings):
        """Earnings already recorded in historical_moves are skipped."""
        earnings_date = "2026-02-06"
        runner._alphavantage.get_earnings_calendar.return_value = [
            {"symbol": "AAPL", "report_date": earnings_date, "timing": "BMO"},
        ]

        mock_repo = MagicMock()
        mock_repo.get_upcoming_earnings.return_value = []
        # Already has this earnings date
        mock_repo.get_moves.return_value = [
            {"earnings_date": earnings_date, "intraday_move_pct": 4.5},
        ]

        mock_now_val = ET.localize(datetime(2026, 2, 9, 4, 0, 0))

        with patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.handlers.now_et", return_value=mock_now_val), \
             patch("src.jobs.handlers.MARKET_TZ", ET), \
             patch("src.jobs.handlers.today_et", return_value="2026-02-09"):
            result = await runner._weekly_backfill()

        assert result["backfilled"] == 0
        assert result["skipped_duplicate"] == 1


# ---------------------------------------------------------------------------
# _evening_summary
# ---------------------------------------------------------------------------

class TestEveningSummary:
    """Tests for the _evening_summary handler."""

    @pytest.mark.asyncio
    async def test_no_earnings_today_skips_summary(self, runner, mock_settings):
        """When no earnings today, evening summary is skipped (no Telegram)."""
        today = "2026-02-09"
        # Return earnings for different date
        runner._alphavantage.get_earnings_calendar.return_value = _make_earnings(
            ["AAPL"], report_date="2026-02-10"
        )

        mock_repo = MagicMock()
        mock_repo.get_tracked_tickers.return_value = {"AAPL"}

        with patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.handlers.filter_to_tracked_tickers") as mock_filter:
            mock_filter.return_value = []

            result = await runner._evening_summary()

        assert result["status"] == "success"
        assert result["sent"] is False
        assert result["earnings_today"] == 0
        runner._telegram.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_earnings_with_recorded_moves_sends_summary(self, runner, mock_settings):
        """Sends summary when there are today's earnings with recorded outcomes."""
        today = "2026-02-09"
        runner._alphavantage.get_earnings_calendar.return_value = _make_earnings(
            ["AAPL", "NVDA"], report_date=today
        )

        mock_repo = MagicMock()
        mock_repo.get_tracked_tickers.return_value = {"AAPL", "NVDA"}
        # AAPL has a recorded move, NVDA doesn't
        mock_repo.get_moves.side_effect = [
            [{"earnings_date": today, "intraday_move_pct": 3.5}],  # AAPL
            [],  # NVDA - no recorded move yet
        ]

        runner._telegram.send_message.return_value = True

        with patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.handlers.filter_to_tracked_tickers") as mock_filter:
            mock_filter.return_value = _make_earnings(["AAPL", "NVDA"], report_date=today)

            result = await runner._evening_summary()

        assert result["status"] == "success"
        assert result["sent"] is True
        assert result["earnings_today"] == 2
        runner._telegram.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_earnings_no_outcomes_yet_skips_summary(self, runner, mock_settings):
        """When earnings today but no recorded outcomes, summary is skipped."""
        today = "2026-02-09"
        runner._alphavantage.get_earnings_calendar.return_value = _make_earnings(
            ["AAPL"], report_date=today
        )

        mock_repo = MagicMock()
        mock_repo.get_tracked_tickers.return_value = {"AAPL"}
        mock_repo.get_moves.return_value = []  # No outcomes recorded yet

        with patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.handlers.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.handlers.filter_to_tracked_tickers") as mock_filter:
            mock_filter.return_value = _make_earnings(["AAPL"], report_date=today)

            result = await runner._evening_summary()

        assert result["status"] == "success"
        assert result["sent"] is False
        runner._telegram.send_message.assert_not_called()


# ---------------------------------------------------------------------------
# _market_open_refresh
# ---------------------------------------------------------------------------

class TestMarketOpenRefresh:
    """Tests for the _market_open_refresh handler."""

    @pytest.mark.asyncio
    async def test_empty_calendar_returns_warning(self, runner, mock_settings):
        """Empty calendar returns warning."""
        runner._alphavantage.get_earnings_calendar.return_value = []

        with patch("src.jobs.base.settings", mock_settings):
            result = await runner._market_open_refresh()

        assert result["status"] == "warning"
        assert result["refreshed"] == 0

    @pytest.mark.asyncio
    async def test_no_earnings_today_returns_success(self, runner, mock_settings):
        """No earnings today returns success with zero refreshed."""
        today = "2026-02-09"
        # Earnings are for tomorrow, not today
        runner._alphavantage.get_earnings_calendar.return_value = _make_earnings(
            ["AAPL"], report_date="2026-02-10"
        )

        mock_repo = MagicMock()
        mock_repo.get_tracked_tickers.return_value = {"AAPL"}

        with patch("src.jobs.handlers.today_et", return_value=today), \
             patch("src.jobs.base.HistoricalMovesRepository", return_value=mock_repo), \
             patch("src.jobs.base.today_et", return_value=today), \
             patch("src.jobs.base.settings", mock_settings):
            result = await runner._market_open_refresh()

        assert result["status"] == "success"
        assert result["refreshed"] == 0


# ---------------------------------------------------------------------------
# BaseJobHandler._evaluate_vrp
# ---------------------------------------------------------------------------

class TestEvaluateVrp:
    """Tests for the BaseJobHandler._evaluate_vrp pipeline."""

    @pytest.mark.asyncio
    async def test_insufficient_historical_data_returns_none(self, runner, mock_settings):
        """Tickers with fewer than 4 historical moves return None."""
        mock_repo = MagicMock()
        mock_repo.get_moves.return_value = [
            {"intraday_move_pct": 3.0},
            {"intraday_move_pct": 5.0},
        ]

        result = await runner._evaluate_vrp(mock_repo, "TINY", "2026-02-09", 0)

        assert result is None

    @pytest.mark.asyncio
    async def test_successful_vrp_evaluation(self, runner, mock_settings):
        """Full VRP evaluation returns expected structure."""
        mock_repo = MagicMock()
        mock_repo.get_moves.return_value = [
            {"intraday_move_pct": 3.0},
            {"intraday_move_pct": 5.0},
            {"intraday_move_pct": 4.0},
            {"intraday_move_pct": 2.0},
        ]

        mock_im_result = {
            "implied_move_pct": 8.0,
            "used_real_data": True,
            "price": 150.0,
        }

        with patch("src.jobs.base.fetch_real_implied_move", new_callable=AsyncMock, return_value=mock_im_result), \
             patch("src.jobs.base.get_implied_move_with_fallback", return_value=(8.0, True)), \
             patch("src.jobs.base.calculate_vrp", return_value={"vrp_ratio": 2.3, "tier": "EXCELLENT"}):

            result = await runner._evaluate_vrp(mock_repo, "AAPL", "2026-02-09", 0)

        assert result is not None
        assert result["vrp_data"]["vrp_ratio"] == 2.3
        assert result["vrp_data"]["tier"] == "EXCELLENT"
        assert result["implied_move_pct"] == 8.0
        assert result["used_real"] is True
        assert result["historical_avg"] == pytest.approx(3.5)
        # API calls should be incremented by TRADIER_CALLS_PER_TICKER (3)
        assert result["api_calls"] == 3


# ---------------------------------------------------------------------------
# DB fallback + empty-calendar regression tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pre_market_prep_empty_calendar_returns_success(runner):
    """Empty earnings calendar must return success, not warning — warning blocks all downstream jobs."""
    with patch("src.jobs.handlers.fetch_earnings_with_db_fallback", new_callable=AsyncMock) as mock_fallback, \
         patch("src.jobs.handlers.HistoricalMovesRepository"):
        mock_fallback.return_value = []
        result = await runner._pre_market_prep()
    assert result["status"] == "success", f"Expected success, got: {result['status']}"
    assert result.get("tickers_found") == 0


@pytest.mark.asyncio
async def test_pre_market_prep_fallback_exception_does_not_propagate(runner):
    """If fetch_earnings_with_db_fallback raises, _pre_market_prep must not propagate it."""
    with patch("src.jobs.handlers.fetch_earnings_with_db_fallback",
               new_callable=AsyncMock) as mock_fallback:
        mock_fallback.side_effect = Exception("AV rate limit + DB also failed")
        result = await runner.run("pre-market-prep")
    # run() catches all exceptions and returns {"status": "error"}; process must not crash
    assert isinstance(result, dict)
    assert result.get("status") == "error"


@pytest.mark.asyncio
async def test_sentiment_scan_empty_calendar_returns_success(runner):
    """Sentiment scan: empty calendar must return success, not warning."""
    runner._alphavantage.get_earnings_calendar = AsyncMock(return_value=[])
    with patch.object(runner, "_fetch_earnings", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = None  # simulates empty
        result = await runner._sentiment_scan()
    assert result["status"] == "success", f"Expected success, got: {result['status']}"
    assert result.get("candidates") == 0
    assert result.get("primed") == 0


def test_upcoming_earnings_uses_single_time_snapshot():
    """_upcoming_earnings must produce contiguous dates from a single snapshot."""
    from src.jobs.base import BaseJobHandler
    from datetime import datetime
    import pytz
    et = pytz.timezone("America/New_York")
    # Simulate just-before-midnight: all dates should be relative to the same day
    midnight = et.localize(datetime(2026, 3, 27, 23, 59, 59))
    with patch("src.jobs.base.now_et", return_value=midnight):
        upcoming, dates = BaseJobHandler._upcoming_earnings([], days=3)
    assert dates == ["2026-03-27", "2026-03-28", "2026-03-29"]
    assert upcoming == []  # no earnings in empty list


def test_sentiment_cache_uses_separate_db_from_ivcrush():
    """Sentiment cache must use SENTIMENT_CACHE_DB_PATH, not DB_PATH (ivcrush.db)."""
    from src.core.config import Settings
    s = Settings()
    assert s.SENTIMENT_CACHE_DB_PATH != s.DB_PATH, (
        "Sentiment cache must use a separate DB from ivcrush.db — "
        "they run as separate processes (5.0 Cloud Run vs local 4.0)"
    )
    assert "sentiment_cache" in s.SENTIMENT_CACHE_DB_PATH or "4.0" in s.SENTIMENT_CACHE_DB_PATH
