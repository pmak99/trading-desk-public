"""_pre_trade_refresh — pre-market VRP refresh and candidate selection (14:00 ET)."""
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


async def _pre_trade_refresh(self) -> Dict[str, Any]:
    """
    Pre-trade refresh (14:30 ET).
    Final refresh before typical 2:30-3:30 PM trade window.
    Re-validates VRP with current IV and sends actionable alert.
    """
    start_time = self._start_timer()
    today = today_et()

    # Get earnings for today (AMC - after market close)
    earnings = await self._fetch_earnings("pre_trade_refresh")
    if not earnings:
        return {"status": "warning", "candidates": 0, "note": "Empty calendar from API"}

    # Filter to today's AMC earnings (tradeable now)
    todays_earnings = self._todays_earnings(earnings)

    # Filter to tracked tickers only (excludes OTC/foreign stocks without VRP data)
    todays_earnings, repo = self._filter_tracked(todays_earnings)

    if not todays_earnings:
        log("info", "No earnings today", job="pre_trade_refresh")
        return {"status": "success", "candidates": 0, "note": "No earnings today"}

    # Re-evaluate VRP for today's tickers with current prices
    cache = SentimentCacheRepository(settings.SENTIMENT_CACHE_DB_PATH)
    candidates = []
    failed_tickers = []
    api_calls = 0

    for e in todays_earnings[:MAX_PRE_MARKET_TICKERS]:
        ticker = e["symbol"]
        try:
            # Evaluate VRP using base class pipeline
            vrp_result = await self._evaluate_vrp(repo, ticker, today, api_calls)
            if vrp_result is None:
                continue

            api_calls = vrp_result["api_calls"]
            vrp_data = vrp_result["vrp_data"]
            im_result = vrp_result["im_result"]
            implied_move_pct = vrp_result["implied_move_pct"]

            # Filter out tickers without weekly options if configured
            if settings.require_weekly_options and not im_result.get("has_weekly_options", True):
                log("debug", "Skipping non-weekly ticker",
                    ticker=ticker, reason=im_result.get("weekly_reason", ""),
                    job="pre_trade_refresh")
                continue

            if vrp_data.get("vrp_ratio", 0) < settings.VRP_DISCOVERY:
                continue

            # Get current price for context (use price from implied move result)
            price = im_result.get("price")

            # Get cached sentiment and use get_direction for consistency
            sentiment = cache.get_sentiment(ticker, today)
            sentiment_score = sentiment.get("score") if sentiment else None
            sentiment_direction = sentiment.get("direction") if sentiment else None
            direction = get_direction(
                skew_bias=None,  # No skew analysis in pre-trade refresh
                sentiment_score=sentiment_score,
                sentiment_direction=sentiment_direction,
            )

            candidates.append({
                "ticker": ticker,
                "vrp_ratio": round(vrp_data["vrp_ratio"], 2),
                "tier": vrp_data["tier"],
                "direction": direction,
                "price": round(price, 2) if price else None,
                "implied_move": round(implied_move_pct, 2),
            })

        except Exception as ex:
            failed_tickers.append(ticker)
            log("warn", "Failed to evaluate ticker",
                ticker=ticker, error=str(ex), job="pre_trade_refresh")

    # Sort by VRP ratio
    candidates.sort(key=lambda x: x["vrp_ratio"], reverse=True)

    # Save qualified tickers for downstream jobs (after-hours check)
    if candidates:
        self._save_daily_candidates(
            settings.DB_PATH, today,
            [c["ticker"] for c in candidates],
            "pre_trade_refresh",
        )

    # Send actionable alert
    telegram_error = None
    if candidates:
        try:
            msg_lines = [f"\U0001f3af <b>Pre-Trade Alert ({today} 2:30 PM)</b>\n"]
            msg_lines.append("Top opportunities for AMC earnings:\n")
            for c in candidates[:5]:
                emoji = "\U0001f7e2" if c["direction"] == "BULLISH" else "\U0001f534" if c["direction"] == "BEARISH" else "⚪"
                msg_lines.append(
                    f"{emoji} <b>{c['ticker']}</b>: VRP {c['vrp_ratio']}x ({c['tier']}) "
                    f"| ±{c['implied_move']}% | ${c['price'] or 'N/A'}"
                )
            msg_lines.append("\n⚠️ Verify liquidity before trading")
            await self.telegram.send_message("\n".join(msg_lines))
        except Exception as tg_err:
            telegram_error = str(tg_err)
            log("error", "Failed to send pre-trade alert", error=telegram_error)

    # Record metrics
    self._record_duration(start_time, "pre_trade_refresh")
    metrics.gauge("ivcrush.job.candidates", len(candidates), {"job": "pre_trade_refresh"})

    return self._build_result(
        failed_tickers=failed_tickers if failed_tickers else None,
        telegram_error=telegram_error,
        job_name="pre_trade_refresh",
        candidates=len(candidates),
        top_tickers=[c["ticker"] for c in candidates[:5]],
    )
