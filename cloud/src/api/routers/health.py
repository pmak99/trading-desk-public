"""
Health check endpoints for Trading Desk 5.0.

Provides system health and status information.
"""

from fastapi import APIRouter, Depends

from src.core.config import now_et
from src.api.dependencies import verify_api_key, get_budget_tracker, get_job_manager

router = APIRouter(tags=["health"])


@router.get("/")
async def root():
    """Health check endpoint (public, no sensitive info)."""
    return {
        "service": "trading-desk",
        "timestamp_et": now_et().isoformat(),
        "status": "healthy"
    }


@router.get("/api/health")
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
