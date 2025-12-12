"""
Job dispatcher and dependency manager.

Single dispatcher pattern: Cloud Scheduler calls /dispatch every 15 min,
and this module routes to the correct job based on current time.
"""

from typing import Optional, List, Dict

from .config import now_et, today_et
from .logging import log

# Weekday schedule (Mon-Fri) - all times ET
WEEKDAY_SCHEDULE = {
    "05:30": "pre-market-prep",
    "06:30": "sentiment-scan",
    "07:30": "morning-digest",
    "10:00": "market-open-refresh",
    "14:30": "pre-trade-refresh",
    "16:30": "after-hours-check",
    "19:00": "outcome-recorder",
    "20:00": "evening-summary",
}

# Saturday schedule
SATURDAY_SCHEDULE = {
    "04:00": "weekly-backfill",
}

# Sunday schedule
SUNDAY_SCHEDULE = {
    "03:00": "weekly-backup",
    "03:30": "weekly-cleanup",
    "04:00": "calendar-sync",
}

# Job dependencies (job -> list of jobs that must succeed first)
JOB_DEPENDENCIES: Dict[str, List[str]] = {
    "sentiment-scan": ["pre-market-prep"],
    "morning-digest": ["pre-market-prep"],
    "market-open-refresh": ["pre-market-prep"],
    "pre-trade-refresh": ["pre-market-prep"],
    "after-hours-check": ["pre-market-prep"],
    "outcome-recorder": ["pre-market-prep"],
    "evening-summary": ["outcome-recorder"],
}


def get_scheduled_job(
    time_str: str,
    is_weekend: bool,
    day_of_week: int = 0,
) -> Optional[str]:
    """
    Get job scheduled for given time.

    Args:
        time_str: Time in HH:MM format
        is_weekend: True if Saturday or Sunday
        day_of_week: 0=Mon, 5=Sat, 6=Sun

    Returns:
        Job name or None if no job scheduled
    """
    if is_weekend:
        if day_of_week == 5:  # Saturday
            return SATURDAY_SCHEDULE.get(time_str)
        elif day_of_week == 6:  # Sunday
            return SUNDAY_SCHEDULE.get(time_str)
        return None
    else:
        return WEEKDAY_SCHEDULE.get(time_str)


class JobManager:
    """Manages job dispatch and dependency checking."""

    def __init__(self):
        self._job_status: Dict[str, Dict[str, str]] = {}  # {date: {job: status}}

    def get_dependencies(self, job_name: str) -> List[str]:
        """Get list of jobs that must succeed before this job."""
        return JOB_DEPENDENCIES.get(job_name, [])

    def check_dependencies(self, job_name: str) -> tuple[bool, str]:
        """
        Check if all dependencies succeeded today.

        Returns:
            (can_run, reason) - True if can run, else reason why not
        """
        deps = self.get_dependencies(job_name)
        if not deps:
            return True, ""

        today = today_et()
        day_status = self._job_status.get(today, {})

        for dep in deps:
            status = day_status.get(dep, "not_run")
            if status != "success":
                return False, f"Dependency '{dep}' status: {status}"

        return True, ""

    def record_status(self, job_name: str, status: str):
        """Record job completion status."""
        today = today_et()
        if today not in self._job_status:
            self._job_status[today] = {}
        self._job_status[today][job_name] = status
        log("info", "Job status recorded", job=job_name, status=status)

    def get_current_job(self) -> Optional[str]:
        """
        Get job to run based on current time.

        Uses ±7.5 minute window around scheduled time.
        """
        now = now_et()
        current_time = now.strftime("%H:%M")
        is_weekend = now.weekday() >= 5
        day_of_week = now.weekday()

        # Check exact match first
        job = get_scheduled_job(current_time, is_weekend, day_of_week)
        if job:
            return job

        # Check within ±7 minute window for 15-min dispatcher
        minute = now.minute
        for offset in [-7, -6, -5, -4, -3, -2, -1, 1, 2, 3, 4, 5, 6, 7]:
            check_minute = (minute + offset) % 60
            check_hour = now.hour + ((minute + offset) // 60)
            check_time = f"{check_hour:02d}:{check_minute:02d}"
            job = get_scheduled_job(check_time, is_weekend, day_of_week)
            if job:
                return job

        return None
