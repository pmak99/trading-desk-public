"""
Trading Desk 5.0 - Autopilot
FastAPI application entry point.
"""

import re
import uuid
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
from src.jobs import JobRunner
from src.domain import (
    calculate_vrp,
    classify_liquidity_tier,
    calculate_score,
    apply_sentiment_modifier,
    generate_strategies,
    calculate_position_size,
    calculate_implied_move_from_chain,
    HistoricalMovesRepository,
    SentimentCacheRepository,
)
from src.integrations import (
    TradierClient,
    AlphaVantageClient,
    PerplexityClient,
    TelegramSender,
    YahooFinanceClient,
)
from src.formatters.telegram import format_ticker_line, format_digest, format_alert
from src.formatters.cli import format_digest_cli, format_analyze_cli

app = FastAPI(
    title="Trading Desk 5.0",
    description="Autopilot trading system",
    version="5.0.0"
)

# Lazy initialization to avoid issues during test collection
_job_manager = None
_job_runner = None
_budget_tracker = None
_tradier = None
_alphavantage = None
_perplexity = None
_telegram = None
_yahoo = None
_historical_repo = None
_sentiment_cache = None


def get_job_manager() -> JobManager:
    global _job_manager
    if _job_manager is None:
        _job_manager = JobManager(db_path=settings.DB_PATH)
    return _job_manager


def get_job_runner() -> JobRunner:
    global _job_runner
    if _job_runner is None:
        _job_runner = JobRunner()
    return _job_runner


def get_budget_tracker() -> BudgetTracker:
    global _budget_tracker
    if _budget_tracker is None:
        _budget_tracker = BudgetTracker(db_path=settings.DB_PATH)
    return _budget_tracker


def get_tradier() -> TradierClient:
    global _tradier
    if _tradier is None:
        _tradier = TradierClient(settings.tradier_api_key)
    return _tradier


def get_alphavantage() -> AlphaVantageClient:
    global _alphavantage
    if _alphavantage is None:
        _alphavantage = AlphaVantageClient(settings.alpha_vantage_key)
    return _alphavantage


def get_perplexity() -> PerplexityClient:
    global _perplexity
    if _perplexity is None:
        _perplexity = PerplexityClient(
            api_key=settings.perplexity_api_key,
            db_path=settings.DB_PATH,
        )
    return _perplexity


def get_telegram() -> TelegramSender:
    global _telegram
    if _telegram is None:
        _telegram = TelegramSender(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        )
    return _telegram


def get_yahoo() -> YahooFinanceClient:
    global _yahoo
    if _yahoo is None:
        _yahoo = YahooFinanceClient()
    return _yahoo


def get_historical_repo() -> HistoricalMovesRepository:
    global _historical_repo
    if _historical_repo is None:
        _historical_repo = HistoricalMovesRepository(settings.DB_PATH)
    return _historical_repo


def get_sentiment_cache() -> SentimentCacheRepository:
    global _sentiment_cache
    if _sentiment_cache is None:
        _sentiment_cache = SentimentCacheRepository(settings.DB_PATH)
    return _sentiment_cache


# API Key security
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: str = Depends(api_key_header)):
    """Verify API key for protected endpoints."""
    expected_key = settings.api_key
    if not expected_key:
        # No API key configured - allow access (for development)
        return True
    if not api_key:
        raise HTTPException(status_code=401, detail="Missing API key")
    if not hmac.compare_digest(api_key, expected_key):
        raise HTTPException(status_code=403, detail="Invalid API key")
    return True


def verify_telegram_secret(request: Request) -> bool:
    """Verify Telegram webhook secret token."""
    expected_secret = settings.telegram_webhook_secret
    if not expected_secret:
        # No secret configured - allow access (for development)
        return True
    received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not hmac.compare_digest(received_secret, expected_secret):
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
    """Health check endpoint."""
    return {
        "service": "trading-desk",
        "version": "5.0.0",
        "timestamp_et": now_et().isoformat(),
        "status": "healthy"
    }


@app.post("/dispatch")
async def dispatch(_: bool = Depends(verify_api_key)):
    """
    Dispatcher endpoint called by Cloud Scheduler every 15 min.
    Routes to correct job based on current time.
    """
    try:
        manager = get_job_manager()
        job = manager.get_current_job()

        if not job:
            log("info", "No job scheduled for current time")
            return {"status": "no_job", "message": "No job scheduled"}

        # Check dependencies
        can_run, reason = manager.check_dependencies(job)
        if not can_run:
            log("warn", "Job dependencies not met", job=job, reason=reason)
            return {"status": "skipped", "job": job, "reason": reason}

        log("info", "Dispatching job", job=job)

        # Run the job with error handling
        runner = get_job_runner()
        try:
            result = await runner.run(job)
        except Exception as e:
            log("error", "Job execution failed", job=job, error=str(e))
            manager.record_status(job, "failed")
            return {"status": "error", "job": job, "error": str(e)}

        # Record status based on result
        status = "success" if result.get("status") == "success" else "failed"
        manager.record_status(job, status)

        return {"status": status, "job": job, "result": result}

    except Exception as e:
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


# Common company name → ticker symbol mappings
TICKER_ALIASES = {
    "NIKE": "NKE",
    "GOOGLE": "GOOGL",
    "FACEBOOK": "META",
    "AMAZON": "AMZN",
    "APPLE": "AAPL",
    "MICROSOFT": "MSFT",
    "TESLA": "TSLA",
    "NETFLIX": "NFLX",
    "NVIDIA": "NVDA",
    "COSTCO": "COST",
    "STARBUCKS": "SBUX",
    "WALMART": "WMT",
    "TARGET": "TGT",
    "DISNEY": "DIS",
    "BERKSHIRE": "BRK.B",
    "JPMORGAN": "JPM",
    "ALPHABET": "GOOGL",
}


@app.get("/api/analyze")
async def analyze(ticker: str, date: str = None, format: str = "json", _: bool = Depends(verify_api_key)):
    """
    Deep analysis of single ticker.

    Returns VRP, liquidity, sentiment, and strategy recommendations.
    """
    # Validate and normalize ticker
    ticker = ticker.upper().strip()
    if not ticker.replace(".", "").isalnum() or len(ticker) > 10:
        raise HTTPException(400, "Invalid ticker")

    # Convert common company names to ticker symbols
    ticker = TICKER_ALIASES.get(ticker, ticker)

    log("info", "Analyze request", ticker=ticker, date=date)

    try:
        # Get historical data
        repo = get_historical_repo()
        moves = repo.get_moves(ticker)
        historical_count = len(moves)

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
            else:
                target_date = today_et()
                log("warn", "No earnings date in calendar, using today", ticker=ticker)

        # Get current price from Tradier (more reliable than Yahoo in cloud)
        tradier = get_tradier()
        quote = await tradier.get_quote(ticker)
        price = quote.get("last") or quote.get("close") or quote.get("prevclose")
        if not price:
            # Fallback to Yahoo if Tradier fails
            yahoo = get_yahoo()
            price = await yahoo.get_current_price(ticker)
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
                    position_size=10,
                )

        # Calculate VRP - extract historical move percentages (use intraday, matches 2.0)
        historical_pcts = [abs(m["intraday_move_pct"]) for m in moves if m.get("intraday_move_pct")]
        historical_avg = sum(historical_pcts) / len(historical_pcts) if historical_pcts else 5.0
        implied_move_pct = implied_move_data["implied_move_pct"] if implied_move_data else historical_avg * 1.5
        vrp_data = calculate_vrp(
            implied_move_pct=implied_move_pct,
            historical_moves=historical_pcts,
        )

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

        # Check cache first
        cached = cache.get_sentiment(ticker, target_date)
        if cached:
            sentiment_data = cached
        elif budget.can_call("perplexity"):
            perplexity = get_perplexity()
            sentiment_data = await perplexity.get_sentiment(ticker, target_date)
            # Save to cache if successful
            if sentiment_data and not sentiment_data.get("error"):
                cache.save_sentiment(ticker, target_date, sentiment_data)

        # Apply sentiment modifier
        direction = "NEUTRAL"
        final_score = score_data["total_score"]
        if sentiment_data:
            direction = sentiment_data.get("direction", "neutral").upper()
            sentiment_score = sentiment_data.get("score", 0)
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
        position_size = 0
        if strategies and liquidity_tier != "REJECT":
            top_strategy = strategies[0]
            position_size = calculate_position_size(
                account_value=100000,  # Default account size
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
        }

        # Format for CLI if requested
        if format == "cli":
            return {"output": format_analyze_cli(result)}

        return result

    except Exception as e:
        log("error", "Analyze failed", ticker=ticker, error=str(e))
        raise HTTPException(500, f"Analysis failed: {str(e)}")


@app.get("/api/whisper")
async def whisper(date: str = None, format: str = "json", _: bool = Depends(verify_api_key)):
    """
    Most anticipated earnings - find high-VRP opportunities.

    Scans upcoming earnings and returns qualified tickers sorted by score.
    """
    log("info", "Whisper request", date=date)

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

        # Analyze each ticker
        repo = get_historical_repo()
        tradier = get_tradier()
        results = []

        for e in upcoming[:30]:  # Limit to 30 tickers
            ticker = e["symbol"]
            earnings_date = e["report_date"]

            try:
                # Get historical data
                moves = repo.get_moves(ticker)
                historical_count = len(moves)

                if historical_count < 4:
                    continue

                # Extract historical move percentages (use intraday, matches 2.0)
                historical_pcts = [abs(m["intraday_move_pct"]) for m in moves if m.get("intraday_move_pct")]
                if not historical_pcts:
                    continue

                historical_avg = sum(historical_pcts) / len(historical_pcts)

                # Get current price from Tradier
                quote = await tradier.get_quote(ticker)
                price = quote.get("last") or quote.get("close") or quote.get("prevclose")
                if not price:
                    continue

                # Estimate implied move (1.5x historical as proxy without options data)
                implied_move_pct = historical_avg * 1.5

                # Calculate VRP
                vrp_data = calculate_vrp(
                    implied_move_pct=implied_move_pct,
                    historical_moves=historical_pcts,
                )

                # Skip if VRP calculation failed or below threshold
                if vrp_data.get("error") or vrp_data.get("vrp_ratio", 0) < 3.0:
                    continue

                # Calculate score (assume GOOD liquidity for screening)
                score_data = calculate_score(
                    vrp_ratio=vrp_data["vrp_ratio"],
                    vrp_tier=vrp_data["tier"],
                    implied_move_pct=implied_move_pct,
                    liquidity_tier="GOOD",
                )

                results.append({
                    "ticker": ticker,
                    "name": e.get("name", ""),
                    "earnings_date": earnings_date,
                    "price": price,
                    "vrp_ratio": vrp_data["vrp_ratio"],
                    "vrp_tier": vrp_data["tier"],
                    "implied_move_pct": round(implied_move_pct, 1),
                    "historical_mean": round(historical_avg, 1),
                    "score": score_data["total_score"],
                })

            except Exception as ex:
                log("debug", "Skipping ticker", ticker=ticker, error=str(ex))
                continue

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
            return {"output": cli_output}

        return response

    except Exception as e:
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
            # Get whisper data
            result = await whisper(format="json")
            if result.get("status") == "success" and result.get("tickers"):
                ticker_data = [
                    {
                        "ticker": t["ticker"],
                        "vrp_ratio": t["vrp_ratio"],
                        "score": t["score"],
                        "direction": "NEUTRAL",
                        "tailwinds": t.get("name", "")[:20],
                        "headwinds": "",
                        "strategy": f"VRP {t['vrp_tier']}",
                        "credit": 0,
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
                ticker = parts[1].upper()
                try:
                    result = await analyze(ticker=ticker, format="json")
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
                    await telegram.send_message(f"Error analyzing {ticker}: {str(e)}")

        elif text.startswith("/"):
            await telegram.send_message(
                "Available commands:\n"
                "/health - System status\n"
                "/whisper - Today's opportunities\n"
                "/analyze TICKER - Deep analysis"
            )

        return {"ok": True}

    except Exception as e:
        log("error", "Telegram handler failed", error=str(e))
        return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
