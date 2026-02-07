"""
Trading Desk 5.0 - Autopilot
FastAPI application entry point.

This module creates the FastAPI app, registers routers and middleware,
and sets up the application lifespan. All endpoint logic lives in
src/api/routers/.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.state import lifespan
from src.api.middleware import rate_limit_middleware, add_security_headers, add_request_id
from src.api.routers import health, analysis, operations, webhooks, jobs

# Re-export for backward compatibility (tests import from src.main)
from src.api.dependencies import (  # noqa: F401
    _get_state,
    get_job_manager,
    get_job_runner,
    get_budget_tracker,
    get_tradier,
    get_alphavantage,
    get_perplexity,
    get_telegram,
    get_yahoo,
    get_twelvedata,
    get_historical_repo,
    get_sentiment_cache,
    get_vrp_cache,
    reset_app_state,
    verify_api_key,
    verify_telegram_secret,
)
from src.api.state import (  # noqa: F401
    AppState,
    _mask_sensitive,
    InMemoryRateLimiter,
)
from src.api.routers.analysis import (  # noqa: F401
    _analyze_single_ticker,
    _scan_tickers_for_whisper,
    MAX_SCAN_TIME_SECONDS,
    MAX_CONCURRENT_ANALYSIS,
)
from src.api.routers.jobs import _safe_record_status  # noqa: F401

# Re-export settings so patches on src.main.settings still work
from src.core.config import settings  # noqa: F401

app = FastAPI(
    title="Trading Desk 5.0",
    description="Autopilot trading system",
    version="5.0.0",
    lifespan=lifespan,
)

# SECURITY: Add CORS middleware with restrictive settings
# Currently no web frontend, so we deny all cross-origin requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],  # No origins allowed - API-only service
    allow_credentials=False,
    allow_methods=["GET", "POST"],  # Only methods we use
    allow_headers=["X-API-Key", "Content-Type", "X-Request-ID"],
    max_age=600,  # Cache preflight for 10 minutes
)

# Register middleware (order matters - last registered runs first)
app.middleware("http")(rate_limit_middleware)
app.middleware("http")(add_security_headers)
app.middleware("http")(add_request_id)

# Register routers
app.include_router(health.router)
app.include_router(analysis.router)
app.include_router(operations.router)
app.include_router(webhooks.router)
app.include_router(jobs.router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
