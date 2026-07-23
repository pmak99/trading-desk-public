"""_evening_summary — send evening Telegram digest of today's outcomes (21:00 ET)."""
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


async def _evening_summary(self) -> Dict[str, Any]:
    """
    Evening summary (20:00 ET).
    Send end-of-day summary only if there were earnings today.
    """
    start_time = self._start_timer()
    today = today_et()

    # Check if there were any earnings today worth summarizing
    earnings = await self.finnhub.get_earnings_calendar()
    todays_earnings = [e for e in (earnings or []) if e["report_date"] == today]

    # Filter to tracked tickers only (excludes OTC/foreign stocks without VRP data)
    repo = HistoricalMovesRepository(settings.DB_PATH)
    tracked_tickers = repo.get_tracked_tickers()
    todays_earnings = filter_to_tracked_tickers(todays_earnings, tracked_tickers)

    sent = False
    telegram_error = None

    if not todays_earnings:
        # No earnings today - skip sending empty summary
        log("info", "Skipping evening summary - no earnings today", job="evening_summary")
    else:
        try:
            # Get outcome stats from today's recordings
            recorded_moves = []
            for e in todays_earnings[:10]:
                moves = repo.get_moves(e["symbol"])
                today_move = next((m for m in moves if m.get("earnings_date") == today), None)
                if today_move:
                    recorded_moves.append({
                        "ticker": e["symbol"],
                        "move": today_move.get("intraday_move_pct", 0),
                    })

            if recorded_moves:
                msg_lines = [f"\U0001f4ca <b>Trading Desk Summary: {today}</b>\n"]
                msg_lines.append(f"Tracked {len(todays_earnings)} earnings today:\n")
                for m in sorted(recorded_moves, key=lambda x: abs(x["move"]), reverse=True)[:5]:
                    direction = "\U0001f4c8" if m["move"] > 0 else "\U0001f4c9"
                    move_str = f"+{m['move']:.1f}%" if m["move"] > 0 else f"{m['move']:.1f}%"
                    msg_lines.append(f"{direction} <b>{m['ticker']}</b>: {move_str}")
                sent = await self.telegram.send_message("\n".join(msg_lines))
            else:
                # Had earnings but no recorded outcomes yet - skip
                log("info", "Skipping evening summary - no outcomes recorded yet", job="evening_summary")
        except Exception as tg_err:
            telegram_error = str(tg_err)
            log("error", "Failed to send evening summary",
                error=telegram_error, job="evening_summary")

    # Record metrics
    self._record_duration(start_time, "evening_summary")

    result = {"status": "success", "sent": sent, "earnings_today": len(todays_earnings)}
    if telegram_error:
        result["telegram_error"] = telegram_error
    return result
