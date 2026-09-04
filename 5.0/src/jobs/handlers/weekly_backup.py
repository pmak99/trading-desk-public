"""_weekly_backup — backup ivcrush.db to GCS (Sundays)."""
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


async def _weekly_backup(self) -> Dict[str, Any]:
    """
    Weekly backup (Sunday 03:00 ET).
    Backup database to GCS after integrity check.
    """
    start_time = self._start_timer()

    try:
        import shutil
        from pathlib import Path

        db_path = Path(settings.DB_PATH)
        if not db_path.exists():
            log("warn", "No database file to backup", path=str(db_path))
            metrics.count("ivcrush.job.backup_skipped", {"reason": "no_file"})
            return {"status": "success", "backed_up": False, "reason": "No database file"}

        # Run database integrity check before backup
        integrity_ok = False
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute("PRAGMA integrity_check")
            result = cursor.fetchone()
            conn.close()

            integrity_ok = result and result[0] == "ok"
            if not integrity_ok:
                log("error", "Database integrity check failed",
                    result=result[0] if result else "no result", job="weekly_backup")
                metrics.count("ivcrush.job.integrity_failed", {"job": "weekly_backup"})
                return {
                    "status": "error",
                    "error": f"Database integrity check failed: {result[0] if result else 'no result'}",
                }
        except sqlite3.Error as db_err:
            log("error", "Failed to run integrity check",
                error=str(db_err), job="weekly_backup")
            return {"status": "error", "error": f"Integrity check error: {str(db_err)}"}

        # Create timestamped backup filename
        timestamp = now_et().strftime("%Y%m%d_%H%M%S_%f")
        backup_blob_name = f"backups/ivcrush_{timestamp}.db"

        # Use DatabaseSync to upload
        sync = DatabaseSync(
            bucket_name=settings.gcs_bucket,
            blob_name=backup_blob_name,
        )

        # Copy current database to sync location
        shutil.copy(str(db_path), str(sync.local_path))

        # Upload to GCS (raises DatabaseSyncConflictError on conflict)
        try:
            sync.upload()
        except DatabaseSyncConflictError:
            # Conflict on a unique-timestamped blob is unexpected but harmless
            log("warn", "Weekly backup upload conflict (duplicate timestamp)", job="weekly_backup")

        # Record metrics
        self._record_duration(start_time, "weekly_backup")
        log("info", "Weekly backup complete", blob=backup_blob_name)
        metrics.count("ivcrush.job.backup_success")
        return {"status": "success", "backed_up": True, "blob": backup_blob_name}

    except Exception as e:
        log("error", "Weekly backup failed", error=str(e), job="weekly_backup")
        metrics.count("ivcrush.job.backup_failed", {"reason": "exception"})
        return {"status": "error", "error": str(e)}

