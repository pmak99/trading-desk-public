"""
Application state management and lifespan for Trading Desk 5.0.

Contains AppState dataclass, InMemoryRateLimiter, and the FastAPI lifespan
context manager for proper resource lifecycle management.
"""

import asyncio
import collections
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional, Dict

from fastapi import FastAPI

from src.core.config import settings
from src.core.logging import log
from src.core.database import DatabaseSync
from src.domain.repositories import cleanup_all_pools
from src.core.job_manager import JobManager
from src.core.budget import BudgetTracker
from src.core import metrics
from src.jobs import JobRunner
from src.domain import (
    HistoricalMovesRepository,
    SentimentCacheRepository,
    VRPCacheRepository,
)
from src.integrations import (
    TradierClient,
    AlphaVantageClient,
    PerplexityClient,
    TelegramSender,
    YahooFinanceClient,
    TwelveDataClient,
)


def _mask_sensitive(text: str) -> str:
    """Mask API keys and tokens in text to prevent leaking in logs.

    Shows only the last 4 characters of any value that looks like an API key.
    """
    import re as _re
    # Mask common API key patterns in URLs and error messages
    # Matches: api_key=XXX, apikey=XXX, token=XXX, key=XXX, Bearer XXX
    text = _re.sub(
        r'((?:api[_-]?key|apikey|token|key|Bearer)\s*[=:]\s*)([A-Za-z0-9_\-]{8,})',
        lambda m: m.group(1) + '***' + m.group(2)[-4:],
        text,
        flags=_re.IGNORECASE,
    )
    return text


class InMemoryRateLimiter:
    """
    Simple in-memory rate limiter using sliding window per IP.

    Tracks request timestamps per IP address and enforces a maximum
    number of requests within a rolling time window.

    Not suitable for multi-instance deployments (each instance has its own state),
    but sufficient for single Cloud Run instance protection against abuse.
    """

    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # IP -> deque of request timestamps
        self._requests: Dict[str, collections.deque] = {}
        self._lock = asyncio.Lock()

    async def is_allowed(self, client_ip: str) -> bool:
        """Check if a request from client_ip is allowed."""
        now = time.monotonic()
        cutoff = now - self.window_seconds

        async with self._lock:
            if client_ip not in self._requests:
                self._requests[client_ip] = collections.deque()

            dq = self._requests[client_ip]

            # Remove expired timestamps
            while dq and dq[0] < cutoff:
                dq.popleft()

            if len(dq) >= self.max_requests:
                return False

            dq.append(now)
            return True

    async def cleanup_stale(self):
        """Remove entries for IPs with no recent requests."""
        now = time.monotonic()
        cutoff = now - self.window_seconds

        async with self._lock:
            stale_ips = [
                ip for ip, dq in self._requests.items()
                if not dq or dq[-1] < cutoff
            ]
            for ip in stale_ips:
                del self._requests[ip]


# Global rate limiter instance (60 requests per minute per IP)
rate_limiter = InMemoryRateLimiter(max_requests=60, window_seconds=60)


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


def get_app_state() -> Optional[AppState]:
    """Get the current global app state."""
    return _app_state


def set_app_state(state: Optional[AppState]):
    """Set the global app state."""
    global _app_state
    _app_state = state


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI lifespan context manager for proper resource management.

    Startup: Initialize all clients and repositories
    Shutdown: Clean up resources (close connections, etc.)
    """
    log("info", "Starting Trading Desk 5.0")

    # SECURITY FIX: Validate required config at startup - fail fast on misconfiguration
    config_errors = settings.validate_required_config()
    if config_errors:
        for error in config_errors:
            log("error", f"Configuration error: {error}")
        # In production, we want to fail fast. In development, we may allow partial operation.
        if settings.is_production:
            raise RuntimeError(f"Cannot start in production with missing config: {config_errors}")
        else:
            log("warn", "Starting with missing config (development mode) - some features may not work")

    # Log warnings for optional but recommended config
    settings.validate_or_warn()

    # Download latest database from GCS at startup (persistent storage)
    # This ensures Cloud Run instances start with the most recent data
    db_path = settings.DB_PATH
    if settings.is_production and settings.gcs_bucket:
        try:
            log("info", "Downloading database from GCS", bucket=settings.gcs_bucket)
            db_sync = DatabaseSync(bucket_name=settings.gcs_bucket)
            gcs_db_path = db_sync.download()

            # Copy GCS database to expected path using atomic write pattern
            # Write to temp file first, then rename to avoid partial writes on failure
            import shutil
            from pathlib import Path
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            temp_db_path = db_path + ".tmp"
            shutil.copy(gcs_db_path, temp_db_path)
            shutil.move(temp_db_path, db_path)
            log("info", "Database downloaded from GCS", path=db_path)
        except Exception as e:
            log("warn", "Failed to download DB from GCS, using bundled DB",
                error=type(e).__name__, message=str(e)[:100])

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
    set_app_state(state)

    # Warn if Telegram webhook secret is not configured
    if settings.telegram_bot_token and not settings.telegram_webhook_secret:
        log("error", "TELEGRAM_WEBHOOK_SECRET not configured - Telegram bot will reject all webhooks")

    log("info", "All services initialized")

    # Start background task for rate limiter cleanup (prevents memory leak from stale IPs)
    async def _rate_limiter_cleanup():
        while True:
            await asyncio.sleep(300)  # Every 5 minutes
            await rate_limiter.cleanup_stale()

    cleanup_task = asyncio.create_task(_rate_limiter_cleanup())

    yield  # Application runs here

    # Cancel the cleanup task on shutdown
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

    # Cleanup on shutdown
    log("info", "Shutting down Trading Desk 5.0")

    # Close HTTP clients
    if state.twelvedata:
        await state.twelvedata.close()

    # Close database connection pools
    cleanup_all_pools()
    log("info", "Database connection pools closed")

    # Shutdown metrics thread pool
    metrics.shutdown()

    set_app_state(None)
