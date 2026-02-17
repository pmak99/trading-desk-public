"""
Shared FastAPI dependencies for Trading Desk 5.0.

Provides dependency injection functions for service access and
security verification (API key, Telegram webhook secret).
"""

import hmac

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader

from src.core.config import settings
from src.core.logging import log
from src.core.job_manager import JobManager
from src.core.budget import BudgetTracker
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
    FinnhubClient,
)
from typing import Optional
from src.api.state import AppState, get_app_state, set_app_state


def _get_state() -> AppState:
    """Get current app state, with fallback for tests."""
    state = get_app_state()
    if state is None:
        # Lazy initialization for tests that don't use lifespan
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
            finnhub=FinnhubClient(settings.finnhub_api_key) if settings.finnhub_api_key else None,
        )
        set_app_state(state)
    return state


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


def get_finnhub() -> Optional[FinnhubClient]:
    return _get_state().finnhub


def reset_app_state():
    """Reset app state - useful for tests."""
    set_app_state(None)


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
