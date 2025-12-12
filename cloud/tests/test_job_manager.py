import pytest
from src.core.job_manager import JobManager, get_scheduled_job

def test_get_scheduled_job_weekday_morning():
    """5:30 AM weekday should dispatch pre-market-prep."""
    job = get_scheduled_job("05:30", is_weekend=False)
    assert job == "pre-market-prep"

def test_get_scheduled_job_weekday_digest():
    """7:30 AM weekday should dispatch morning-digest."""
    job = get_scheduled_job("07:30", is_weekend=False)
    assert job == "morning-digest"

def test_get_scheduled_job_saturday():
    """4:00 AM Saturday should dispatch weekly-backfill."""
    job = get_scheduled_job("04:00", is_weekend=True, day_of_week=5)  # Saturday
    assert job == "weekly-backfill"

def test_get_scheduled_job_no_match():
    """Random time with no scheduled job returns None."""
    job = get_scheduled_job("03:45", is_weekend=False)
    assert job is None

def test_job_dependencies():
    """sentiment-scan depends on pre-market-prep."""
    manager = JobManager()
    deps = manager.get_dependencies("sentiment-scan")
    assert "pre-market-prep" in deps

def test_job_no_dependencies():
    """pre-market-prep has no dependencies."""
    manager = JobManager()
    deps = manager.get_dependencies("pre-market-prep")
    assert deps == []
