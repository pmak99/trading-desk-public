import pytest
import tempfile
import os
from src.core.job_manager import JobManager, get_scheduled_job


@pytest.fixture
def db_path():
    """Create temp database for tests."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    os.unlink(path)


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


def test_job_dependencies(db_path):
    """sentiment-scan depends on pre-market-prep."""
    manager = JobManager(db_path=db_path)
    deps = manager.get_dependencies("sentiment-scan")
    assert "pre-market-prep" in deps


def test_job_no_dependencies(db_path):
    """pre-market-prep has no dependencies."""
    manager = JobManager(db_path=db_path)
    deps = manager.get_dependencies("pre-market-prep")
    assert deps == []


def test_job_status_persistence(db_path):
    """Job status persists in database."""
    manager = JobManager(db_path=db_path)
    manager.record_status("test-job", "success")

    # Create new manager instance - should see the same status
    manager2 = JobManager(db_path=db_path)
    summary = manager2.get_day_summary()
    assert "test-job" in summary
    assert summary["test-job"] == "success"


def test_check_dependencies_satisfied(db_path):
    """Dependencies pass when all are successful."""
    manager = JobManager(db_path=db_path)
    manager.record_status("pre-market-prep", "success")

    can_run, reason = manager.check_dependencies("sentiment-scan")
    assert can_run is True
    assert reason == ""


def test_check_dependencies_not_satisfied(db_path):
    """Dependencies fail when prerequisite not run."""
    manager = JobManager(db_path=db_path)

    can_run, reason = manager.check_dependencies("sentiment-scan")
    assert can_run is False
    assert "pre-market-prep" in reason
