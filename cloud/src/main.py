"""
IV Crush 5.0 - Autopilot
FastAPI application entry point.
"""

import uuid
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

from src.core.config import now_et, settings
from src.core.logging import log, set_request_id
from src.core.job_manager import JobManager

app = FastAPI(
    title="IV Crush 5.0",
    description="Autopilot trading system",
    version="5.0.0"
)

# Lazy initialization to avoid issues during test collection
_job_manager = None

def get_job_manager() -> JobManager:
    """Get or create the job manager instance."""
    global _job_manager
    if _job_manager is None:
        _job_manager = JobManager(db_path=settings.DB_PATH)
    return _job_manager


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
        "service": "ivcrush",
        "version": "5.0.0",
        "timestamp_et": now_et().isoformat(),
        "status": "healthy"
    }


@app.post("/dispatch")
async def dispatch():
    """
    Dispatcher endpoint called by Cloud Scheduler every 15 min.
    Routes to correct job based on current time.
    """
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

    # TODO: Actually run the job
    # For now, just record success
    manager.record_status(job, "success")

    return {"status": "success", "job": job}


@app.get("/api/health")
async def health(format: str = "json"):
    """System health check."""
    data = {
        "status": "healthy",
        "timestamp_et": now_et().isoformat(),
        "budget": {
            "calls_today": 0,  # TODO: Get from DB
            "remaining": settings.PERPLEXITY_MONTHLY_BUDGET,
        }
    }
    return data


@app.get("/api/analyze")
async def analyze(ticker: str, date: str = None, format: str = "json"):
    """Deep analysis of single ticker."""
    ticker = ticker.upper().strip()
    if not ticker.isalnum():
        raise HTTPException(400, "Invalid ticker")

    log("info", "Analyze request", ticker=ticker)

    # TODO: Implement full analysis
    return {
        "ticker": ticker,
        "status": "not_implemented",
    }


@app.get("/api/whisper")
async def whisper(date: str = None, format: str = "json"):
    """Most anticipated earnings this week."""
    log("info", "Whisper request")

    # TODO: Implement whisper
    return {
        "status": "not_implemented",
    }


@app.post("/telegram")
async def telegram_webhook(request: Request):
    """Telegram bot webhook handler."""
    try:
        body = await request.json()
        log("info", "Telegram update", update_id=body.get("update_id"))
        # TODO: Handle telegram commands
        return {"ok": True}
    except Exception as e:
        log("error", "Telegram handler failed", error=str(e))
        return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
