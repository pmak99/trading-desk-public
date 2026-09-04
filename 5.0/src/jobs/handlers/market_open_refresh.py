"""_market_open_refresh — refresh VRP and alert on movers at market open (09:30 ET)."""
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


async def _market_open_refresh(self) -> Dict[str, Any]:
    """
    Market open refresh (10:00 ET).
    Refresh prices for today's earnings tickers after market opens.
    Sends alert if any high-VRP ticker has significant pre-market movement.
    """
    start_time = self._start_timer()
    today = today_et()

    # Get earnings for today
    earnings = await self._fetch_earnings("market_open_refresh")
    if not earnings:
        return {"status": "warning", "refreshed": 0, "note": "Empty calendar from API"}

    # Filter to today's earnings only
    todays_earnings = self._todays_earnings(earnings)

    # Filter to tracked tickers only (excludes OTC/foreign stocks without VRP data)
    todays_earnings, repo = self._filter_tracked(todays_earnings)

    if not todays_earnings:
        log("info", "No earnings today", job="market_open_refresh")
        return {"status": "success", "refreshed": 0, "note": "No earnings today"}

    # Refresh prices and check for significant moves
    refreshed = 0
    significant_moves = []
    failed_tickers = []
    api_calls = 0

    for e in todays_earnings[:MAX_TWELVEDATA_TICKERS]:
        ticker = e["symbol"]
        try:
            # Rate limiting
            api_calls += 1
            await self._rate_limit_tick(api_calls)

            # Get current price from Tradier (more reliable than Yahoo)
            quote = await self.tradier.get_quote(ticker)
            price = quote.get("last") or quote.get("close") or quote.get("prevclose") if quote else None
            if not price:
                log("debug", "No current price for market refresh", ticker=ticker)
                continue

            refreshed += 1

            # Check historical average to detect significant pre-market moves
            pcts, historical_avg = self._get_historical_pcts(repo, ticker)
            if pcts is not None:
                # If we have a previous close, check pre-market move
                history = await self.twelvedata.get_stock_history(ticker, period="5d", interval="1d")
                if history and "Close" in history:
                    closes = list(history["Close"].values())
                    if len(closes) >= 2 and closes[-2]:
                        prev_close = closes[-2]
                        pre_market_move = abs((price - prev_close) / prev_close * 100)
                        # Alert if pre-market move exceeds threshold of historical avg
                        if pre_market_move > historical_avg * PRE_MARKET_ALERT_THRESHOLD:
                            significant_moves.append({
                                "ticker": ticker,
                                "pre_market_move": round(pre_market_move, 2),
                                "historical_avg": round(historical_avg, 2),
                                "current_price": round(price, 2),
                            })

        except Exception as ex:
            failed_tickers.append(ticker)
            log("warn", "Failed to refresh ticker",
                ticker=ticker, error=str(ex), job="market_open_refresh")

    # Send alert if significant pre-market moves detected
    telegram_error = None
    if significant_moves:
        try:
            msg_lines = [f"⚡ <b>Market Open Alert ({today})</b>\n"]
            for move in significant_moves[:5]:
                msg_lines.append(
                    f"• <b>{move['ticker']}</b>: {move['pre_market_move']}% pre-market "
                    f"(avg: {move['historical_avg']}%)"
                )
            await self.telegram.send_message("\n".join(msg_lines))
        except Exception as tg_err:
            telegram_error = str(tg_err)
            log("error", "Failed to send market open alert", error=telegram_error)

    # Record metrics
    self._record_duration(start_time, "market_open_refresh")
    metrics.gauge("ivcrush.job.tickers_refreshed", refreshed, {"job": "market_open_refresh"})

    return self._build_result(
        failed_tickers=failed_tickers if failed_tickers else None,
        telegram_error=telegram_error,
        job_name="market_open_refresh",
        refreshed=refreshed,
        significant_moves=len(significant_moves),
    )
