"""
Health check endpoints for Trading Desk 5.0.

Provides system health and status information.
"""

from fastapi import APIRouter, Depends

from src.core.config import now_et
from src.api.dependencies import verify_api_key, get_job_manager

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
    """System health check."""
    data = {
        "status": "healthy",
        "timestamp_et": now_et().isoformat(),
        "jobs": get_job_manager().get_day_summary(),
    }
    return data
