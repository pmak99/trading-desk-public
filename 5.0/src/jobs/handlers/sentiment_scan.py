"""_sentiment_scan — pre-cache AI sentiment for high-VRP tickers (06:30 ET)."""
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

async def _sentiment_scan(self) -> Dict[str, Any]:
    """
    Sentiment scan / Prime (06:30 ET).
    Pre-cache AI sentiment for high-VRP tickers.

    Uses REAL implied move from Tradier options chains (ATM straddle pricing)
    to calculate accurate VRP ratios for selecting which tickers to prime.
    Falls back to estimate only if options data unavailable.
    """
    start_time = self._start_timer()

    # Get earnings for today and next few days
    earnings = await self._fetch_earnings("sentiment_scan")
    if not earnings:
        log("info", "No earnings found for sentiment scan", job="sentiment_scan")
        return {"status": "success", "candidates": 0, "primed": 0}

    upcoming, target_dates = self._upcoming_earnings(earnings, days=4)

    # Filter to tracked tickers only (excludes OTC/foreign stocks without VRP data)
    upcoming, repo = self._filter_tracked(upcoming)

    # Log truncation if limit exceeded
    if len(upcoming) > MAX_PRIME_CANDIDATES:
        log("info", "Truncating prime candidates",
            total=len(upcoming), processing=MAX_PRIME_CANDIDATES)

    # Calculate VRP and filter to candidates worth priming
    cache = SentimentCacheRepository(settings.SENTIMENT_CACHE_DB_PATH)

    candidates = []
    failed_tickers = []
    api_calls = 0
    real_implied_count = 0

    for e in upcoming[:MAX_PRIME_CANDIDATES]:
        ticker = e["symbol"]
        earnings_date = e["report_date"]

        try:
            # Skip if already cached
            if cache.get_sentiment(ticker, earnings_date):
                continue

            # Evaluate VRP using base class pipeline
            vrp_result = await self._evaluate_vrp(repo, ticker, earnings_date, api_calls)
            if vrp_result is None:
                continue

            api_calls = vrp_result["api_calls"]
            if vrp_result["used_real"]:
                real_implied_count += 1

            # Only prime tickers with VRP >= discovery threshold
            if vrp_result["vrp_data"].get("vrp_ratio", 0) >= settings.VRP_DISCOVERY:
                candidates.append({
                    "ticker": ticker,
                    "earnings_date": earnings_date,
                    "vrp_ratio": vrp_result["vrp_data"]["vrp_ratio"],
                })
        except Exception as ex:
            failed_tickers.append(ticker)
            log("warn", "Failed to evaluate ticker for prime",
                ticker=ticker, error=str(ex), job="sentiment_scan")

    log("info", "Sentiment scan VRP analysis complete",
        real_implied_count=real_implied_count, total_evaluated=api_calls)

    # Sort by VRP and prime top candidates
    candidates.sort(key=lambda x: x["vrp_ratio"], reverse=True)

    primed = 0
    prime_failed = []

    for i, c in enumerate(candidates[:MAX_PRIME_CALLS]):
        try:
            # Rate limiting between Perplexity API calls
            if i > 0 and i % RATE_LIMIT_BATCH_SIZE == 0:
                await asyncio.sleep(RATE_LIMIT_DELAY)

            sentiment = await self.perplexity.get_sentiment(c["ticker"], c["earnings_date"])
            if sentiment and not sentiment.get("error"):
                cache.save_sentiment(c["ticker"], c["earnings_date"], sentiment, ttl_hours=12)
                primed += 1
                log("info", "Primed sentiment", ticker=c["ticker"], vrp=c["vrp_ratio"])
            else:
                log("debug", "Empty sentiment response", ticker=c["ticker"])
        except Exception as ex:
            prime_failed.append(c["ticker"])
            log("warn", "Failed to prime ticker",
                ticker=c["ticker"], error=str(ex), job="sentiment_scan")

    # Record metrics
    self._record_duration(start_time, "sentiment_scan")
    metrics.gauge("ivcrush.job.candidates", len(candidates), {"job": "sentiment_scan"})
    metrics.gauge("ivcrush.job.primed", primed, {"job": "sentiment_scan"})

    result = {
        "status": "success",
        "candidates": len(candidates),
        "primed": primed,
    }
    if failed_tickers or prime_failed:
        result["failed_tickers"] = failed_tickers + prime_failed

    return result
