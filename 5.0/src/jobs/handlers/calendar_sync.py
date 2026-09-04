"""_calendar_sync — sync earnings calendar to DB and GCS (daily)."""
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

async def _calendar_sync(self) -> Dict[str, Any]:
    """
    Calendar sync (Sunday 04:00 ET).
    Sync earnings calendar from Alpha Vantage and upload to GCS.
    """
    start_time = self._start_timer()

    try:
        earnings = await self._fetch_earnings("calendar_sync", horizon="3month")
        if not earnings:
            return {"status": "warning", "synced": 0, "note": "Empty calendar from API"}

        # Actually store the earnings to the database
        repo = HistoricalMovesRepository(settings.DB_PATH)
        upserted = repo.upsert_earnings_calendar(earnings)

        # Upload updated database to GCS for persistence across Cloud Run instances
        gcs_uploaded = False
        if settings.gcs_bucket:
            try:
                sync = DatabaseSync(bucket_name=settings.gcs_bucket)
                db_path = Path(settings.DB_PATH)
                shutil.copy(str(db_path), str(sync.local_path))
                sync.upload()
                gcs_uploaded = True
                log("info", "Calendar sync uploaded to GCS", bucket=settings.gcs_bucket)
            except Exception as gcs_err:
                log("warn", "Failed to upload calendar sync to GCS",
                    error=type(gcs_err).__name__)

        # Record metrics
        self._record_duration(start_time, "calendar_sync")
        metrics.gauge("ivcrush.job.earnings_synced", upserted, {"job": "calendar_sync"})

        log("info", "Calendar sync complete", fetched=len(earnings), upserted=upserted, gcs=gcs_uploaded)
        return {"status": "success", "fetched": len(earnings), "synced": upserted, "gcs_uploaded": gcs_uploaded}
    except Exception as e:
        log("error", "Calendar sync failed", error=str(e), job="calendar_sync")
        return {"status": "error", "error": str(e)}
