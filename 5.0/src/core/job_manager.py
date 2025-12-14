"""
Job dispatcher and dependency manager.

Single dispatcher pattern: Cloud Scheduler calls /dispatch every 15 min,
and this module routes to the correct job based on current time.
"""

import sqlite3
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


def _validate_no_cycles():
    """Validate that job dependencies form a DAG (no circular dependencies)."""
    def find_cycle(job: str, visited: set, rec_stack: set, path: list) -> list:
        """Return cycle path if found, empty list otherwise."""
        visited.add(job)
        rec_stack.add(job)
        path.append(job)

        for dep in JOB_DEPENDENCIES.get(job, []):
            if dep not in visited:
                cycle = find_cycle(dep, visited, rec_stack, path)
                if cycle:
                    return cycle
            elif dep in rec_stack:
                # Found cycle - return path from dep to current
                cycle_start = path.index(dep)
                return path[cycle_start:] + [dep]

        path.pop()
        rec_stack.remove(job)
        return []

    for job in JOB_DEPENDENCIES:
        cycle = find_cycle(job, set(), set(), [])
        if cycle:
            cycle_path = " -> ".join(cycle)
            raise ValueError(f"Circular dependency detected: {cycle_path}")


# Validate at import time
_validate_no_cycles()


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
    """Manages job dispatch and dependency checking with persistent storage."""

    def __init__(self, db_path: str = "data/ivcrush.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize job_status table."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_status (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    job_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(date, job_name)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_job_status_date
                ON job_status(date)
            """)
            conn.commit()
        finally:
            conn.close()

    def get_dependencies(self, job_name: str) -> List[str]:
        """Get list of jobs that must succeed before this job."""
        return JOB_DEPENDENCIES.get(job_name, [])

    def _get_status(self, date: str, job_name: str) -> str:
        """Get job status from database."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT status FROM job_status WHERE date = ? AND job_name = ?",
                (date, job_name)
            )
            row = cursor.fetchone()
            return row[0] if row else "not_run"
        finally:
            conn.close()

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

        for dep in deps:
            status = self._get_status(today, dep)
            if status != "success":
                return False, f"Dependency '{dep}' status: {status}"

        return True, ""

    def record_status(self, job_name: str, status: str):
        """Record job completion status to persistent storage.

        Raises:
            sqlite3.Error: On database errors (don't swallow - caller should handle)
        """
        today = today_et()
        timestamp = now_et().isoformat()

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                INSERT INTO job_status (date, job_name, status, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date, job_name) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at
            """, (today, job_name, status, timestamp))
            conn.commit()
            log("info", "Job status recorded", job=job_name, status=status)
        except sqlite3.Error as e:
            log("error", "Failed to record job status", error=str(e), job=job_name)
            raise  # Don't swallow database errors - caller should know
        finally:
            conn.close()

    def get_day_summary(self, date: Optional[str] = None) -> Dict[str, str]:
        """Get all job statuses for a given day."""
        if date is None:
            date = today_et()

        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.execute(
                "SELECT job_name, status FROM job_status WHERE date = ?",
                (date,)
            )
            return {row[0]: row[1] for row in cursor.fetchall()}
        finally:
            conn.close()

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
