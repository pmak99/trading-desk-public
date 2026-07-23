"""_weekly_cleanup — clear stale caches (Sundays)."""
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
    FinnhubClient, TradierClient, PerplexityClient, TelegramSender,
    YahooFinanceClient, TwelveDataClient,
)
from src.domain import (
    calculate_vrp, classify_liquidity_tier, calculate_score,
    apply_sentiment_modifier, HistoricalMovesRepository, SentimentCacheRepository,
    generate_strategies,
)
from src.domain.implied_move import (
    fetch_real_implied_move, get_implied_move_with_fallback, IMPLIED_MOVE_FALLBACK_MULTIPLIER,
)
from src.domain.direction import get_direction
from src.formatters.telegram import format_digest
from src.jobs.base import (
    BaseJobHandler, filter_to_tracked_tickers,
    MAX_PRE_MARKET_TICKERS, MAX_PRIME_CANDIDATES, MAX_PRIME_CALLS,
    MAX_DIGEST_CANDIDATES, MAX_BACKFILL_TICKERS, MAX_OUTCOME_TICKERS,
    MAX_TWELVEDATA_TICKERS, RATE_LIMIT_DELAY, RATE_LIMIT_BATCH_SIZE,
    PRE_MARKET_ALERT_THRESHOLD, AFTER_HOURS_ALERT_THRESHOLD, TRADIER_CALLS_PER_TICKER,
)


async def _weekly_cleanup(self) -> Dict[str, Any]:
    """
    Weekly cleanup (Sunday 03:30 ET).
    Clean expired cache entries.
    """
    start_time = self._start_timer()

    try:
        cache = SentimentCacheRepository(settings.SENTIMENT_CACHE_DB_PATH)
        cleared = cache.clear_expired()

        # Record metrics
        self._record_duration(start_time, "weekly_cleanup")
        metrics.gauge("ivcrush.job.cache_cleared", cleared, {"job": "weekly_cleanup"})

        log("info", "Weekly cleanup complete", cleared=cleared)
        return {"status": "success", "cleared": cleared}
    except Exception as e:
        log("error", "Weekly cleanup failed", error=str(e), job="weekly_cleanup")
        return {"status": "error", "error": str(e)}
