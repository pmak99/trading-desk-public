"""_morning_digest — morning digest of top VRP opportunities via Telegram (07:30 ET)."""
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

async def _morning_digest(self) -> Dict[str, Any]:
    """
    Morning digest / Whisper (07:30 ET).
    Send summary of top VRP opportunities via Telegram.

    Uses REAL implied move from Tradier options chains (ATM straddle pricing)
    to calculate accurate VRP ratios. Falls back to estimate only if options
    data unavailable.

    Note: GOOD liquidity is assumed during screening. Actual liquidity
    must be verified via Tradier before placing any trades.
    """
    start_time = self._start_timer()
    today = today_et()

    # Primary: DB earnings_calendar (populated by calendar-sync, same source as /api/whisper).
    # Avoids Alpha Vantage gaps that drop tickers like INTC from the digest.
    # Fallback: Alpha Vantage + tracked-ticker filter if DB window is empty.
    repo = HistoricalMovesRepository(settings.DB_PATH)
    upcoming = repo.get_upcoming_earnings(today, days=4)
    log("debug", "Fetched earnings from DB",
        job="morning_digest", count=len(upcoming))

    if not upcoming:
        log("warn", "DB earnings empty, falling back to Finnhub", job="morning_digest")
        fh_earnings = await self.finnhub.get_earnings_calendar()
        if fh_earnings:
            upcoming_raw, _ = self._upcoming_earnings(fh_earnings, days=4)
            upcoming, repo = self._filter_tracked(upcoming_raw, repo=repo)
            log("debug", "Fetched earnings from Finnhub fallback",
                job="morning_digest", count=len(upcoming))

    if not upcoming:
        log("warn", "No earnings found in DB or Finnhub", job="morning_digest")
        metrics.count("ivcrush.job.api_empty", {"job": "morning_digest", "api": "all"})
        try:
            await self.telegram.send_message(
                f"\U0001f4cb <b>Trading Desk Digest: {today}</b>\n\n\u26a0\ufe0f Earnings calendar unavailable."
            )
        except Exception as tg_err:
            log("error", "Failed to send Telegram notification", error=str(tg_err))
        return {"status": "warning", "opportunities": 0, "sent": False, "note": "No earnings found"}

    target_dates = sorted({e["report_date"] for e in upcoming})
    log("debug", "Digest target dates",
        job="morning_digest", target_dates=target_dates, count=len(upcoming))

    # Log truncation if limit exceeded
    if len(upcoming) > MAX_DIGEST_CANDIDATES:
        log("info", "Truncating digest candidates",
            total=len(upcoming), processing=MAX_DIGEST_CANDIDATES)

    # Build opportunities list with VRP and sentiment
    cache = SentimentCacheRepository(settings.SENTIMENT_CACHE_DB_PATH)

    opportunities: List[Dict[str, Any]] = []
    failed_tickers = []
    api_calls = 0  # Track API calls for rate limiting
    real_implied_count = 0  # Track how many tickers got real options data

    for e in upcoming[:MAX_DIGEST_CANDIDATES]:
        ticker = e["symbol"]
        earnings_date = e["report_date"]

        try:
            # Evaluate VRP using base class pipeline
            vrp_result = await self._evaluate_vrp(repo, ticker, earnings_date, api_calls)
            if vrp_result is None:
                continue

            api_calls = vrp_result["api_calls"]
            if vrp_result["used_real"]:
                real_implied_count += 1

            vrp_data = vrp_result["vrp_data"]
            im_result = vrp_result["im_result"]
            implied_move_pct = vrp_result["implied_move_pct"]

            # Filter out tickers without weekly options if configured
            if settings.require_weekly_options and not im_result.get("has_weekly_options", True):
                log("debug", "Skipping non-weekly ticker",
                    ticker=ticker, reason=im_result.get("weekly_reason", ""),
                    job="morning_digest")
                continue

            # Apply VRP discovery threshold
            if vrp_data.get("vrp_ratio", 0) < settings.VRP_DISCOVERY:
                continue

            # Calculate score (assume GOOD liquidity for screening - see docstring)
            score_data = calculate_score(
                vrp_ratio=vrp_data["vrp_ratio"],
                vrp_tier=vrp_data["tier"],
                implied_move_pct=implied_move_pct,
                liquidity_tier="GOOD",
            )

            # Get cached sentiment if available and use get_direction for consistency
            # Note: skew analysis not available in job handlers (would require extra API calls)
            sentiment = cache.get_sentiment(ticker, earnings_date)
            sentiment_score = sentiment.get("score") if sentiment else None
            sentiment_direction = sentiment.get("direction") if sentiment else None
            direction = get_direction(
                skew_bias=None,  # No skew analysis in morning digest
                sentiment_score=sentiment_score,
                sentiment_direction=sentiment_direction,
            )
            tailwinds = sentiment.get("tailwinds", "") if sentiment else ""
            headwinds = sentiment.get("headwinds", "") if sentiment else ""
            final_score = score_data["total_score"]
            if sentiment_score is not None:
                final_score = apply_sentiment_modifier(score_data["total_score"], sentiment_score)

            # Generate actual trading strategies
            price = im_result.get("price")
            expiration = im_result.get("expiration", "")
            strategy_name = f"VRP {vrp_data['tier']}"  # Fallback
            credit = 0

            if price and implied_move_pct > 0:
                strategies = generate_strategies(
                    ticker=ticker,
                    price=price,
                    implied_move_pct=implied_move_pct,
                    direction=direction,
                    liquidity_tier="GOOD",  # Assumed for screening
                    expiration=expiration,
                )
                if strategies:
                    top_strategy = strategies[0]
                    strategy_name = top_strategy.description
                    credit = top_strategy.max_profit / 100  # Convert to per-contract

            opportunities.append({
                "ticker": ticker,
                "earnings_date": earnings_date,
                "vrp_ratio": vrp_data["vrp_ratio"],
                "score": final_score,
                "direction": direction,
                "tailwinds": tailwinds,
                "headwinds": headwinds,
                "strategy": strategy_name,
                "credit": credit,
                "real_data": vrp_result["used_real"],  # Track if we used real options data
            })

        except Exception as ex:
            failed_tickers.append(ticker)
            log("warn", "Failed to evaluate ticker for digest",
                ticker=ticker, error=str(ex), job="morning_digest")

    log("info", "Digest analysis complete",
        real_implied_count=real_implied_count, total_candidates=len(upcoming[:MAX_DIGEST_CANDIDATES]))

    # Save qualified tickers for downstream jobs (after-hours check)
    if opportunities:
        self._save_daily_candidates(
            settings.DB_PATH, today,
            [o["ticker"] for o in opportunities],
            "morning_digest",
        )

    # Sort by score descending
    opportunities.sort(key=lambda x: x["score"], reverse=True)

    # Format and send digest (only if there are opportunities)
    log("info", "Sending digest", opportunities=len(opportunities))

    sent = False
    telegram_error = None
    try:
        if opportunities:
            digest_msg = format_digest(
                target_dates[0],
                opportunities[:10],  # Top 10
            )
            sent = await self.telegram.send_message(digest_msg)
        else:
            # Skip sending when no opportunities - don't spam with empty alerts
            log("info", "Skipping digest - no opportunities", job="morning_digest")
    except Exception as tg_err:
        telegram_error = str(tg_err)
        log("error", "Failed to send Telegram digest",
            error=telegram_error, opportunities=len(opportunities), job="morning_digest")

    # Record metrics
    self._record_duration(start_time, "morning_digest")
    metrics.tickers_qualified(len(opportunities))

    return self._build_result(
        failed_tickers=failed_tickers if failed_tickers else None,
        telegram_error=telegram_error,
        job_name="morning_digest",
        opportunities=len(opportunities),
        sent=sent,
    )

