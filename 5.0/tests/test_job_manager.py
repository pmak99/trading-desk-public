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


def test_midnight_rollover_friday_does_not_match_saturday_jobs():
    """get_current_job at Friday 23:55 must not match any Saturday job."""
    import pytz
    from unittest.mock import patch
    from datetime import datetime
    et = pytz.timezone("America/New_York")
    # Friday March 27, 2026 at 23:55 ET
    friday_night = et.localize(datetime(2026, 3, 27, 23, 55))
    with patch("src.core.job_manager.now_et", return_value=friday_night):
        manager = JobManager(db_path=":memory:")
        job = manager.get_current_job()
    # No weekday jobs are scheduled near midnight; Saturday jobs must not fire
    assert job is None, f"Friday 23:55 should not trigger any job, got: {job}"


def test_midnight_rollover_does_not_wrap_day_incorrectly():
    """Saturday 00:02 must match Saturday schedule, not weekday schedule."""
    import pytz
    from unittest.mock import patch
    from datetime import datetime
    et = pytz.timezone("America/New_York")
    # Saturday March 28, 2026 at 00:02 ET — within ±7 min of nothing (nearest Saturday job is 04:00)
    saturday_early = et.localize(datetime(2026, 3, 28, 0, 2))
    with patch("src.core.job_manager.now_et", return_value=saturday_early):
        manager = JobManager(db_path=":memory:")
        job = manager.get_current_job()
    assert job is None, f"Saturday 00:02 has no scheduled job (nearest is 04:00), got: {job}"
