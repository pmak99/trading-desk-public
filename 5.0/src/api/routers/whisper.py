"""Whisper endpoint — most anticipated earnings with high VRP."""

import asyncio
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException

from src.core.config import now_et, today_et, settings
from src.core.logging import log
from src.core import metrics
from src.domain.direction import get_direction
from src.formatters.cli import format_digest_cli
from src.api.state import _mask_sensitive
from src.api.dependencies import (
    verify_api_key,
    get_tradier,
    get_historical_repo,
    get_perplexity,
    get_sentiment_cache,
)
from src.api.routers.analysis_common import _scan_tickers_for_whisper, MAX_SCAN_TIME_SECONDS

router = APIRouter(prefix="/api", tags=["analysis"])

@router.get("/whisper")
async def whisper(date: str = None, format: str = "json", fresh: bool = False, _: bool = Depends(verify_api_key)):
    """
    Most anticipated earnings - find high-VRP opportunities.

    Scans upcoming earnings and returns qualified tickers sorted by score.

    Args:
        fresh: If True, bypass VRP and sentiment caches (for Telegram real-time requests)
    """
    log("info", "Whisper request", date=date, fresh=fresh)
    start_time = time.time()

    try:
        # Get earnings from database (populated by calendar-sync job)
        # This avoids rate-limiting issues with Alpha Vantage API
        repo = get_historical_repo()
        tradier = get_tradier()

        # Build target dates
        today = today_et()
        target_dates = [today]
        for i in range(1, 5):
            future = (now_et() + timedelta(days=i)).strftime("%Y-%m-%d")
            target_dates.append(future)

        if date:
            target_dates = [date]

        # Get upcoming earnings from database (use ET date to avoid UTC mismatch)
        upcoming = repo.get_upcoming_earnings(start_date=today, days=5)
        if date:
            upcoming = [e for e in upcoming if e["report_date"] == date]
        else:
            upcoming = [e for e in upcoming if e["report_date"] in target_dates]

        log("debug", "Fetched upcoming earnings from database", count=len(upcoming), dates=target_dates)

        scan_errors = 0
        # Shared list accumulates results as tasks complete
        # On timeout, this list contains all results completed before timeout
        partial_results = []
        try:
            results, scan_errors = await asyncio.wait_for(
                _scan_tickers_for_whisper(upcoming, repo, tradier, fresh=fresh,
                                         partial_results=partial_results),
                timeout=MAX_SCAN_TIME_SECONDS
            )
        except asyncio.TimeoutError:
            log("warn", "Whisper scan timed out, using partial results",
                timeout_seconds=MAX_SCAN_TIME_SECONDS,
                partial_count=len(partial_results))
            metrics.count("ivcrush.whisper.timeout", {"reason": "scan_timeout"})
            # Use whatever results completed before the timeout
            results = partial_results

        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)

        # If fresh=True, fetch real-time sentiment for top 5 tickers
        # This provides up-to-date direction for Telegram requests
        if fresh and results:
            perplexity = get_perplexity()
            cache = get_sentiment_cache()
            top_n = min(5, len(results))

            for i in range(top_n):
                ticker = results[i]["ticker"]
                earnings_date = results[i]["earnings_date"]

                try:
                    sentiment_data = await perplexity.get_sentiment(ticker, earnings_date)
                    if sentiment_data and not sentiment_data.get("error"):
                        # Cache for future requests
                        cache.save_sentiment(ticker, earnings_date, sentiment_data)

                        # Update direction using fresh sentiment
                        direction = get_direction(
                            skew_bias=None,  # No skew in whisper
                            sentiment_score=sentiment_data.get("score"),
                            sentiment_direction=sentiment_data.get("direction"),
                        )
                        results[i]["direction"] = direction
                        results[i]["sentiment_score"] = sentiment_data.get("score", 0)
                        log("debug", "Fresh sentiment fetched",
                            ticker=ticker, direction=direction,
                            score=sentiment_data.get("score"))
                except Exception as e:
                    log("warn", "Failed to fetch fresh sentiment",
                        ticker=ticker, error=type(e).__name__)

        response = {
            "status": "success",
            "target_dates": target_dates,
            "analyzed": len(upcoming),
            "qualified_count": len(results),
            "error_count": scan_errors,
            "tickers": results[:10],  # Top 10
        }

        # Format for CLI if requested
        if format == "cli":
            ticker_data = [
                {
                    "ticker": t["ticker"],
                    "earnings_date": t.get("earnings_date", ""),
                    "vrp_ratio": t["vrp_ratio"],
                    "score": t["score"],
                    "direction": t.get("direction", "NEUTRAL"),
                    "tailwinds": "",
                    "headwinds": "",
                    "strategy": t.get("strategy", f"VRP {t['vrp_tier']}"),
                }
                for t in results[:10]
            ]
            cli_output = format_digest_cli(
                target_dates[0],
                ticker_data,
            )
            # Record metrics
            duration_ms = (time.time() - start_time) * 1000
            metrics.request_success("whisper", duration_ms)
            metrics.tickers_qualified(len(results))
            return {"output": cli_output}

        # Record metrics
        duration_ms = (time.time() - start_time) * 1000
        metrics.request_success("whisper", duration_ms)
        metrics.tickers_qualified(len(results))

        return response

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        metrics.request_error("whisper", duration_ms)
        log("error", "Whisper failed", error=type(e).__name__, details=_mask_sensitive(str(e)))
        raise HTTPException(500, "Whisper failed")

