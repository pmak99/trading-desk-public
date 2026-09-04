"""_weekly_backfill — record historical earnings moves for past 7 days (Saturday 04:00 ET)."""
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


async def _weekly_backfill(self) -> Dict[str, Any]:
    """
    Weekly backfill (Saturday 04:00 ET).
    Record actual moves for recent completed earnings with BMO/AMC timing awareness.

    BMO/AMC Timing Logic:
        - BMO (Before Market Open): Reaction happens ON earnings day
          Record: prev_day_close -> earnings_day_close
        - AMC (After Market Close): Reaction happens NEXT trading day
          Record: earnings_day_close -> next_day_close
    """
    from datetime import datetime

    start_time = self._start_timer()
    repo = HistoricalMovesRepository(settings.DB_PATH)
    backfilled = 0
    skipped_duplicate = 0
    failed_tickers = []

    # Get earnings from DB (has timing info) with API fallback
    earnings = await fetch_earnings_with_db_fallback(self.finnhub, repo, days=14)

    # Validate API response
    if not earnings:
        log("warn", "Empty earnings calendar", job="weekly_backfill")
        metrics.count("ivcrush.job.api_empty", {"job": "weekly_backfill", "api": "finnhub"})
        return {"status": "warning", "backfilled": 0, "note": "Empty calendar from API"}

    today = now_et()

    # Filter to valid tickers from past 7 days
    from src.domain.repositories import is_valid_ticker

    past_earnings = []
    for e in earnings:
        try:
            if not is_valid_ticker(e["symbol"]):
                continue
            earnings_date_str = e["report_date"]
            earnings_date = datetime.strptime(earnings_date_str, "%Y-%m-%d")
            earnings_date = MARKET_TZ.localize(earnings_date)
            days_ago = (today - earnings_date).days
            if 1 <= days_ago <= 7:
                past_earnings.append(e)
        except Exception:
            continue

    # Log truncation if limit exceeded
    if len(past_earnings) > MAX_BACKFILL_TICKERS:
        log("info", "Truncating backfill candidates",
            total=len(past_earnings), processing=MAX_BACKFILL_TICKERS)

    log("info", "Found past earnings to backfill", count=len(past_earnings))

    api_calls = 0
    for e in past_earnings[:MAX_TWELVEDATA_TICKERS]:
        ticker = e["symbol"]
        earnings_date = e["report_date"]
        timing = e.get("timing", "").upper()

        try:
            # Check ALL existing moves for this ticker
            existing = repo.get_moves(ticker)
            if any(m.get("earnings_date") == earnings_date for m in existing):
                skipped_duplicate += 1
                continue

            # Rate limiting
            api_calls += 1
            await self._rate_limit_tick(api_calls)

            # Get historical prices around earnings
            history = await self.twelvedata.get_stock_history(ticker, period="1mo", interval="1d")

            if not history or "Close" not in history:
                log("debug", "No history data for backfill", ticker=ticker)
                continue

            price_data = _parse_price_history(history.get("Close", {}))
            if not price_data:
                log("debug", "No valid price data after parsing", ticker=ticker)
                continue

            # Build date->price lookup
            price_by_date = {d: p for d, p in price_data}

            # Determine reference and reaction days based on timing
            # BMO: reaction on earnings day, reference is prev day
            # AMC: reaction on next trading day, reference is earnings day
            earnings_idx = None
            for i, (date_str, _) in enumerate(price_data):
                if date_str == earnings_date:
                    earnings_idx = i
                    break

            if earnings_idx is None:
                # Earnings date not in price data - find closest trading day after
                for i, (date_str, _) in enumerate(price_data):
                    if date_str > earnings_date:
                        earnings_idx = i
                        break

            if earnings_idx is None or earnings_idx < 1:
                log("debug", "Cannot find earnings date in price data", ticker=ticker)
                continue

            # Calculate reference and reaction closes based on timing
            if timing == "AMC":
                # AMC: reference = earnings day close, reaction = next trading day
                if earnings_idx + 1 < len(price_data):
                    reference_close = price_data[earnings_idx][1]  # earnings day
                    reaction_close = price_data[earnings_idx + 1][1]  # next day
                else:
                    log("debug", "No next-day data for AMC earnings", ticker=ticker)
                    continue
            else:
                # BMO or unknown: reference = prev day close, reaction = earnings day
                reference_close = price_data[earnings_idx - 1][1]  # prev day
                reaction_close = price_data[earnings_idx][1]  # earnings day

            if not reference_close or not reaction_close or reference_close <= 0:
                log("debug", "Invalid price data for backfill",
                    ticker=ticker, reference=reference_close, reaction=reaction_close)
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
            backfilled += 1
            log("debug", "Backfilled move", ticker=ticker, date=earnings_date,
                timing=timing or "UNKNOWN", move=round(move_pct, 2))

        except Exception as ex:
            failed_tickers.append(ticker)
            log("warn", "Failed to backfill ticker",
                ticker=ticker, earnings_date=earnings_date, error=str(ex), job="weekly_backfill")

    # Record metrics
    self._record_duration(start_time, "weekly_backfill")
    metrics.gauge("ivcrush.job.backfilled", backfilled, {"job": "weekly_backfill"})
    metrics.gauge("ivcrush.job.errors", len(failed_tickers), {"job": "weekly_backfill"})

    log("info", "Weekly backfill complete",
        backfilled=backfilled, skipped_duplicate=skipped_duplicate, errors=len(failed_tickers))

    result = {
        "status": "success",
        "backfilled": backfilled,
        "skipped_duplicate": skipped_duplicate,
        "errors": len(failed_tickers),
    }
    if failed_tickers:
        result["failed_tickers"] = failed_tickers
        metrics.gauge("ivcrush.job.tickers_failed", len(failed_tickers), {"job": "weekly_backfill"})
    return result

