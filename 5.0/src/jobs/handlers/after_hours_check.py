"""_after_hours_check — after-hours move monitoring and Telegram alerts (16:30 ET)."""
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
from .helpers import _parse_price_history


async def _after_hours_check(self) -> Dict[str, Any]:
    """
    After hours check (16:30 ET).
    Check for after-market-close earnings announcements.
    Only tracks tickers that qualified in morning digest or pre-trade alert.
    """
    start_time = self._start_timer()
    today = today_et()

    # Only show tickers that qualified in earlier alerts (digest / pre-trade)
    qualified = self._get_daily_candidates(settings.DB_PATH, today)
    if not qualified:
        log("info", "No qualified candidates today", job="after_hours_check")
        return {"status": "success", "checked": 0, "note": "No qualified candidates today"}

    # Get earnings for today
    earnings = await self._fetch_earnings("after_hours_check")
    if not earnings:
        return {"status": "warning", "checked": 0, "note": "Empty calendar from API"}

    # Filter to today's earnings that were in our qualified list
    todays_earnings = [
        e for e in self._todays_earnings(earnings)
        if e["symbol"] in qualified
    ]

    if not todays_earnings:
        log("info", "No qualified earnings today", job="after_hours_check")
        return {"status": "success", "checked": 0, "note": "No qualified earnings today"}

    # Check after-hours prices for qualified earnings
    repo = HistoricalMovesRepository(settings.DB_PATH)
    checked = 0
    reported = []
    failed_tickers = []
    api_calls = 0

    for e in todays_earnings[:MAX_TWELVEDATA_TICKERS]:
        ticker = e["symbol"]
        try:
            # Rate limiting
            api_calls += 1
            await self._rate_limit_tick(api_calls)

            # Get current after-hours quote from Tradier (more reliable than Yahoo)
            quote = await self.tradier.get_quote(ticker)
            price = quote.get("last") or quote.get("close") or quote.get("prevclose") if quote else None
            if not price:
                log("debug", "No after-hours price available", ticker=ticker)
                continue

            checked += 1

            # Get today's close for comparison
            history = await self.twelvedata.get_stock_history(ticker, period="5d", interval="1d")
            if not history or "Close" not in history:
                log("debug", "No history data for after-hours check", ticker=ticker)
                continue

            # Parse price history with date verification
            price_data = _parse_price_history(history["Close"])
            if not price_data:
                log("debug", "No valid price data", ticker=ticker)
                continue

            # Find today's regular session close explicitly
            regular_close = None
            for date_str, close_price in price_data:
                if date_str == today:
                    regular_close = close_price
                    break

            # Fallback to last available if today not found (with warning)
            if regular_close is None:
                if price_data:
                    log("warn", "Today's close not found, using last available",
                        ticker=ticker, last_date=price_data[-1][0], today=today)
                    regular_close = price_data[-1][1]
                else:
                    continue

            if not regular_close:
                continue

            # Calculate after-hours move
            ah_move_pct = ((price - regular_close) / regular_close) * 100

            # Get historical avg for context
            pcts, historical_avg = self._get_historical_pcts(repo, ticker)

            # Only track if move exceeds threshold
            if abs(ah_move_pct) > AFTER_HOURS_ALERT_THRESHOLD:
                reported.append({
                    "ticker": ticker,
                    "ah_move": round(ah_move_pct, 2),
                    "regular_close": round(regular_close, 2),
                    "ah_price": round(price, 2),
                    "historical_avg": round(historical_avg, 2) if historical_avg else None,
                    "beat_expected": abs(ah_move_pct) < historical_avg if historical_avg else None,
                })

        except Exception as ex:
            failed_tickers.append(ticker)
            log("warn", "Failed to check ticker",
                ticker=ticker, error=str(ex), job="after_hours_check")

    # Sort by absolute move size
    reported.sort(key=lambda x: abs(x["ah_move"]), reverse=True)

    # Send after-hours alert
    telegram_error = None
    if reported:
        try:
            msg_lines = [f"\U0001f4ca <b>After-Hours Earnings ({today})</b>\n"]
            for r in reported[:10]:
                direction = "\U0001f4c8" if r["ah_move"] > 0 else "\U0001f4c9"
                move_str = f"+{r['ah_move']}%" if r["ah_move"] > 0 else f"{r['ah_move']}%"
                context = ""
                if r["historical_avg"]:
                    if r["beat_expected"]:
                        context = f" (within ±{r['historical_avg']}% avg)"
                    else:
                        context = f" (exceeded ±{r['historical_avg']}% avg)"
                msg_lines.append(f"{direction} <b>{r['ticker']}</b>: {move_str}{context}")
            await self.telegram.send_message("\n".join(msg_lines))
        except Exception as tg_err:
            telegram_error = str(tg_err)
            log("error", "Failed to send after-hours alert", error=telegram_error)

    # Record metrics
    self._record_duration(start_time, "after_hours_check")
    metrics.gauge("ivcrush.job.tickers_checked", checked, {"job": "after_hours_check"})
    metrics.gauge("ivcrush.job.earnings_reported", len(reported), {"job": "after_hours_check"})

    return self._build_result(
        failed_tickers=failed_tickers if failed_tickers else None,
        telegram_error=telegram_error,
        job_name="after_hours_check",
        checked=checked,
        reported=len(reported),
        moves=[{k: v for k, v in r.items() if k in ["ticker", "ah_move"]} for r in reported[:5]],
    )
