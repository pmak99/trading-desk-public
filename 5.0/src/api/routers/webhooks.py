"""
Webhook endpoints for Trading Desk 5.0.

Handles GCP Monitoring alert ingestion and Telegram bot webhook processing.
"""

import hmac
import time
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, Header

from src.core.config import now_et, settings
from src.core.logging import log
from src.core import metrics
from src.domain import normalize_ticker, InvalidTickerError
from src.formatters.telegram import format_digest, format_alert
from src.api.state import _mask_sensitive
from src.api.dependencies import (
    verify_telegram_secret,
    get_telegram,
    get_budget_tracker,
    get_perplexity,
    get_sentiment_cache,
)

router = APIRouter(tags=["webhooks"])


def _strategy_type(description: str) -> str:
    """Extract short strategy type from generate_strategies() description.

    Expects formatted descriptions like "Sell 200P / Buy 195P" from strategies.py.
    Not safe for arbitrary strings (e.g. tickers ending in P/C).
    """
    if not description:
        return ""
    # Iron Condor: has both P and C with slash separators
    if ("P/" in description or "P " in description) and ("C/" in description or "C " in description):
        return "IC"
    if "Put" in description or description.strip().endswith("P"):
        return "Put"
    if "Call" in description or description.strip().endswith("C"):
        return "Call"
    if "Iron Condor" in description:
        return "IC"
    if "Bull Put" in description:
        return "Put"
    if "Bear Call" in description:
        return "Call"
    return description[:10]


@router.post("/alerts/ingest")
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

    SECURITY: Always requires authentication. Fails closed if API_KEY not configured.
    """
    import base64
    start_time = time.time()

    # SECURITY FIX: Always require authentication - fail closed if not configured
    expected_key = settings.api_key
    if not expected_key:
        # Fail closed: refuse to accept webhooks without API key configured
        log("error", "API_KEY not configured - refusing alert webhook (fail closed)")
        raise HTTPException(
            status_code=503,
            detail="Service misconfigured: API_KEY required for alert ingestion"
        )

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
            log("warn", "Alert webhook Basic auth decode failed", error=type(e).__name__)
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
        log("error", "Alert ingest failed", error=type(e).__name__, details=_mask_sensitive(str(e)))
        raise HTTPException(500, "Alert processing failed")


@router.post("/telegram")
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

        # Issue 9: Validate text is actually a string
        if not isinstance(text, str):
            log("warn", "Telegram message text is not a string", type=type(text).__name__)
            return {"ok": True}

        # Issue 7: Truncate excessively long inputs to prevent abuse
        MAX_COMMAND_LENGTH = 500
        if len(text) > MAX_COMMAND_LENGTH:
            log("warn", "Telegram command too long, truncating",
                length=len(text), max=MAX_COMMAND_LENGTH)
            text = text[:MAX_COMMAND_LENGTH]

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
            # Import whisper endpoint from analysis router to call it directly
            from src.api.routers.analysis import whisper
            # Get whisper data (always fresh)
            result = await whisper(format="json", fresh=True)
            if result.get("status") == "success" and result.get("tickers"):
                ticker_data = [
                    {
                        "ticker": t["ticker"],
                        "earnings_date": t.get("earnings_date", ""),
                        "vrp_ratio": t["vrp_ratio"],
                        "score": t["score"],
                        "direction": t.get("direction", "NEUTRAL"),
                        "strategy_type": _strategy_type(t.get("strategy", "")),
                        "credit": t.get("credit", 0),
                        "timing": t.get("timing", ""),
                        "trr_high": t.get("trr_high", False),
                    }
                    for t in result["tickers"][:7]
                ]
                budget = result.get("budget", {})
                digest = format_digest(
                    result["target_dates"][0],
                    ticker_data,
                    budget.get("calls_today", 0),
                    budget.get("remaining", settings.PERPLEXITY_MONTHLY_BUDGET),
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
                raw_ticker = parts[1][:10]  # Truncate to prevent abuse
                # Validate and normalize using centralized validation
                try:
                    ticker = normalize_ticker(raw_ticker)
                except InvalidTickerError:
                    await telegram.send_message("Invalid ticker symbol. Use 1-5 letter stock symbols (e.g., AAPL).")
                    return {"ok": True}
                try:
                    # Import analyze endpoint from analysis router to call it directly
                    from src.api.routers.analysis import analyze
                    result = await analyze(ticker=ticker, format="json", fresh=True)
                    if result.get("status") == "success":
                        vrp = result.get("vrp", {})
                        sentiment = result.get("sentiment", {})
                        strat = result["strategies"][0] if result.get("strategies") else {}
                        tail = result.get("tail_risk", {})
                        alert_data = {
                            "ticker": ticker,
                            "price": result.get("price", 0),
                            "earnings_date": result.get("earnings_date", ""),
                            "timing": result.get("timing") or "",
                            "vrp_ratio": vrp.get("ratio", 0),
                            "vrp_tier": vrp.get("tier", ""),
                            "score": result["score"]["final"],
                            "direction": result["direction"],
                            "sentiment_score": sentiment.get("score", 0),
                            "tailwinds": sentiment.get("tailwinds", ""),
                            "headwinds": sentiment.get("headwinds", ""),
                            "strategy": strat.get("name", "No strategy"),
                            "strategy_desc": strat.get("description", ""),
                            "credit": strat.get("max_profit", 0) / 100 if strat else 0,
                            "max_risk": strat.get("max_risk", 0),
                            "pop": strat.get("pop", 0),
                            "liquidity_tier": result.get("liquidity_tier", ""),
                            "implied_move_pct": vrp.get("implied_move_pct", 0),
                            "hist_mean_pct": round(vrp.get("historical_mean", 0), 1),
                            "hist_count": vrp.get("historical_count", 0),
                            "trr_ratio": tail.get("ratio", 0),
                            "trr_level": tail.get("level", ""),
                            "skew_bias": result.get("skew", {}).get("bias", ""),
                        }
                        await telegram.send_message(format_alert(alert_data))
                    else:
                        await telegram.send_message(f"Analysis failed: {result.get('message', 'Unknown error')}")
                except Exception as e:
                    # Log error type only - str(e) could contain API keys from HTTP errors
                    log("error", "Telegram analyze failed", ticker=ticker, error=type(e).__name__)
                    await telegram.send_message(f"Error analyzing {ticker}: {type(e).__name__}")

        elif text.startswith("/council"):
            parts = text.split()
            if len(parts) < 2:
                await telegram.send_message("Usage: /council TICKER")
            else:
                try:
                    ticker = normalize_ticker(parts[1][:10])
                except InvalidTickerError:
                    await telegram.send_message("Invalid ticker. Use 1-5 letter symbols (e.g., NVDA).")
                    return {"ok": True}
                try:
                    await telegram.send_message(f"\U0001f3db Running council for <b>{ticker}</b>...")
                    from src.domain.council import run_council
                    from src.formatters.telegram import format_council
                    from src.api.dependencies import (
                        get_finnhub, get_tradier, get_historical_repo,
                        get_sentiment_cache,
                    )
                    result = await run_council(
                        ticker=ticker,
                        finnhub=get_finnhub(),
                        perplexity=get_perplexity(),
                        tradier=get_tradier(),
                        repo=get_historical_repo(),
                        cache=get_sentiment_cache(),
                        budget=get_budget_tracker(),
                    )
                    await telegram.send_message(format_council(result))
                except Exception as e:
                    log("error", "Telegram council failed", ticker=ticker, error=type(e).__name__)
                    await telegram.send_message(f"Council failed for {ticker}: {type(e).__name__}")

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
                "/council TICKER - 6-source sentiment consensus\n"
                "/dashboard - Metrics dashboard"
            )

        return {"ok": True}

    except Exception as e:
        log("error", "Telegram handler failed", error=type(e).__name__)
        return {"ok": True}
