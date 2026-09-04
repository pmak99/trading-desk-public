"""
handlers package — JobRunner dispatching to per-handler modules.

Extracted from the 1,498-line handlers.py monolith:
  helpers.py              — fetch_earnings_with_db_fallback, _parse_price_history
  pre_market_prep.py      — _pre_market_prep (05:30 ET)
  sentiment_scan.py       — _sentiment_scan (06:30 ET)
  morning_digest.py       — _morning_digest (07:30 ET)
  market_open_refresh.py  — _market_open_refresh (09:30 ET)
  pre_trade_refresh.py    — _pre_trade_refresh (14:00 ET)
  after_hours_check.py    — _after_hours_check (16:30 ET)
  outcome_recorder.py     — _outcome_recorder (19:00 ET)
  evening_summary.py      — _evening_summary (21:00 ET)
  weekly_backfill.py      — _weekly_backfill (Saturday 04:00 ET)
  weekly_backup.py        — _weekly_backup (Sundays)
  weekly_cleanup.py       — _weekly_cleanup (Sundays)
  calendar_sync.py        — _calendar_sync (daily)
"""

import asyncio
import shutil
import sqlite3
from pathlib import Path
from typing import Dict, Any, List
from datetime import timedelta

from src.core.config import settings, now_et, today_et, MARKET_TZ
from src.core.logging import log
from src.core.database import DatabaseSync, DatabaseSyncConflictError
from src.core import metrics
from src.integrations import (
    FinnhubClient,
    TradierClient,
    PerplexityClient,
    TelegramSender,
    YahooFinanceClient,
    TwelveDataClient,
)
from src.domain import (
    calculate_vrp,
    classify_liquidity_tier,
    calculate_score,
    apply_sentiment_modifier,
    HistoricalMovesRepository,
    SentimentCacheRepository,
    generate_strategies,
)
from src.domain.implied_move import (
    fetch_real_implied_move,
    get_implied_move_with_fallback,
    IMPLIED_MOVE_FALLBACK_MULTIPLIER,
)
from src.domain.direction import get_direction
from src.formatters.telegram import format_digest
from src.jobs.base import (
    BaseJobHandler,
    filter_to_tracked_tickers,
    MAX_PRE_MARKET_TICKERS,
    MAX_PRIME_CANDIDATES,
    MAX_PRIME_CALLS,
    MAX_DIGEST_CANDIDATES,
    MAX_BACKFILL_TICKERS,
    MAX_OUTCOME_TICKERS,
    MAX_TWELVEDATA_TICKERS,
    RATE_LIMIT_DELAY,
    RATE_LIMIT_BATCH_SIZE,
    PRE_MARKET_ALERT_THRESHOLD,
    AFTER_HOURS_ALERT_THRESHOLD,
    TRADIER_CALLS_PER_TICKER,
)

# Module-level helpers — re-exported for backward-compat (tests patch these names)
from .helpers import fetch_earnings_with_db_fallback, _parse_price_history  # noqa: F401

# Handler implementations
from .pre_market_prep import _pre_market_prep as _impl_pre_market_prep
from .sentiment_scan import _sentiment_scan as _impl_sentiment_scan
from .morning_digest import _morning_digest as _impl_morning_digest
from .market_open_refresh import _market_open_refresh as _impl_market_open_refresh
from .pre_trade_refresh import _pre_trade_refresh as _impl_pre_trade_refresh
from .after_hours_check import _after_hours_check as _impl_after_hours_check
from .outcome_recorder import _outcome_recorder as _impl_outcome_recorder
from .evening_summary import _evening_summary as _impl_evening_summary
from .weekly_backfill import _weekly_backfill as _impl_weekly_backfill
from .weekly_backup import _weekly_backup as _impl_weekly_backup
from .weekly_cleanup import _weekly_cleanup as _impl_weekly_cleanup
from .calendar_sync import _calendar_sync as _impl_calendar_sync


class JobRunner(BaseJobHandler):
    """Runs scheduled jobs with proper error handling."""

    def __init__(self, twelvedata_client=None):
        self._tradier = None
        self._finnhub = None
        self._perplexity = None
        self._telegram = None
        self._yahoo = None
        self._twelvedata = twelvedata_client  # Accept shared client from app state; create lazily if None

    @property
    def tradier(self) -> TradierClient:
        if self._tradier is None:
            self._tradier = TradierClient(settings.tradier_api_key)
        return self._tradier

    @property
    def finnhub(self) -> FinnhubClient:
        if self._finnhub is None:
            self._finnhub = FinnhubClient(settings.finnhub_api_key)
        return self._finnhub

    @property
    def perplexity(self) -> PerplexityClient:
        if self._perplexity is None:
            self._perplexity = PerplexityClient(
                api_key=settings.perplexity_api_key,
                db_path=settings.DB_PATH,
            )
        return self._perplexity

    @property
    def telegram(self) -> TelegramSender:
        if self._telegram is None:
            self._telegram = TelegramSender(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            )
        return self._telegram

    @property
    def yahoo(self) -> YahooFinanceClient:
        if self._yahoo is None:
            self._yahoo = YahooFinanceClient()
        return self._yahoo

    @property
    def twelvedata(self) -> TwelveDataClient:
        if self._twelvedata is None:
            self._twelvedata = TwelveDataClient(settings.twelve_data_key)
        return self._twelvedata

    async def run(self, job_name: str) -> Dict[str, Any]:
        """
        Run a job by name.

        Args:
            job_name: Name of the job to run

        Returns:
            Result dict with status and details
        """
        handler_map = {
            "pre-market-prep": self._pre_market_prep,
            "sentiment-scan": self._sentiment_scan,
            "morning-digest": self._morning_digest,
            "market-open-refresh": self._market_open_refresh,
            "pre-trade-refresh": self._pre_trade_refresh,
            "after-hours-check": self._after_hours_check,
            "outcome-recorder": self._outcome_recorder,
            "evening-summary": self._evening_summary,
            "weekly-backfill": self._weekly_backfill,
            "weekly-backup": self._weekly_backup,
            "weekly-cleanup": self._weekly_cleanup,
            "calendar-sync": self._calendar_sync,
        }

        handler = handler_map.get(job_name)
        if not handler:
            log("error", "Unknown job", job=job_name)
            return {"status": "error", "error": f"Unknown job: {job_name}"}

        JOB_TIMEOUT_SECONDS = 300

        try:
            log("info", "Starting job", job=job_name)
            result = await asyncio.wait_for(handler(), timeout=JOB_TIMEOUT_SECONDS)
            log("info", "Job completed", job=job_name, result=result)
            return result
        except asyncio.TimeoutError:
            log("error", "Job timed out", job=job_name, timeout_seconds=JOB_TIMEOUT_SECONDS)
            metrics.count("ivcrush.job.timeout", {"job": job_name})
            return {"status": "error", "error": f"Job timed out after {JOB_TIMEOUT_SECONDS}s"}
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            log("error", "Job failed", job=job_name, error=str(e), traceback=tb)
            return {"status": "error", "error": str(e)}

    # --- Delegation one-liners --- #

    async def _pre_market_prep(self) -> Dict[str, Any]:
        return await _impl_pre_market_prep(self)

    async def _sentiment_scan(self) -> Dict[str, Any]:
        return await _impl_sentiment_scan(self)

    async def _morning_digest(self) -> Dict[str, Any]:
        return await _impl_morning_digest(self)

    async def _market_open_refresh(self) -> Dict[str, Any]:
        return await _impl_market_open_refresh(self)

    async def _pre_trade_refresh(self) -> Dict[str, Any]:
        return await _impl_pre_trade_refresh(self)

    async def _after_hours_check(self) -> Dict[str, Any]:
        return await _impl_after_hours_check(self)

    async def _outcome_recorder(self) -> Dict[str, Any]:
        return await _impl_outcome_recorder(self)

    async def _evening_summary(self) -> Dict[str, Any]:
        return await _impl_evening_summary(self)

    async def _weekly_backfill(self) -> Dict[str, Any]:
        return await _impl_weekly_backfill(self)

    async def _weekly_backup(self) -> Dict[str, Any]:
        return await _impl_weekly_backup(self)

    async def _weekly_cleanup(self) -> Dict[str, Any]:
        return await _impl_weekly_cleanup(self)

    async def _calendar_sync(self) -> Dict[str, Any]:
        return await _impl_calendar_sync(self)
