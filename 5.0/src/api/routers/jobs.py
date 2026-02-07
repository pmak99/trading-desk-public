"""
Job dispatching endpoints for Trading Desk 5.0.

Handles Cloud Scheduler job routing and execution.
"""

import sqlite3
import time

from fastapi import APIRouter, Depends

from src.core.logging import log
from src.core import metrics
from src.core.job_manager import JobManager
from src.api.state import _mask_sensitive
from src.api.dependencies import (
    verify_api_key,
    get_job_manager,
    get_job_runner,
)

router = APIRouter(tags=["jobs"])


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


@router.post("/dispatch")
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
        log("error", "Dispatch endpoint failed", error=_mask_sensitive(str(e)))
        return {"status": "error", "error": _mask_sensitive(str(e))}
