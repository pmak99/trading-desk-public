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
        log("error", "Alert ingest failed", error=type(e).__name__)
        raise HTTPException(500, f"Alert processing failed: {_mask_sensitive(str(e))}")


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
                    # Log error type only - str(e) could contain API keys from HTTP errors
                    log("error", "Telegram analyze failed", ticker=ticker, error=type(e).__name__)
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
        log("error", "Telegram handler failed", error=type(e).__name__)
        return {"ok": True}
