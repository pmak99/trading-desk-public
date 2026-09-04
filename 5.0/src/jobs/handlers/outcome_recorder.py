"""_outcome_recorder — record post-earnings moves with BMO/AMC timing awareness (19:00 ET)."""
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
from .helpers import fetch_earnings_with_db_fallback, _parse_price_history


async def _outcome_recorder(self) -> Dict[str, Any]:
    """
    Outcome recorder (19:00 ET).
    Record post-earnings moves with BMO/AMC timing awareness.

    BMO/AMC Timing Logic:
        - BMO (Before Market Open): Reaction happens ON earnings day
          Record: prev_day_close -> earnings_day_close
        - AMC (After Market Close): Reaction happens NEXT trading day
          Record: earnings_day_close -> next_day_close

    This job processes:
        1. Today's BMO earnings (reaction already happened today)
        2. Yesterday's AMC earnings (reaction happened today)
    """
    from datetime import datetime

    start_time = self._start_timer()
    today = today_et()
    yesterday = (now_et() - timedelta(days=1)).strftime("%Y-%m-%d")
    repo = HistoricalMovesRepository(settings.DB_PATH)

    # Get earnings from DB (has timing info) with API fallback
    earnings = await fetch_earnings_with_db_fallback(self.finnhub, repo, days=5)

    if not earnings:
        log("warn", "Empty earnings calendar", job="outcome_recorder")
        metrics.count("ivcrush.job.api_empty", {"job": "outcome_recorder", "api": "finnhub"})
        return {"status": "warning", "recorded": 0, "note": "Empty calendar from API"}

    # Build list of earnings to record with timing awareness:
    # 1. Today's BMO earnings (reaction happened today)
    # 2. Yesterday's AMC earnings (reaction happened today)
    from src.domain.repositories import is_valid_ticker

    earnings_to_record = []
    for e in earnings:
        if not is_valid_ticker(e["symbol"]):
            continue

        report_date = e["report_date"]
        timing = e.get("timing", "").upper()

        # Today's BMO: record today's move
        if report_date == today and timing == "BMO":
            earnings_to_record.append({
                "symbol": e["symbol"],
                "earnings_date": today,
                "reference_close_day": yesterday,  # prev day close
                "reaction_day": today,  # reaction on earnings day
            })
        # Yesterday's AMC: record today's move (reaction happened today)
        elif report_date == yesterday and timing == "AMC":
            earnings_to_record.append({
                "symbol": e["symbol"],
                "earnings_date": yesterday,
                "reference_close_day": yesterday,  # earnings day close (before announcement)
                "reaction_day": today,  # reaction next morning
            })
        # Unknown timing for today: assume BMO (conservative - record if we can)
        elif report_date == today and timing in ("", "UNKNOWN", None):
            earnings_to_record.append({
                "symbol": e["symbol"],
                "earnings_date": today,
                "reference_close_day": yesterday,
                "reaction_day": today,
            })

    if not earnings_to_record:
        log("info", "No recordable earnings (BMO today or AMC yesterday)", job="outcome_recorder")
        return {"status": "success", "recorded": 0, "note": "No recordable earnings"}

    log("info", "Processing earnings outcomes",
        bmo_today=len([e for e in earnings_to_record if e["earnings_date"] == today]),
        amc_yesterday=len([e for e in earnings_to_record if e["earnings_date"] == yesterday]),
        job="outcome_recorder")

    recorded = 0
    skipped_duplicate = 0
    skipped_amc_pending = 0
    failed_tickers = []
    api_calls = 0

    for e in earnings_to_record[:MAX_TWELVEDATA_TICKERS]:
        ticker = e["symbol"]
        earnings_date = e["earnings_date"]
        reference_day = e["reference_close_day"]
        reaction_day = e["reaction_day"]

        try:
            # Check if we already have this record
            existing = repo.get_moves(ticker)
            if any(m.get("earnings_date") == earnings_date for m in existing):
                skipped_duplicate += 1
                continue

            # Rate limiting
            api_calls += 1
            await self._rate_limit_tick(api_calls)

            # Get historical prices
            history = await self.twelvedata.get_stock_history(ticker, period="5d", interval="1d")

            if not history or "Close" not in history:
                log("debug", "No history data for outcome recording", ticker=ticker)
                continue

            # Parse price history using helper function
            price_data = _parse_price_history(history.get("Close", {}))
            if len(price_data) < 2:
                log("debug", "Insufficient price data", ticker=ticker, data_points=len(price_data))
                continue

            # Find reference close and reaction close
            reference_close = None
            reaction_close = None

            for date_str, price in price_data:
                if date_str == reference_day:
                    reference_close = price
                if date_str == reaction_day:
                    reaction_close = price

            if not reference_close or not reaction_close or reference_close <= 0:
                log("debug", "Missing price data for outcome",
                    ticker=ticker, reference_day=reference_day, reaction_day=reaction_day,
                    reference_close=reference_close, reaction_close=reaction_close)
                continue

            move_pct = ((reaction_close - reference_close) / reference_close) * 100

            move_record = {
                "ticker": ticker,
                "earnings_date": earnings_date,
                "gap_move_pct": round(move_pct, 4),
                "intraday_move_pct": round(move_pct, 4),
                "prev_close": round(reference_close, 2),
                "earnings_close": round(reaction_close, 2),
            }

            repo.save_move(move_record)
            recorded += 1
            log("debug", "Recorded outcome", ticker=ticker, date=earnings_date,
                move=round(move_pct, 2), reference_day=reference_day, reaction_day=reaction_day)

        except Exception as ex:
            failed_tickers.append(ticker)
            log("warn", "Failed to record outcome",
                ticker=ticker, error=str(ex), job="outcome_recorder")

    # Record metrics
    self._record_duration(start_time, "outcome_recorder")
    metrics.gauge("ivcrush.job.outcomes_recorded", recorded, {"job": "outcome_recorder"})

    log("info", "Outcome recording complete",
        recorded=recorded, skipped=skipped_duplicate, errors=len(failed_tickers))

    result = {
        "status": "success",
        "recorded": recorded,
        "skipped_duplicate": skipped_duplicate,
        "errors": len(failed_tickers),
    }
    if failed_tickers:
        result["failed_tickers"] = failed_tickers
        metrics.gauge("ivcrush.job.tickers_failed", len(failed_tickers), {"job": "outcome_recorder"})
    return result
