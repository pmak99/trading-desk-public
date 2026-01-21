"""
Trading Desk 5.0 - Autopilot
FastAPI application entry point.
"""

import asyncio
import re
import time
import uuid
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
import hmac

from src.core.config import now_et, today_et, settings
from src.core.logging import log, set_request_id
from src.core.job_manager import JobManager
from src.core.budget import BudgetTracker
from src.core import metrics
from src.jobs import JobRunner
from src.domain import (
    calculate_vrp,
    classify_liquidity_tier,
    calculate_score,
    apply_sentiment_modifier,
    generate_strategies,
    calculate_position_size,
    HistoricalMovesRepository,
    SentimentCacheRepository,
    VRPCacheRepository,
    normalize_ticker,
    validate_ticker,
    is_valid_ticker,
    InvalidTickerError,
    TICKER_ALIASES,
)
from src.domain.implied_move import (
    calculate_implied_move_from_chain,
    fetch_real_implied_move,
    get_implied_move_with_fallback,
)
from src.domain.skew import analyze_skew
from src.domain.direction import get_direction, adjust_direction
from src.integrations import (
    TradierClient,
    AlphaVantageClient,
    PerplexityClient,
    TelegramSender,
    YahooFinanceClient,
    TwelveDataClient,
)
from src.formatters.telegram import format_ticker_line, format_digest, format_alert
from src.formatters.cli import format_digest_cli, format_analyze_cli


@dataclass
class AppState:
    """
    Application state container for proper lifecycle management.

    Uses FastAPI's lifespan context manager for:
    - Clean startup initialization
    - Proper shutdown cleanup
    - No global state leakage between tests
    """
    job_manager: Optional[JobManager] = None
    job_runner: Optional[JobRunner] = None
    budget_tracker: Optional[BudgetTracker] = None
    tradier: Optional[TradierClient] = None
    alphavantage: Optional[AlphaVantageClient] = None
    perplexity: Optional[PerplexityClient] = None
    telegram: Optional[TelegramSender] = None
    yahoo: Optional[YahooFinanceClient] = None
    twelvedata: Optional[TwelveDataClient] = None
    historical_repo: Optional[HistoricalMovesRepository] = None
    sentiment_cache: Optional[SentimentCacheRepository] = None
    vrp_cache: Optional[VRPCacheRepository] = None


# Global state reference - set during lifespan, used by getters
# This allows tests to override state without modifying the app
_app_state: Optional[AppState] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager for proper resource management.

    Startup: Initialize all clients and repositories
    Shutdown: Clean up resources (close connections, etc.)
    """
    global _app_state

    log("info", "Starting Trading Desk 5.0")

    # Initialize all components
    state = AppState(
        job_manager=JobManager(db_path=settings.DB_PATH),
        job_runner=JobRunner(),
        budget_tracker=BudgetTracker(db_path=settings.DB_PATH),
        tradier=TradierClient(settings.tradier_api_key),
        alphavantage=AlphaVantageClient(settings.alpha_vantage_key),
        perplexity=PerplexityClient(
            api_key=settings.perplexity_api_key,
            db_path=settings.DB_PATH,
        ),
        telegram=TelegramSender(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        ),
        yahoo=YahooFinanceClient(),
        twelvedata=TwelveDataClient(settings.twelve_data_key),
        historical_repo=HistoricalMovesRepository(settings.DB_PATH),
        sentiment_cache=SentimentCacheRepository(settings.DB_PATH),
        vrp_cache=VRPCacheRepository(settings.DB_PATH),
    )

    # Store in app.state for access via request.app.state
    app.state.services = state
    _app_state = state

    log("info", "All services initialized")

    yield  # Application runs here

    # Cleanup on shutdown
    log("info", "Shutting down Trading Desk 5.0")

    # Close HTTP clients
    if state.twelvedata:
        await state.twelvedata.close()

    # Shutdown metrics thread pool
    metrics.shutdown()

    _app_state = None


app = FastAPI(
    title="Trading Desk 5.0",
    description="Autopilot trading system",
    version="5.0.0",
    lifespan=lifespan,
)


def _get_state() -> AppState:
    """Get current app state, with fallback for tests."""
    global _app_state
    if _app_state is None:
        # Lazy initialization for tests that don't use lifespan
        _app_state = AppState(
            job_manager=JobManager(db_path=settings.DB_PATH),
            job_runner=JobRunner(),
            budget_tracker=BudgetTracker(db_path=settings.DB_PATH),
            tradier=TradierClient(settings.tradier_api_key),
            alphavantage=AlphaVantageClient(settings.alpha_vantage_key),
            perplexity=PerplexityClient(
                api_key=settings.perplexity_api_key,
                db_path=settings.DB_PATH,
            ),
            telegram=TelegramSender(
                bot_token=settings.telegram_bot_token,
                chat_id=settings.telegram_chat_id,
            ),
            yahoo=YahooFinanceClient(),
            twelvedata=TwelveDataClient(settings.twelve_data_key),
            historical_repo=HistoricalMovesRepository(settings.DB_PATH),
            sentiment_cache=SentimentCacheRepository(settings.DB_PATH),
            vrp_cache=VRPCacheRepository(settings.DB_PATH),
        )
    return _app_state


def get_job_manager() -> JobManager:
    return _get_state().job_manager


def get_job_runner() -> JobRunner:
    return _get_state().job_runner


def get_budget_tracker() -> BudgetTracker:
    return _get_state().budget_tracker


def get_tradier() -> TradierClient:
    return _get_state().tradier


def get_alphavantage() -> AlphaVantageClient:
    return _get_state().alphavantage


def get_perplexity() -> PerplexityClient:
    return _get_state().perplexity


def get_telegram() -> TelegramSender:
    return _get_state().telegram


def get_yahoo() -> YahooFinanceClient:
    return _get_state().yahoo


def get_twelvedata() -> TwelveDataClient:
    return _get_state().twelvedata


def get_historical_repo() -> HistoricalMovesRepository:
    return _get_state().historical_repo


def get_sentiment_cache() -> SentimentCacheRepository:
    return _get_state().sentiment_cache


def get_vrp_cache() -> VRPCacheRepository:
    return _get_state().vrp_cache


def reset_app_state():
    """Reset app state - useful for tests."""
    global _app_state
    _app_state = None


# API Key security
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Depends(api_key_header)):
    """
    Verify API key for protected endpoints.

    SECURITY: Always fail closed - never allow access without valid API key.
    This prevents unauthorized access to trading analysis and alerts.
    """
    expected_key = settings.api_key
    if not expected_key:
        # SECURITY: Always fail closed, even in development
        # To test locally, set API_KEY env var
        log("error", "API_KEY not configured - rejecting request")
        raise HTTPException(
            status_code=503,
            detail="Service misconfigured: API_KEY not set"
        )
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    if not hmac.compare_digest(api_key, expected_key):
        raise HTTPException(status_code=403, detail="Invalid API key")
    return True


def verify_telegram_secret(request: Request) -> bool:
    """
    Verify Telegram webhook secret token.

    SECURITY: Always fail closed - never accept unverified webhooks.
    This prevents webhook spoofing attacks where an attacker could send
    fake Telegram messages to trigger actions.
    """
    expected_secret = settings.telegram_webhook_secret
    if not expected_secret:
        # SECURITY: Always fail closed, even in development
        # To test locally, set TELEGRAM_WEBHOOK_SECRET env var
        log("error", "TELEGRAM_WEBHOOK_SECRET not configured - rejecting webhook")
        return False
    received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not received_secret:
        log("warn", "Missing X-Telegram-Bot-Api-Secret-Token header")
        return False
    if not hmac.compare_digest(received_secret, expected_secret):
        log("warn", "Invalid Telegram webhook secret token")
        return False
    return True


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Add request ID to all requests."""
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4())[:8])
    set_request_id(request_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/")
async def root():
    """Health check endpoint (public, no sensitive info)."""
    return {
        "service": "trading-desk",
        "timestamp_et": now_et().isoformat(),
        "status": "healthy"
    }


def _safe_record_status(manager: JobManager, job: str, status: str) -> bool:
    """
    Safely record job status, handling database errors gracefully.

    Returns:
        True if status was recorded successfully, False if database error occurred
    """
    try:
        manager.record_status(job, status)
        return True
    except sqlite3.Error as db_err:
        log("error", "Failed to record job status",
            job=job, status=status, error=str(db_err))
        metrics.count("ivcrush.job_status.failed", {"job": job, "error": "sqlite3"})
        return False


@app.post("/dispatch")
async def dispatch(
    force: str | None = None,
    _: bool = Depends(verify_api_key)
):
    """
    Dispatcher endpoint called by Cloud Scheduler every 15 min.
    Routes to correct job based on current time.

    Args:
        force: Optional job name to force-run (bypasses time-based scheduling)
    """
    start_time = time.time()
    try:
        manager = get_job_manager()

        # Force-run specific job if requested (for testing)
        if force:
            job = force
            log("info", "Force-running job", job=job)
        else:
            job = manager.get_current_job()

        if not job:
            log("info", "No job scheduled for current time")
            duration_ms = (time.time() - start_time) * 1000
            metrics.request_success("dispatch", duration_ms)
            return {"status": "no_job", "message": "No job scheduled"}

        # Check dependencies
        can_run, reason = manager.check_dependencies(job)
        if not can_run:
            log("warn", "Job dependencies not met", job=job, reason=reason)
            duration_ms = (time.time() - start_time) * 1000
            metrics.request_success("dispatch", duration_ms)
            return {"status": "skipped", "job": job, "reason": reason}

        log("info", "Dispatching job", job=job)

        # Run the job with error handling
        runner = get_job_runner()
        try:
            result = await runner.run(job)
        except Exception as e:
            log("error", "Job execution failed", job=job, error=str(e))
            status_recorded = _safe_record_status(manager, job, "failed")
            duration_ms = (time.time() - start_time) * 1000
            metrics.request_error("dispatch", duration_ms, "job_failed")
            response = {"status": "error", "job": job, "error": str(e)}
            if not status_recorded:
                response["status_recording"] = "failed"
            return response

        # Record status based on result
        status = "success" if result.get("status") == "success" else "failed"
        status_recorded = _safe_record_status(manager, job, status)

        duration_ms = (time.time() - start_time) * 1000
        if status == "success":
            metrics.request_success("dispatch", duration_ms)
        else:
            metrics.request_error("dispatch", duration_ms, "job_result_failed")

        response = {"status": status, "job": job, "result": result}
        if not status_recorded:
            response["status_recording"] = "failed"
        return response

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        metrics.request_error("dispatch", duration_ms)
        log("error", "Dispatch endpoint failed", error=str(e))
        return {"status": "error", "error": str(e)}


@app.get("/api/health")
async def health(format: str = "json", _: bool = Depends(verify_api_key)):
    """System health check with budget info."""
    budget = get_budget_tracker()
    summary = budget.get_summary("perplexity")

    data = {
        "status": "healthy",
        "timestamp_et": now_et().isoformat(),
        "budget": {
            "calls_today": summary["today_calls"],
            "daily_limit": summary["daily_limit"],
            "month_cost": summary["month_cost"],
            "budget_remaining": summary["budget_remaining"],
            "can_call": summary["can_call"],
        },
        "jobs": get_job_manager().get_day_summary(),
    }
    return data


@app.get("/api/budget")
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


@app.post("/prime")
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
        log("error", "Prime failed", error=str(e))
        raise HTTPException(500, f"Prime failed: {str(e)}")


@app.get("/api/scan")
async def scan(date: str, format: str = "json", _: bool = Depends(verify_api_key)):
    """
    Scan all earnings for a specific date.

    Returns all tickers with earnings on the given date, sorted by VRP score.
    Includes VRP analysis, liquidity tier, and basic metrics.

    Args:
        date: Target date in YYYY-MM-DD format (required)
        format: Output format - "json" or "cli"
    """
    # Validate date format and actual validity
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
        raise HTTPException(400, "Invalid date format (expected YYYY-MM-DD)")

    # Validate it's a real date (e.g., not 2026-02-30)
    try:
        from datetime import datetime as dt
        dt.strptime(date, '%Y-%m-%d')
    except ValueError:
        raise HTTPException(400, f"Invalid date: {date}")

    log("info", "Scan request", date=date)
    start_time = time.time()

    try:
        # Get earnings from database (populated by calendar-sync job)
        # This avoids rate-limiting issues with Alpha Vantage API
        repo = get_historical_repo()
        target_earnings = repo.get_earnings_by_date(date)
        log("debug", "Fetched earnings from database", date=date, count=len(target_earnings))

        if not target_earnings:
            return {
                "status": "success",
                "date": date,
                "message": "No earnings found for this date",
                "total_found": 0,
                "qualified": [],
                "filtered": [],
                "errors": [],
            }

        # Get dependencies (repo already created above)
        tradier = get_tradier()

        qualified = []
        filtered = []
        errors = []

        for e in target_earnings[:50]:  # Limit to 50 tickers
            ticker = e["symbol"]
            earnings_date = e["report_date"]

            try:
                # Check historical data requirement
                moves = repo.get_moves(ticker)
                historical_count = len(moves)

                if historical_count < 4:
                    filtered.append({
                        "ticker": ticker,
                        "reason": f"Insufficient history ({historical_count} quarters)",
                    })
                    continue

                # Extract historical move percentages
                historical_pcts = [abs(m["intraday_move_pct"]) for m in moves if m.get("intraday_move_pct")]
                if not historical_pcts:
                    filtered.append({
                        "ticker": ticker,
                        "reason": "No valid historical moves",
                    })
                    continue

                historical_avg = sum(historical_pcts) / len(historical_pcts)

                # Fetch real implied move
                im_result = await fetch_real_implied_move(tradier, ticker, earnings_date)

                # Skip if we couldn't get a price
                if im_result.get("error") == "No price available":
                    filtered.append({
                        "ticker": ticker,
                        "reason": "No price available",
                    })
                    continue

                implied_move_pct, used_real_data = get_implied_move_with_fallback(
                    im_result, historical_avg
                )
                price = im_result.get("price")

                # Calculate VRP
                vrp_data = calculate_vrp(
                    implied_move_pct=implied_move_pct,
                    historical_moves=historical_pcts,
                )

                if vrp_data.get("error"):
                    filtered.append({
                        "ticker": ticker,
                        "reason": f"VRP calculation failed: {vrp_data.get('error')}",
                    })
                    continue

                vrp_ratio = vrp_data.get("vrp_ratio", 0)
                vrp_tier = vrp_data.get("tier", "SKIP")

                # Get liquidity tier from options chain
                liquidity_tier = "UNKNOWN"
                if im_result.get("chain"):
                    chain = im_result["chain"]
                    total_oi = sum(opt.get("open_interest") or 0 for opt in chain)
                    avg_spread = 0
                    spread_count = 0
                    for opt in chain:
                        bid = opt.get("bid") or 0
                        ask = opt.get("ask") or 0
                        if bid > 0 and ask > 0:
                            spread_pct = (ask - bid) / ((ask + bid) / 2) * 100
                            avg_spread += spread_pct
                            spread_count += 1

                    if spread_count > 0:
                        avg_spread /= spread_count

                    liquidity_tier = classify_liquidity_tier(
                        oi=total_oi,
                        spread_pct=avg_spread,
                        position_size=settings.DEFAULT_POSITION_SIZE,
                    )

                # Calculate score
                score_data = calculate_score(
                    vrp_ratio=vrp_ratio,
                    vrp_tier=vrp_tier,
                    implied_move_pct=implied_move_pct,
                    liquidity_tier=liquidity_tier if liquidity_tier != "UNKNOWN" else "WARNING",
                )

                # Determine if qualified (VRP >= discovery threshold)
                if vrp_ratio >= settings.VRP_DISCOVERY:
                    qualified.append({
                        "ticker": ticker,
                        "name": e.get("name", ""),
                        "price": price,
                        "vrp_ratio": round(vrp_ratio, 2),
                        "vrp_tier": vrp_tier,
                        "implied_move_pct": round(implied_move_pct, 1),
                        "historical_mean": round(historical_avg, 1),
                        "historical_count": historical_count,
                        "liquidity_tier": liquidity_tier,
                        "score": round(score_data["total_score"], 1),
                        "real_data": used_real_data,
                    })
                else:
                    filtered.append({
                        "ticker": ticker,
                        "reason": f"Low VRP ({vrp_ratio:.2f}x < {settings.VRP_DISCOVERY}x)",
                        "vrp_ratio": round(vrp_ratio, 2),
                    })

            except Exception as ex:
                log("debug", "Scan failed for ticker", ticker=ticker, error=str(ex))
                errors.append({
                    "ticker": ticker,
                    "error": str(ex)[:100],
                })
                continue

        # Sort qualified by score descending
        qualified.sort(key=lambda x: x["score"], reverse=True)

        duration_ms = (time.time() - start_time) * 1000
        metrics.request_success("scan", duration_ms)
        metrics.tickers_qualified(len(qualified))

        result = {
            "status": "success",
            "date": date,
            "total_found": len(target_earnings),
            "qualified_count": len(qualified),
            "filtered_count": len(filtered),
            "error_count": len(errors),
            "qualified": qualified,
            "filtered": filtered[:10],  # Limit filtered output
            "errors": errors[:5] if errors else None,
        }

        # Format for CLI if requested
        if format == "cli":
            lines = [f"📅 Scan Results for {date}", "=" * 40]
            lines.append(f"Found: {len(target_earnings)} | Qualified: {len(qualified)} | Filtered: {len(filtered)}")
            lines.append("")
            if qualified:
                lines.append("🎯 QUALIFIED OPPORTUNITIES:")
                for t in qualified[:10]:
                    tier_emoji = "🟢" if t["liquidity_tier"] in ["EXCELLENT", "GOOD"] else "🟡" if t["liquidity_tier"] == "WARNING" else "🔴"
                    lines.append(f"  {tier_emoji} {t['ticker']}: VRP {t['vrp_ratio']}x ({t['vrp_tier']}) | Score {t['score']} | {t['liquidity_tier']}")
            else:
                lines.append("❌ No qualified opportunities found")
            return {"output": "\n".join(lines)}

        return result

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        metrics.request_error("scan", duration_ms)
        log("error", "Scan failed", date=date, error=str(e))
        raise HTTPException(500, f"Scan failed: {str(e)}")


@app.post("/alerts/ingest")
async def alerts_ingest(
    request: Request,
    authorization: Optional[str] = Header(None),
):
    """
    Webhook endpoint for GCP Monitoring alerts.

    Receives alerts from GCP Monitoring notification channels and forwards
    them to Telegram. Supports both Bearer token and Basic auth.

    GCP Monitoring sends webhooks with:
    - Header: Authorization: Basic <base64(username:password)> or Bearer <token>
    - Body: JSON with incident details
    """
    import base64
    start_time = time.time()

    # Verify auth (GCP Monitoring uses Basic auth for webhook_basicauth)
    expected_key = settings.api_key
    if expected_key:
        if not authorization:
            log("warn", "Alert webhook missing auth header")
            raise HTTPException(status_code=401, detail="Missing authorization")

        # Support both Basic and Bearer auth
        if authorization.startswith("Basic "):
            try:
                decoded = base64.b64decode(authorization[6:]).decode("utf-8")
                # Format: username:password
                _, password = decoded.split(":", 1)
                if not hmac.compare_digest(password, expected_key):
                    log("warn", "Alert webhook invalid password")
                    raise HTTPException(status_code=403, detail="Invalid credentials")
            except Exception as e:
                log("warn", "Alert webhook Basic auth decode failed", error=str(e))
                raise HTTPException(status_code=401, detail="Invalid authorization format")
        elif authorization.startswith("Bearer "):
            token = authorization[7:]
            if not hmac.compare_digest(token, expected_key):
                log("warn", "Alert webhook invalid token")
                raise HTTPException(status_code=403, detail="Invalid token")
        else:
            log("warn", "Alert webhook invalid auth format")
            raise HTTPException(status_code=401, detail="Invalid authorization format")

    try:
        body = await request.json()
        log("info", "Alert webhook received", incident_id=body.get("incident", {}).get("incident_id"))

        # Parse GCP Monitoring alert payload
        incident = body.get("incident", {})
        condition = incident.get("condition", {})
        policy = incident.get("policy_name", "Unknown Policy")
        state = incident.get("state", "unknown")
        started_at = incident.get("started_at", "")
        summary = incident.get("summary", "No summary available")
        url = incident.get("url", "")

        # Format message for Telegram
        if state == "open":
            emoji = "🚨"
            status_text = "ALERT TRIGGERED"
        elif state == "closed":
            emoji = "✅"
            status_text = "ALERT RESOLVED"
        else:
            emoji = "⚠️"
            status_text = state.upper()

        message = f"""{emoji} <b>{status_text}</b>

<b>Policy:</b> {policy}
<b>Summary:</b> {summary}
<b>Started:</b> {started_at}

<a href="{url}">View in GCP Console</a>

#ivcrush #alert #monitoring"""

        # Send to Telegram
        telegram = get_telegram()
        sent = await telegram.send_message(message)

        duration_ms = (time.time() - start_time) * 1000
        if sent:
            log("info", "Alert forwarded to Telegram", state=state, policy=policy)
            metrics.request_success("alerts_ingest", duration_ms)
            return {"status": "forwarded", "telegram_sent": True}
        else:
            log("warn", "Failed to send alert to Telegram", state=state)
            metrics.request_error("alerts_ingest", duration_ms, "telegram_failed")
            return {"status": "failed", "telegram_sent": False}

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        metrics.request_error("alerts_ingest", duration_ms)
        log("error", "Alert ingest failed", error=str(e))
        raise HTTPException(500, f"Alert processing failed: {str(e)}")


@app.get("/api/analyze")
async def analyze(ticker: str, date: str = None, format: str = "json", fresh: bool = False, _: bool = Depends(verify_api_key)):
    """
    Deep analysis of single ticker.

    Returns VRP, liquidity, sentiment, and strategy recommendations.

    Args:
        fresh: If True, skip sentiment cache and fetch fresh data
    """
    # Validate date parameter if provided
    if date and not re.match(r'^\d{4}-\d{2}-\d{2}$', date):
        raise HTTPException(400, "Invalid date format (expected YYYY-MM-DD)")

    # Validate and normalize ticker using centralized validation
    try:
        ticker = normalize_ticker(ticker)
    except InvalidTickerError as e:
        raise HTTPException(400, str(e))

    log("info", "Analyze request", ticker=ticker, date=date)
    start_time = time.time()

    try:
        # Get historical data
        repo = get_historical_repo()
        moves = repo.get_moves(ticker)
        historical_count = len(moves)

        # Get position limits (TRR data) from precomputed table
        position_limits = repo.get_position_limits(ticker)

        if historical_count < 4:
            return {
                "ticker": ticker,
                "status": "insufficient_data",
                "message": f"Need at least 4 historical moves, found {historical_count or 0}. Use stock ticker symbol (e.g., NKE not NIKE).",
            }

        # Determine earnings date - use provided date or look up from calendar
        target_date = date
        if not target_date:
            earnings_info = repo.get_next_earnings(ticker)
            if earnings_info:
                target_date = earnings_info["earnings_date"]
                log("info", "Found earnings date from calendar", ticker=ticker, date=target_date)

                # Freshness validation: if earnings within 7 days, validate against Alpha Vantage
                try:
                    from datetime import datetime
                    db_date = datetime.strptime(target_date, "%Y-%m-%d").date()
                    today = datetime.strptime(today_et(), "%Y-%m-%d").date()
                    days_until = (db_date - today).days

                    if 0 <= days_until <= 7:
                        alphavantage = get_alphavantage()
                        av_earnings = await alphavantage.get_earnings_calendar(symbol=ticker)
                        if av_earnings:
                            av_date = av_earnings[0].get("report_date")
                            if av_date and av_date != target_date:
                                # Check if API is returning next quarter (earnings already reported)
                                av_date_parsed = datetime.strptime(av_date, "%Y-%m-%d").date()
                                date_diff_days = (av_date_parsed - db_date).days
                                db_date_is_past = db_date <= today

                                # Threshold: 45 days distinguishes same-quarter corrections from next quarter
                                NEXT_QUARTER_THRESHOLD_DAYS = 45
                                if date_diff_days >= NEXT_QUARTER_THRESHOLD_DAYS and db_date_is_past:
                                    # Earnings likely already reported - API shows next quarter
                                    log("warn", "Earnings likely ALREADY REPORTED",
                                        ticker=ticker, reported_date=target_date,
                                        next_quarter=av_date, diff_days=date_diff_days)
                                    return {
                                        "ticker": ticker,
                                        "status": "already_reported",
                                        "message": f"Earnings already reported on {target_date}. Next: {av_date}",
                                    }

                                log("warn", "Earnings date changed", ticker=ticker, db_date=target_date, api_date=av_date)
                                target_date = av_date
                except Exception as e:
                    log("debug", "Earnings validation failed, using cached date", ticker=ticker, error=str(e))
            else:
                # No earnings in calendar - query Alpha Vantage directly
                log("info", "No earnings in calendar, querying Alpha Vantage", ticker=ticker)
                try:
                    alphavantage = get_alphavantage()
                    av_earnings = await alphavantage.get_earnings_calendar(symbol=ticker)
                    if av_earnings:
                        av_date = av_earnings[0].get("report_date")
                        if av_date:
                            target_date = av_date
                            log("info", "Found earnings from Alpha Vantage", ticker=ticker, date=av_date)
                            # Store in calendar for future use
                            repo.upsert_earnings_calendar(av_earnings)
                        else:
                            return {
                                "ticker": ticker,
                                "status": "no_earnings",
                                "message": f"No upcoming earnings found for {ticker}",
                            }
                    else:
                        return {
                            "ticker": ticker,
                            "status": "no_earnings",
                            "message": f"No upcoming earnings found for {ticker}",
                        }
                except Exception as e:
                    log("error", "Failed to fetch earnings from Alpha Vantage", ticker=ticker, error=str(e))
                    return {
                        "ticker": ticker,
                        "status": "no_earnings",
                        "message": f"Could not determine earnings date for {ticker}",
                    }

        # Get current price from Tradier (more reliable than Yahoo in cloud)
        tradier = get_tradier()
        quote = await tradier.get_quote(ticker)
        price = quote.get("last") or quote.get("close") or quote.get("prevclose")
        if not price:
            # Fallback to Twelve Data if Tradier fails (more reliable than Yahoo)
            twelvedata = get_twelvedata()
            price = await twelvedata.get_current_price(ticker)
        if not price:
            return {
                "ticker": ticker,
                "status": "error",
                "message": "Could not get current price",
            }

        # Get options chain for implied move
        expirations = await tradier.get_expirations(ticker)
        nearest_exp = None
        for exp in expirations:
            if exp >= target_date:
                nearest_exp = exp
                break

        implied_move_data = None
        liquidity_tier = "REJECT"
        skew_analysis = None
        if nearest_exp:
            chain = await tradier.get_options_chain(ticker, nearest_exp)
            if chain:
                implied_move_data = calculate_implied_move_from_chain(chain, price)

                # Calculate liquidity from chain
                total_oi = sum(opt.get("open_interest") or 0 for opt in chain)
                avg_spread = 0
                spread_count = 0
                for opt in chain:
                    bid = opt.get("bid") or 0
                    ask = opt.get("ask") or 0
                    if bid > 0 and ask > 0:
                        spread_pct = (ask - bid) / ((ask + bid) / 2) * 100
                        avg_spread += spread_pct
                        spread_count += 1

                if spread_count > 0:
                    avg_spread /= spread_count

                liquidity_tier = classify_liquidity_tier(
                    oi=total_oi,
                    spread_pct=avg_spread,
                    position_size=settings.DEFAULT_POSITION_SIZE,
                )

                # Analyze skew for directional bias
                skew_analysis = analyze_skew(ticker, price, chain)

        # Calculate VRP - extract historical move percentages (use intraday, matches 2.0)
        historical_pcts = [abs(m["intraday_move_pct"]) for m in moves if m.get("intraday_move_pct")]
        historical_avg = sum(historical_pcts) / len(historical_pcts) if historical_pcts else 5.0
        implied_move_pct = implied_move_data["implied_move_pct"] if implied_move_data else historical_avg * 1.5
        vrp_data = calculate_vrp(
            implied_move_pct=implied_move_pct,
            historical_moves=historical_pcts,
        )

        # Calculate tail risk from historical data (fallback if not in position_limits table)
        if historical_pcts:
            max_move = max(historical_pcts)
            tail_risk_ratio = max_move / historical_avg if historical_avg > 0 else 0
            if tail_risk_ratio > 2.5:
                tail_risk_level = "HIGH"
            elif tail_risk_ratio >= 1.5:
                tail_risk_level = "NORMAL"
            else:
                tail_risk_level = "LOW"
        else:
            max_move = 0
            tail_risk_ratio = 0
            tail_risk_level = "UNKNOWN"

        # Use precomputed position_limits if available, otherwise calculate on the fly
        if not position_limits and historical_pcts:
            position_limits = {
                "ticker": ticker,
                "tail_risk_ratio": round(tail_risk_ratio, 2),
                "tail_risk_level": tail_risk_level,
                "avg_move": round(historical_avg, 2),
                "max_move": round(max_move, 2),
                "num_quarters": len(historical_pcts),
                # Default limits for HIGH tail risk
                "max_contracts": 50 if tail_risk_level == "HIGH" else 100,
                "max_notional": 25000 if tail_risk_level == "HIGH" else 50000,
            }

        # Calculate score
        score_data = calculate_score(
            vrp_ratio=vrp_data["vrp_ratio"],
            vrp_tier=vrp_data["tier"],
            implied_move_pct=implied_move_pct,
            liquidity_tier=liquidity_tier,
        )

        # Get sentiment if budget allows
        sentiment_data = None
        budget = get_budget_tracker()
        cache = get_sentiment_cache()

        # Check cache first (unless fresh=True)
        if not fresh:
            cached = cache.get_sentiment(ticker, target_date)
            if cached:
                sentiment_data = cached

        if not sentiment_data and budget.can_call("perplexity"):
            perplexity = get_perplexity()
            sentiment_data = await perplexity.get_sentiment(ticker, target_date)
            # Save to cache if successful
            if sentiment_data and not sentiment_data.get("error"):
                cache.save_sentiment(ticker, target_date, sentiment_data)

        # Apply sentiment modifier and determine direction
        # Uses 3-rule system: skew + sentiment → adjusted direction
        skew_bias = skew_analysis.directional_bias.value if skew_analysis else None
        sentiment_score = sentiment_data.get("score", 0) if sentiment_data else None
        sentiment_direction = sentiment_data.get("direction") if sentiment_data else None

        direction = get_direction(
            skew_bias=skew_bias,
            sentiment_score=sentiment_score,
            sentiment_direction=sentiment_direction,
        )

        final_score = score_data["total_score"]
        if sentiment_data and sentiment_score is not None:
            final_score = apply_sentiment_modifier(score_data["total_score"], sentiment_score)

        # Generate strategies
        strategies = generate_strategies(
            ticker=ticker,
            price=price,
            implied_move_pct=implied_move_pct,
            direction=direction,
            liquidity_tier=liquidity_tier,
            expiration=nearest_exp or "",
        )

        # Calculate position size for top strategy
        # Account size from environment or default (configurable)
        account_size = settings.account_size
        position_size = 0
        if strategies and liquidity_tier != "REJECT":
            top_strategy = strategies[0]
            position_size = calculate_position_size(
                account_value=account_size,
                max_risk_per_contract=top_strategy.max_risk,
                win_rate=0.574,  # Historical win rate
                risk_reward=top_strategy.risk_reward,
            )

        result = {
            "ticker": ticker,
            "status": "success",
            "price": price,
            "earnings_date": target_date,
            "expiration": nearest_exp,
            "vrp": {
                "ratio": vrp_data["vrp_ratio"],
                "tier": vrp_data["tier"],
                "implied_move_pct": implied_move_pct,
                "historical_mean": historical_avg,
                "historical_count": historical_count,
            },
            "liquidity_tier": liquidity_tier,
            "score": {
                "base": score_data["total_score"],
                "final": final_score,
                "components": score_data["components"],
            },
            "sentiment": sentiment_data,
            "skew": {
                "bias": skew_analysis.directional_bias.value if skew_analysis else None,
                "slope": round(skew_analysis.slope, 2) if skew_analysis else None,
                "confidence": round(skew_analysis.confidence, 3) if skew_analysis else None,
                "points": skew_analysis.num_points if skew_analysis else 0,
            } if skew_analysis else None,
            "direction": direction,
            "strategies": [
                {
                    "name": s.name,
                    "description": s.description,
                    "max_profit": s.max_profit,
                    "max_risk": s.max_risk,
                    "pop": s.pop,
                    "breakeven": s.breakeven,
                }
                for s in strategies
            ],
            "position_size": position_size,
            "position_limits": position_limits,
            "tail_risk": {
                "ratio": round(tail_risk_ratio, 2),
                "level": tail_risk_level,
                "max_move": round(max_move, 2),
            },
        }

        # Record metrics
        duration_ms = (time.time() - start_time) * 1000
        metrics.request_success("analyze", duration_ms)
        metrics.vrp_analyzed(ticker, vrp_data["vrp_ratio"], vrp_data["tier"])
        metrics.liquidity_checked(liquidity_tier)
        if sentiment_data and sentiment_data.get("score"):
            metrics.sentiment_fetched(ticker, sentiment_data["score"])

        # Format for CLI if requested
        if format == "cli":
            return {"output": format_analyze_cli(result)}

        return result

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        metrics.request_error("analyze", duration_ms)
        log("error", "Analyze failed", ticker=ticker, error=str(e))
        raise HTTPException(500, f"Analysis failed: {str(e)}")


# Scan timeout for whisper endpoint to avoid Cloud Run timeout
MAX_SCAN_TIME_SECONDS = 60

# Concurrency limit for parallel ticker analysis (ported from 6.0)
# Prevents database connection pool exhaustion and API rate limiting
MAX_CONCURRENT_ANALYSIS = 5


async def _analyze_single_ticker(
    ticker: str,
    earnings_date: str,
    name: str,
    repo,
    tradier,
    sentiment_cache,
    vrp_cache,
    semaphore: asyncio.Semaphore,
    prefetched_moves: Optional[List[Dict[str, Any]]] = None
) -> Optional[Dict[str, Any]]:
    """
    Analyze a single ticker for VRP opportunity.

    Uses semaphore for controlled concurrency across parallel calls.
    Uses VRP cache to reduce Tradier API calls (smart TTL based on earnings proximity).
    Accepts pre-fetched moves to reduce N+1 database queries.
    Returns result dict if qualified, None otherwise.
    """
    async with semaphore:
        try:
            # Get historical data (use pre-fetched if available)
            moves = prefetched_moves if prefetched_moves is not None else repo.get_moves(ticker)
            historical_count = len(moves)

            if historical_count < 4:
                return None

            # Extract historical move percentages (use intraday, matches 2.0)
            historical_pcts = [abs(m["intraday_move_pct"]) for m in moves if m.get("intraday_move_pct")]
            if not historical_pcts:
                return None

            historical_avg = sum(historical_pcts) / len(historical_pcts)

            # Check VRP cache first (reduces Tradier API calls by ~89%)
            cached_vrp = vrp_cache.get_vrp(ticker, earnings_date)
            if cached_vrp:
                # Use cached VRP data
                implied_move_pct = cached_vrp["implied_move_pct"]
                vrp_ratio = cached_vrp["vrp_ratio"]
                vrp_tier = cached_vrp["vrp_tier"]
                price = cached_vrp.get("price")
                expiration = cached_vrp.get("expiration", "")
                used_real_data = cached_vrp.get("used_real_data", False)
                log("debug", "VRP cache hit", ticker=ticker, vrp_ratio=vrp_ratio)
                metrics.count("ivcrush.vrp_cache.hit", {"ticker": ticker})
            else:
                # Cache miss - fetch fresh data from Tradier
                metrics.count("ivcrush.vrp_cache.miss", {"ticker": ticker})

                # Fetch real implied move from Tradier options chain
                im_result = await fetch_real_implied_move(
                    tradier, ticker, earnings_date
                )

                # Skip if we couldn't get a price
                if im_result.get("error") == "No price available":
                    return None

                implied_move_pct, used_real_data = get_implied_move_with_fallback(
                    im_result, historical_avg
                )
                price = im_result.get("price")
                expiration = im_result.get("expiration", "")

                # Calculate VRP
                vrp_data = calculate_vrp(
                    implied_move_pct=implied_move_pct,
                    historical_moves=historical_pcts,
                )

                # Skip if VRP calculation failed
                if vrp_data.get("error"):
                    return None

                vrp_ratio = vrp_data["vrp_ratio"]
                vrp_tier = vrp_data["tier"]

                # Cache the VRP data for future requests
                vrp_cache.save_vrp(ticker, earnings_date, {
                    "implied_move_pct": implied_move_pct,
                    "vrp_ratio": vrp_ratio,
                    "vrp_tier": vrp_tier,
                    "historical_mean": historical_avg,
                    "price": price,
                    "expiration": expiration,
                    "used_real_data": used_real_data,
                })
                log("debug", "VRP cached", ticker=ticker, vrp_ratio=vrp_ratio)

            # Skip if below discovery threshold
            if vrp_ratio < settings.VRP_DISCOVERY:
                return None

            # Calculate score (assume GOOD liquidity for screening)
            score_data = calculate_score(
                vrp_ratio=vrp_ratio,
                vrp_tier=vrp_tier,
                implied_move_pct=implied_move_pct,
                liquidity_tier="GOOD",
            )

            # Get cached sentiment if available and use get_direction for consistency
            # Note: skew analysis not available in whisper (would require extra API calls)
            # so we pass skew_bias=None to let sentiment drive direction
            sentiment = sentiment_cache.get_sentiment(ticker, earnings_date)
            sentiment_score = sentiment.get("score") if sentiment else None
            sentiment_direction = sentiment.get("direction") if sentiment else None
            direction = get_direction(
                skew_bias=None,  # No skew analysis in whisper endpoint
                sentiment_score=sentiment_score,
                sentiment_direction=sentiment_direction,
            )

            # Generate trading strategies
            strategy_name = f"VRP {vrp_tier}"  # Fallback
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

            return {
                "ticker": ticker,
                "name": name,
                "earnings_date": earnings_date,
                "price": price,
                "vrp_ratio": vrp_ratio,
                "vrp_tier": vrp_tier,
                "implied_move_pct": round(implied_move_pct, 1),
                "historical_mean": round(historical_avg, 1),
                "score": score_data["total_score"],
                "real_data": used_real_data,
                "direction": direction,
                "strategy": strategy_name,
                "credit": credit,
            }

        except Exception as ex:
            log("debug", "Skipping ticker", ticker=ticker, error=str(ex))
            return None


async def _scan_tickers_for_whisper(
    upcoming: List[Dict],
    repo,
    tradier
) -> List[Dict[str, Any]]:
    """
    Scan tickers for VRP opportunities using parallel execution.

    Uses REAL implied move from Tradier options chains (ATM straddle pricing)
    to calculate accurate VRP ratios. Falls back to estimate only if options
    data unavailable.

    Optimizations:
    - Parallelization (from 6.0) - semaphore-controlled concurrency
    - VRP caching - smart TTL reduces Tradier API calls by ~89%
    - Batch DB queries - single query for all historical moves (30 queries → 1)

    Target: 60s → 15s for 30 tickers.

    Extracted for asyncio.wait_for timeout support.
    """
    sentiment_cache = get_sentiment_cache()
    vrp_cache = get_vrp_cache()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_ANALYSIS)

    # Filter out invalid tickers (e.g., COF-PI preferred stocks, warrants)
    # These don't have options and can't be analyzed for IV crush
    valid_upcoming = [e for e in upcoming if is_valid_ticker(e["symbol"])]
    invalid_count = len(upcoming) - len(valid_upcoming)
    if invalid_count > 0:
        invalid_tickers = [e["symbol"] for e in upcoming if not is_valid_ticker(e["symbol"])]
        log("debug", "Filtered invalid tickers", count=invalid_count, tickers=invalid_tickers[:5])

    # Limit to 100 tickers (increased from 30 to capture more opportunities)
    tickers_to_scan = valid_upcoming[:100]

    # Batch fetch all historical moves in ONE query (30 queries → 1)
    all_tickers = [e["symbol"] for e in tickers_to_scan]
    batch_moves = repo.get_moves_batch(all_tickers, limit=12)
    log("debug", "Batch fetched historical moves", ticker_count=len(all_tickers))

    # Create parallel tasks for all tickers
    tasks = []
    for e in tickers_to_scan:
        ticker = e["symbol"]
        earnings_date = e["report_date"]
        name = e.get("name", "")

        # Get pre-fetched moves for this ticker
        prefetched_moves = batch_moves.get(ticker, [])

        task = asyncio.create_task(
            _analyze_single_ticker(
                ticker=ticker,
                earnings_date=earnings_date,
                name=name,
                repo=repo,
                tradier=tradier,
                sentiment_cache=sentiment_cache,
                vrp_cache=vrp_cache,
                semaphore=semaphore,
                prefetched_moves=prefetched_moves
            )
        )
        tasks.append(task)

    # Execute all tasks in parallel with exception handling
    results_raw = await asyncio.gather(*tasks, return_exceptions=True)

    # Filter out None results and exceptions
    results = []
    for result in results_raw:
        if isinstance(result, Exception):
            log("debug", "Task failed with exception", error=str(result))
            continue
        if result is not None:
            results.append(result)

    return results


@app.get("/api/whisper")
async def whisper(date: str = None, format: str = "json", fresh: bool = False, _: bool = Depends(verify_api_key)):
    """
    Most anticipated earnings - find high-VRP opportunities.

    Scans upcoming earnings and returns qualified tickers sorted by score.
    VRP data is always fetched fresh from Tradier options chains.

    Args:
        fresh: Reserved for consistency (VRP already fetched fresh each call)
    """
    log("info", "Whisper request", date=date)
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

        try:
            results = await asyncio.wait_for(
                _scan_tickers_for_whisper(upcoming, repo, tradier),
                timeout=MAX_SCAN_TIME_SECONDS
            )
        except asyncio.TimeoutError:
            log("warn", "Whisper scan timed out", timeout_seconds=MAX_SCAN_TIME_SECONDS)
            metrics.count("ivcrush.whisper.timeout", {"reason": "scan_timeout"})
            # Return empty results on timeout - better than hanging
            results = []

        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)

        # Get budget info
        budget = get_budget_tracker()
        summary = budget.get_summary("perplexity")

        response = {
            "status": "success",
            "target_dates": target_dates,
            "qualified_count": len(results),
            "tickers": results[:10],  # Top 10
            "budget": {
                "calls_today": summary["today_calls"],
                "remaining": summary["budget_remaining"],
            },
        }

        # Format for CLI if requested
        if format == "cli":
            ticker_data = [
                {
                    "ticker": t["ticker"],
                    "vrp_ratio": t["vrp_ratio"],
                    "score": t["score"],
                    "direction": "NEUTRAL",
                    "tailwinds": "",
                    "headwinds": "",
                    "strategy": f"VRP {t['vrp_tier']}",
                }
                for t in results[:10]
            ]
            cli_output = format_digest_cli(
                target_dates[0],
                ticker_data,
                summary["today_calls"],
                summary["budget_remaining"],
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
        metrics.budget_update(
            remaining_calls=40 - summary["today_calls"],
            remaining_dollars=summary["budget_remaining"]
        )

        return response

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000
        metrics.request_error("whisper", duration_ms)
        log("error", "Whisper failed", error=str(e))
        raise HTTPException(500, f"Whisper failed: {str(e)}")


@app.post("/telegram")
async def telegram_webhook(request: Request):
    """
    Telegram bot webhook handler.

    Supports commands:
    - /health - System health check
    - /whisper - Today's opportunities
    - /analyze TICKER - Analyze specific ticker
    """
    # Verify Telegram secret token
    if not verify_telegram_secret(request):
        log("warn", "Invalid Telegram webhook secret")
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    try:
        body = await request.json()
        log("info", "Telegram update", update_id=body.get("update_id"))

        message = body.get("message", {})
        text = message.get("text", "")
        chat_id = message.get("chat", {}).get("id")

        if not text or not chat_id:
            return {"ok": True}

        telegram = get_telegram()

        # Parse command
        if text.startswith("/health"):
            budget = get_budget_tracker()
            summary = budget.get_summary("perplexity")
            response = (
                f"🏥 <b>System Health</b>\n\n"
                f"Status: ✅ Healthy\n"
                f"Time: {now_et().strftime('%H:%M ET')}\n"
                f"Budget: {summary['today_calls']}/{summary['daily_limit']} calls\n"
                f"Remaining: ${summary['budget_remaining']:.2f}"
            )
            await telegram.send_message(response)

        elif text.startswith("/whisper"):
            # Get whisper data (always fresh)
            result = await whisper(format="json", fresh=True)
            if result.get("status") == "success" and result.get("tickers"):
                ticker_data = [
                    {
                        "ticker": t["ticker"],
                        "vrp_ratio": t["vrp_ratio"],
                        "score": t["score"],
                        "direction": t.get("direction", "NEUTRAL"),
                        "tailwinds": t.get("name", "")[:20],
                        "headwinds": "",
                        "strategy": t.get("strategy", f"VRP {t['vrp_tier']}"),
                        "credit": t.get("credit", 0),
                    }
                    for t in result["tickers"][:5]
                ]
                budget = result.get("budget", {})
                digest = format_digest(
                    result["target_dates"][0],
                    ticker_data,
                    budget.get("calls_today", 0),
                    budget.get("remaining", 5.0),
                )
                await telegram.send_message(digest)
            else:
                await telegram.send_message("No high-VRP opportunities found today.")

        elif text.startswith("/analyze"):
            # Parse ticker from command
            parts = text.split()
            if len(parts) < 2:
                await telegram.send_message("Usage: /analyze TICKER")
            else:
                raw_ticker = parts[1]
                # Validate and normalize using centralized validation
                try:
                    ticker = normalize_ticker(raw_ticker)
                except InvalidTickerError as e:
                    await telegram.send_message(str(e))
                    return {"ok": True}
                try:
                    result = await analyze(ticker=ticker, format="json", fresh=True)
                    if result.get("status") == "success":
                        alert_data = {
                            "ticker": ticker,
                            "vrp_ratio": result["vrp"]["ratio"],
                            "score": result["score"]["final"],
                            "direction": result["direction"],
                            "sentiment_score": result.get("sentiment", {}).get("score", 0),
                            "tailwinds": "",
                            "headwinds": "",
                            "strategy": result["strategies"][0]["name"] if result["strategies"] else "No strategy",
                            "credit": result["strategies"][0]["max_profit"] / 100 if result["strategies"] else 0,
                            "max_risk": result["strategies"][0]["max_risk"] if result["strategies"] else 0,
                            "pop": result["strategies"][0]["pop"] if result["strategies"] else 0,
                        }
                        await telegram.send_message(format_alert(alert_data))
                    else:
                        await telegram.send_message(f"Analysis failed: {result.get('message', 'Unknown error')}")
                except Exception as e:
                    # Log full error but only send sanitized message to Telegram
                    log("error", "Telegram analyze failed", ticker=ticker, error=str(e))
                    await telegram.send_message(f"Error analyzing {ticker}: {type(e).__name__}")

        elif text.startswith("/dashboard"):
            grafana_url = settings.grafana_dashboard_url
            if grafana_url:
                await telegram.send_message(f"📊 <b>Dashboard</b>\n\n<a href=\"{grafana_url}\">Open Grafana Dashboard</a>")
            else:
                await telegram.send_message("Dashboard not configured. Set GRAFANA_DASHBOARD_URL in environment.")

        elif text.startswith("/"):
            await telegram.send_message(
                "Available commands:\n"
                "/health - System status\n"
                "/whisper - Today's opportunities\n"
                "/analyze TICKER - Deep analysis\n"
                "/dashboard - Metrics dashboard"
            )

        return {"ok": True}

    except Exception as e:
        log("error", "Telegram handler failed", error=str(e))
        return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
