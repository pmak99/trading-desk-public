"""_pre_market_prep — pre-market VRP calculation job (05:30 ET)."""
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
# fetch_earnings_with_db_fallback lives in helpers.py — activated via sys.path during
# pre-package phase. Do NOT import from src.jobs.handlers (circular once __init__.py exists).
# Instead, import from helpers directly (sys.path points at handlers/ during testing).
from .helpers import fetch_earnings_with_db_fallback

async def _pre_market_prep(self) -> Dict[str, Any]:
    """
    Pre-market prep (05:30 ET).
    Fetch today's earnings and calculate VRP for each.
    Uses DB fallback if Alpha Vantage is unavailable.
    """
    start_time = self._start_timer()
    today = today_et()

    repo_for_fallback = HistoricalMovesRepository(settings.DB_PATH)
    earnings = await fetch_earnings_with_db_fallback(
        self.finnhub, repo_for_fallback, days=4
    )
    if not earnings:
        log("info", "No earnings found for pre-market prep", job="pre_market_prep")
        return self._build_result(tickers_found=0, earnings_dates=[])

    # Filter to upcoming earnings
    upcoming, target_dates = self._upcoming_earnings(earnings, days=4)

    # Filter to tracked tickers only (excludes OTC/foreign stocks without VRP data)
    upcoming, repo = self._filter_tracked(upcoming, repo=repo_for_fallback)

    # Log truncation if limit exceeded
    if len(upcoming) > MAX_PRE_MARKET_TICKERS:
        log("info", "Truncating pre-market candidates",
            total=len(upcoming), processing=MAX_PRE_MARKET_TICKERS)

    # Calculate VRP for each
    results = []
    failed_tickers = []
    api_calls = 0

    for e in upcoming[:MAX_PRE_MARKET_TICKERS]:
        ticker = e["symbol"]
        try:
            # Get historical moves
            historical = repo.get_average_move(ticker)
            if historical is None:
                continue

            # Rate limiting for Tradier API calls
            api_calls += 1
            await self._rate_limit_tick(api_calls)

            # Get current price from Tradier (more reliable than Yahoo after hours)
            quote = await self.tradier.get_quote(ticker)
            price = quote.get("last") or quote.get("close") or quote.get("prevclose") if quote else None
            if not price:
                log("debug", "No price data for ticker", ticker=ticker)
                continue

            results.append({
                "ticker": ticker,
                "earnings_date": e["report_date"],
                "historical_avg": historical,
                "price": price,
            })
        except Exception as ex:
            failed_tickers.append(ticker)
            log("warn", "Failed to process ticker in pre-market prep",
                ticker=ticker, error=str(ex), job="pre_market_prep")

    # Record metrics
    self._record_duration(start_time, "pre_market_prep")
    metrics.gauge("ivcrush.job.tickers_processed", len(results), {"job": "pre_market_prep"})

    return self._build_result(
        failed_tickers=failed_tickers if failed_tickers else None,
        tickers_found=len(results),
        earnings_dates=target_dates,
    )

