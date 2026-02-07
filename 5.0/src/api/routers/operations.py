"""
Operations endpoints for Trading Desk 5.0.

Provides sentiment priming, budget status, and alert configuration.
"""

import sqlite3
import time
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from src.core.config import now_et, today_et, settings
from src.core.logging import log
from src.core import metrics
from src.domain import calculate_vrp
from src.domain.implied_move import (
    fetch_real_implied_move,
    get_implied_move_with_fallback,
)
from src.api.state import _mask_sensitive
from src.api.dependencies import (
    verify_api_key,
    get_alphavantage,
    get_tradier,
    get_perplexity,
    get_budget_tracker,
    get_historical_repo,
    get_sentiment_cache,
)

router = APIRouter(tags=["operations"])


@router.post("/prime")
async def prime(date: str = None, _: bool = Depends(verify_api_key)):
    """
    Pre-cache sentiment for upcoming earnings.

    Fetches and caches sentiment data for all high-VRP tickers with earnings
    in the target date range. Run this 7-8 AM before market open to ensure
    predictable API costs and instant /whisper responses.

    Args:
        date: Optional specific date (YYYY-MM-DD). Defaults to next 5 days.
    """
    log("info", "Prime request", date=date)
    start_time = time.time()

    try:
        # Get earnings calendar
        alphavantage = get_alphavantage()
        earnings = await alphavantage.get_earnings_calendar()

        # Filter to target dates
        today = today_et()
        target_dates = [today]
        for i in range(1, 5):
            future = (now_et() + timedelta(days=i)).strftime("%Y-%m-%d")
            target_dates.append(future)

        if date:
            target_dates = [date]

        upcoming = [e for e in earnings if e["report_date"] in target_dates]

        # Get dependencies
        repo = get_historical_repo()
        tradier = get_tradier()
        budget = get_budget_tracker()
        cache = get_sentiment_cache()
        perplexity = get_perplexity()

        primed_count = 0
        skipped_count = 0
        cached_count = 0
        failed_tickers = []

        for e in upcoming[:30]:  # Limit to 30 tickers
            ticker = e["symbol"]
            earnings_date = e["report_date"]

            try:
                # Check if already cached
                cached = cache.get_sentiment(ticker, earnings_date)
                if cached:
                    cached_count += 1
                    continue

                # Check historical data requirement
                moves = repo.get_moves(ticker)
                if len(moves) < 4:
                    skipped_count += 1
                    continue

                # Check VRP threshold (only prime high-VRP tickers)
                historical_pcts = [abs(m["intraday_move_pct"]) for m in moves if m.get("intraday_move_pct")]
                if not historical_pcts:
                    skipped_count += 1
                    continue

                historical_avg = sum(historical_pcts) / len(historical_pcts)

                # Get implied move for VRP check
                im_result = await fetch_real_implied_move(tradier, ticker, earnings_date)
                implied_move_pct, _ = get_implied_move_with_fallback(im_result, historical_avg)

                vrp_data = calculate_vrp(
                    implied_move_pct=implied_move_pct,
                    historical_moves=historical_pcts,
                )

                # Only prime tickers with VRP >= discovery threshold
                if vrp_data.get("vrp_ratio", 0) < settings.VRP_DISCOVERY:
                    skipped_count += 1
                    continue

                # Check budget before calling
                if not budget.can_call("perplexity"):
                    log("warn", "Budget exhausted during prime", primed=primed_count)
                    break

                # Fetch and cache sentiment
                sentiment_data = await perplexity.get_sentiment(ticker, earnings_date)
                if sentiment_data and not sentiment_data.get("error"):
                    cache.save_sentiment(ticker, earnings_date, sentiment_data)
                    primed_count += 1
                    log("info", "Primed sentiment", ticker=ticker, date=earnings_date)
                else:
                    failed_tickers.append(ticker)

            except Exception as ex:
                log("debug", "Prime failed for ticker", ticker=ticker, error=str(ex))
                failed_tickers.append(ticker)
                continue

        duration_ms = (time.time() - start_time) * 1000
        metrics.request_success("prime", duration_ms)

        return {
            "status": "success",
            "target_dates": target_dates,
            "primed": primed_count,
            "already_cached": cached_count,
            "skipped": skipped_count,
            "failed": failed_tickers if failed_tickers else None,
            "budget": {
                "calls_today": budget.get_summary("perplexity")["today_calls"],
                "remaining": budget.get_summary("perplexity")["budget_remaining"],
            },
        }

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        metrics.request_error("prime", duration_ms)
        log("error", "Prime failed", error=_mask_sensitive(str(e)))
        raise HTTPException(500, f"Prime failed: {_mask_sensitive(str(e))}")


@router.get("/api/budget")
async def budget_status(_: bool = Depends(verify_api_key)):
    """
    Detailed API budget status.

    Returns current usage, daily/monthly limits, and historical spending.
    """
    budget = get_budget_tracker()
    summary = budget.get_summary("perplexity")

    # Get recent spending history from database
    try:
        conn = sqlite3.connect(settings.DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date, SUM(cost) as daily_cost, COUNT(*) as calls
            FROM api_budget
            WHERE date >= date('now', '-7 days')
            GROUP BY date
            ORDER BY date DESC
        """)
        recent_spending = [
            {"date": row[0], "cost": row[1], "calls": row[2]}
            for row in cursor.fetchall()
        ]
        conn.close()
    except Exception:
        recent_spending = []

    return {
        "status": "success",
        "timestamp_et": now_et().isoformat(),
        "perplexity": {
            "calls_today": summary["today_calls"],
            "daily_limit": summary["daily_limit"],
            "can_call": summary["can_call"],
            "month_cost": summary["month_cost"],
            "monthly_budget": 5.00,
            "budget_remaining": summary["budget_remaining"],
            "budget_utilization_pct": round((5.00 - summary["budget_remaining"]) / 5.00 * 100, 1),
        },
        "recent_spending": recent_spending,
    }
