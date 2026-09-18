"""Shared envelope for scheduled 2.0 jobs.

Three properties matter here, and each has bitten a real system:

1. A job must not double-run when its trigger fires twice.
2. A failure must be *reported*, not just logged into a file nobody opens —
   that was the whole value of the 5.0 Telegram path being retired.
3. The reporting itself must never mask or replace the underlying failure.

The force flag exists because the naive once-per-day claim makes a failed job
unrunnable for the rest of the day: the 07:30 digest breaks, you fix it at
07:40, and the manual re-run silently exits having done nothing.

Postgres port: `BEGIN IMMEDIATE` is gone and the claim is now a single
`INSERT ... ON CONFLICT DO UPDATE ... WHERE ... RETURNING`. The claim-state
cases below are unchanged in intent and are the reason to trust the rewrite —
they encode the rule, so they are the test that the new SQL expresses the same
one. `test_creates_its_own_table_on_a_fresh_database` was dropped: the runner
no longer creates its table, because the baseline schema owns it and a wrong
DSN now fails to connect rather than fabricating a database.

`test_two_processes_race_for_one_slot` is new. The SQLite version could only
approximate concurrency with a nested call; a real pool lets 12 threads
actually contend, which is what the ON CONFLICT rewrite has to survive.
"""

import concurrent.futures
import re
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from common.pgtesting import baseline_ddl
from scripts.jobs.runner import (
    LOCK_HELD,
    STALE_RUNNING_AFTER_SEC,
    now_et,
    run_job,
)

pytestmark = pytest.mark.pg


@pytest.fixture
def db(pg_pool, monkeypatch):
    """Point the runner at a throwaway schema holding just job_status.

    Patches `get_pool` in the runner's namespace rather than the pool module's,
    so the production singleton is never opened by a test.
    """
    with pg_pool.connection() as conn:
        conn.execute(baseline_ddl("job_status"))
        conn.commit()
    monkeypatch.setattr("scripts.jobs.runner.get_pool", lambda: pg_pool)
    return pg_pool


def _status(pool, job="digest"):
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT status FROM job_status WHERE job_name = %s", (job,)
        ).fetchone()
    return row.status if row else None


def _seed(pool, status, age_sec=0, job="digest"):
    """Write a claim row for today with a chosen age."""
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO job_status (date, job_name, status, updated_at)"
            " VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (date, job_name) DO UPDATE"
            " SET status = EXCLUDED.status, updated_at = EXCLUDED.updated_at",
            (now_et().date(), job, status,
             now_et() - timedelta(seconds=age_sec)),
        )
        conn.commit()


def test_success_writes_status_row_and_returns_zero(db):
    assert run_job("digest", lambda: "sent 3 rows") == 0
    assert _status(db) == "success"


def _pings(monkeypatch, error=None):
    """Record every URL the runner GETs -- the ping's HTTP call is the seam."""
    import scripts.jobs.runner as runner_module

    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if error is not None:
            raise error

    monkeypatch.setattr(runner_module.requests, "get", fake_get)
    return calls


def test_success_pings_the_freshness_monitor_when_a_push_url_is_given(db, monkeypatch):
    """The Uptime Kuma freshness backstop (Task 9B Step 7) is a ping on success,
    not on the runner's own health -- a job that never runs at all must not
    silently starve the monitor with no signal to investigate."""
    calls = _pings(monkeypatch)
    run_job("digest", lambda: None, push_url="https://kuma.example/api/push/tok")
    assert calls == ["https://kuma.example/api/push/tok"]


def test_no_push_url_means_no_ping_attempt(db, monkeypatch):
    calls = _pings(monkeypatch)
    run_job("digest", lambda: None)
    assert calls == []


def test_failure_does_not_ping_the_freshness_monitor(db, monkeypatch):
    """A failed run must not report itself as fresh -- that would defeat the
    entire purpose of the backstop."""
    calls = _pings(monkeypatch)

    def boom():
        raise RuntimeError("nope")

    run_job("digest", boom, push_url="https://kuma.example/api/push/tok")
    assert calls == []


def _ping_capturing(monkeypatch):
    import scripts.jobs.runner as runner_module

    captured = {}

    def fake_get(url, timeout=None, verify=None):
        captured["verify"] = verify
        captured["timeout"] = timeout

        class R:
            pass

        return R()

    monkeypatch.setattr(runner_module.requests, "get", fake_get)
    runner_module._ping_freshness_monitor("https://uptime.internal/api/push/tok")
    return captured


def test_freshness_ping_verifies_tls_against_the_homelab_ca(monkeypatch, tmp_path):
    """Until 2026-09-14 this ping sent verify=False: the CA sat under
    /home/the server, which `trading` cannot traverse. The copy at HOMELAB_CA is
    readable, and record_moves and weekly-maintenance.sh already verify
    against it (audit step 6.5)."""
    import scripts.jobs.runner as runner_module

    ca = tmp_path / "homelab-ca.crt"
    ca.write_text("pem")
    monkeypatch.setattr(runner_module, "HOMELAB_CA", str(ca))

    captured = _ping_capturing(monkeypatch)
    assert captured["verify"] == str(ca)
    assert captured["timeout"] == runner_module.PUSH_TIMEOUT_SEC


def test_freshness_ping_falls_back_to_system_roots_never_to_no_verification(monkeypatch, tmp_path):
    import scripts.jobs.runner as runner_module

    monkeypatch.setattr(runner_module, "HOMELAB_CA", str(tmp_path / "absent.crt"))
    assert _ping_capturing(monkeypatch)["verify"] is True


def test_tls_verification_is_never_disabled_in_code():
    """The static half: no request anywhere turns verification off, and the CA
    path has one definition. A second copy is how the runner kept verify=False
    while record_moves already had a working CA."""
    repo = Path(__file__).resolve().parents[3]
    disabled, ca_paths = [], []
    for root in ("2.0/src", "2.0/scripts", "scripts", "common"):
        for path in (repo / root).rglob("*.py"):
            if {"venv", "tests"} & set(path.parts):
                continue
            for i, line in enumerate(path.read_text().splitlines(), 1):
                where = f"{path.relative_to(repo)}:{i}: {line.strip()}"
                # A keyword argument or an attribute (session.verify = False);
                # prose that names the rule ("Never verify=False.") is neither.
                if re.search(r"verify\s*=\s*(False|0)\s*[,)]|\.verify\s*=\s*(False|0)\b", line):
                    disabled.append(where)
                if "homelab-ca.crt" in line:
                    ca_paths.append(where)
    assert disabled == []
    assert len(ca_paths) == 1 and ca_paths[0].startswith("2.0/scripts/jobs/runner.py:"), ca_paths


def test_a_broken_monitor_ping_does_not_fail_the_job(db, monkeypatch):
    """The freshness ping is observability, not a dependency -- Uptime Kuma
    being unreachable must never turn a successful digest into a failure."""
    import requests

    calls = _pings(monkeypatch, error=requests.ConnectionError("kuma unreachable"))
    rc = run_job("digest", lambda: "ok", push_url="https://kuma.example/api/push/tok")
    assert calls == ["https://kuma.example/api/push/tok"]
    assert rc == 0
    assert _status(db) == "success"


def test_failure_records_error_and_notifies(db):
    sender = Mock()

    def boom():
        raise ValueError("kaboom")

    assert run_job("digest", boom, sender=sender) == 1
    assert _status(db) == "error"
    sender.send_error.assert_called_once()
    assert "kaboom" in sender.send_error.call_args[0][0]


def test_failure_report_names_the_job(db):
    sender = Mock()
    run_job("morning-digest", lambda: 1 / 0, sender=sender)
    assert "morning-digest" in sender.send_error.call_args[1]["context"]


def test_second_concurrent_run_is_refused(db):
    """A nested call models the trigger firing twice while the first is live."""
    seen = []

    def outer():
        seen.append(run_job("digest", lambda: "inner"))
        return "outer"

    assert run_job("digest", outer) == 0
    assert seen == [LOCK_HELD]


def test_two_processes_race_for_one_slot(db):
    """The property BEGIN IMMEDIATE used to provide, now provided by row-level
    locking on ON CONFLICT: under genuine contention exactly one claimer wins.

    The nested-call test above cannot show this — it is sequential.
    """
    ran = []

    def work():
        ran.append(1)
        return "ok"

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:
        codes = [f.result() for f in
                 [ex.submit(run_job, "digest", work) for _ in range(12)]]

    assert codes.count(0) == 1, codes
    assert codes.count(LOCK_HELD) == 11, codes
    assert len(ran) == 1


def test_job_runs_even_with_no_sender(db):
    def boom():
        raise ValueError("kaboom")

    assert run_job("digest", boom, sender=None) == 1
    assert _status(db) == "error"


def test_notification_failure_does_not_mask_job_failure(db):
    """If Telegram is down too, the job's own exit code must still be the
    failure — not a second, different failure from the reporter."""
    sender = Mock()
    sender.send_error.side_effect = OSError("telegram down")

    def boom():
        raise ValueError("kaboom")

    assert run_job("digest", boom, sender=sender) == 1
    assert _status(db) == "error"


def test_same_day_rerun_refused_without_force(db):
    assert run_job("digest", lambda: "first") == 0
    assert run_job("digest", lambda: "second") == LOCK_HELD


def test_force_allows_same_day_rerun_after_failure(db):
    def boom():
        raise ValueError("kaboom")

    assert run_job("digest", boom) == 1
    assert run_job("digest", lambda: "retry", force=True) == 0
    assert _status(db) == "success"


def test_refused_run_does_not_execute_the_work(db):
    calls = []
    run_job("digest", lambda: calls.append("first"))
    run_job("digest", lambda: calls.append("second"))
    assert calls == ["first"]


# ---------------------------------------------------------------------------
# Claim-state semantics. Added after code review found four separate ways the
# once-per-day claim could either block a legitimate run forever or permit a
# genuine double-run. Unchanged across the Postgres port -- they are the
# specification the new ON CONFLICT ... WHERE clause had to reproduce.
# ---------------------------------------------------------------------------

def test_live_running_claim_is_never_stolen_even_with_force(db):
    """force must not start a second copy of a job that is genuinely running —
    that is the exact double-run the module exists to prevent."""
    _seed(db, "running", age_sec=60)
    calls = []
    assert run_job("digest", lambda: calls.append("x"), force=True) == LOCK_HELD
    assert calls == []


def test_stale_running_claim_is_reclaimed_without_force(db):
    """A job SIGKILLed at TimeoutStartSec leaves status='running' forever.
    Nothing should need a human to notice."""
    _seed(db, "running", age_sec=STALE_RUNNING_AFTER_SEC + 60)
    assert run_job("digest", lambda: "recovered") == 0
    assert _status(db) == "success"


def test_errored_claim_is_retryable_without_force(db):
    """A boot-time catch-up that failed pre-network must not consume the day."""
    _seed(db, "error")
    assert run_job("digest", lambda: "retry") == 0
    assert _status(db) == "success"


def test_successful_claim_still_blocks_without_force(db):
    _seed(db, "success")
    assert run_job("digest", lambda: "again") == LOCK_HELD


def test_successful_claim_is_overridable_with_force(db):
    _seed(db, "success")
    assert run_job("digest", lambda: "again", force=True) == 0
