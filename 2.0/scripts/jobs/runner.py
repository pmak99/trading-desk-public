"""Shared envelope for scheduled 2.0 jobs.

Wraps a job body with the three things every scheduled job needs and none of
them should reimplement: a once-per-day claim so a double-fired trigger cannot
double-run, a status row, and a failure report that reaches a human.

⚠️ **`BEGIN IMMEDIATE` has no Postgres equivalent, and it was the whole
correctness argument.** SQLite serialised competing claimers on a write lock
and then decided in Python. Postgres expresses the same rule declaratively: one
`INSERT ... ON CONFLICT DO UPDATE` whose `WHERE` encodes exactly which prior
states may be taken over. Row-level locking makes it atomic, and `RETURNING`
says whether this process won — zero rows back means the slot is held.

The rule it encodes is unchanged from the SQLite version:

    no row              -> claim
    'running' + fresh   -> REFUSE, even with force. A live job must never be
                           duplicated; that is what this module is for.
    'running' + stale   -> claim. A job SIGKILLed at TimeoutStartSec never ran
                           _set_status, so the row would otherwise sit
                           'running' forever and silently no-op every
                           subsequent trigger.
    'error'             -> claim. A failed run must not consume the day —
                           notably a Persistent= boot catch-up that fired
                           before the network was up.
    'success'           -> REFUSE unless force.

Note `force` appears in exactly one disjunct. It can release a completed day;
it can never touch a live claim.

⚠️ `job_status.updated_at` is `timestamptz`, not text. The staleness test is
real timestamp arithmetic, and as text it would be lexicographic — which
disagrees with real time across a DST offset change ('01:30:00-04:00' and
'01:30:00-05:00' sort adjacent but are an hour apart). Enough to reclaim a live
claim or pin a dead one open. See TYPE_OVERRIDES in generate_pg_ddl.py.
"""

import logging
import os
import traceback
from typing import Callable, Optional, Union

import requests

from src.infrastructure.database.pg_pool import get_pool
from src.infrastructure.notifications import TelegramSender

logger = logging.getLogger(__name__)

from common.timezone import now_et  # noqa: E402

# Uptime Kuma push monitor timeout. This is a fire-and-forget freshness
# backstop, not a dependency -- keep it short so a hung monitor can never
# meaningfully delay a job's exit.
PUSH_TIMEOUT_SEC = 5

# Public certificate of the homelab CA that signs *.internal (Traefik), copied from
# /home/admin/docker/configs/traefik/certs/ca.crt to where `trading` can read
# it. The one definition for Python: record_moves imports it, and
# weekly-maintenance.sh's HOMELAB_CA is pinned to it by a test.
HOMELAB_CA = "/home/trading/certs/homelab-ca.crt"

# Distinct from 0 (ran, succeeded) and 1 (ran, failed): the job did NOT run
# because today's slot was already claimed.
LOCK_HELD = 2

# A 'running' row older than this is treated as a dead job, not a live one.
# systemd's TimeoutStartSec on trading-job@.service is 1800s, so anything past
# an hour cannot still be running.
STALE_RUNNING_AFTER_SEC = 3600

CLAIM_SQL = """
    INSERT INTO job_status (date, job_name, status, updated_at)
    VALUES (%(date)s, %(name)s, 'running', now())
    ON CONFLICT (date, job_name) DO UPDATE
       SET status = 'running', updated_at = now()
     WHERE job_status.status = 'error'
        OR (job_status.status = 'running'
            AND job_status.updated_at
                < now() - make_interval(secs => %(stale_after)s))
        OR (job_status.status = 'success' AND %(force)s)
    RETURNING id
"""


def _claim(date_str: str, name: str, force: bool) -> bool:
    """Take today's slot for `name`. Returns False if the job must not run."""
    with get_pool().connection() as conn:
        won = conn.execute(CLAIM_SQL, {
            "date": date_str, "name": name, "force": force,
            "stale_after": STALE_RUNNING_AFTER_SEC,
        }).fetchone()
        conn.commit()

        if won:
            return True

        # Lost. Read back why, for the log only — the decision is already made
        # and cannot change on this information.
        row = conn.execute(
            "SELECT status, updated_at FROM job_status"
            " WHERE date = %s AND job_name = %s", (date_str, name)
        ).fetchone()

    if row and row.status == "running":
        age = int((now_et() - row.updated_at).total_seconds())
        logger.warning(
            f"job {name} is already running ({age}s); refusing"
            + (" (force does not steal a live claim)" if force else "")
        )
    else:
        logger.warning(f"job {name} already succeeded for {date_str}; refusing")
    return False


def _set_status(date_str: str, name: str, status: str) -> None:
    with get_pool().connection() as conn:
        conn.execute(
            "UPDATE job_status SET status = %s, updated_at = now()"
            " WHERE date = %s AND job_name = %s",
            (status, date_str, name),
        )
        conn.commit()


def tls_verify(ca_path: Optional[str] = None) -> Union[str, bool]:
    """`verify=` for a request to a the server `*.internal` service. Never False.

    The homelab CA's certificate when it is present (the server), else the system
    roots (a Mac run, where a *.internal push is not expected to succeed and fails
    as a logged warning rather than silently trusting anything).
    """
    path = HOMELAB_CA if ca_path is None else ca_path
    return path if os.path.exists(path) else True


def _ping_freshness_monitor(url: str) -> None:
    """Notify an Uptime Kuma push monitor that a job just succeeded.

    This is a backstop for the failure mode that motivated it: a job that
    stops running with no exception to report (a disabled timer, a dead
    host, a future job_status-class bug) leaves no signal at all otherwise.
    A run that raises is already reported directly via Telegram
    (run_job's except branch) — this call happens only on the success path.

    TLS is verified against the homelab CA (`tls_verify`). Until 2026-09-14
    this ping skipped verification, because the only copy of the CA then was
    `/home/admin/docker/configs/traefik/certs/ca.crt`, under a directory
    `trading` cannot traverse. The readable copy at HOMELAB_CA removed that
    reason: record_moves and weekly-maintenance.sh already verified against it
    while this ping, holding its own reasoning, did not. The push token is a
    bearer secret in the URL, so an unverified connection to a spoofed
    `uptime.internal` would hand it over.
    """
    requests.get(url, timeout=PUSH_TIMEOUT_SEC, verify=tls_verify())


def run_job(
    name: str,
    fn: Callable[[], Optional[str]],
    sender: Optional[TelegramSender] = None,
    force: bool = False,
    push_url: Optional[str] = None,
) -> int:
    """Run `fn` once per ET day under an atomic claim.

    Args:
        name: job name, e.g. "morning-digest".
        fn: the work. Its return value is logged as a summary.
        sender: optional — failures are reported here when present.
        force: release a *completed* day's claim, so a job that errored at its
            scheduled time can be fixed and re-run by hand. Without it the
            claim would refuse for the rest of the day and the manual re-run
            would exit LOCK_HELD having done nothing. Never releases a live
            claim.
        push_url: optional Uptime Kuma push-monitor URL, pinged only on
            success. Never blocks or fails the job — see _ping_freshness_monitor.

    Returns:
        0 success, 1 the job raised, LOCK_HELD (2) the slot was already claimed.

    Note the `db_path` argument is gone: job_status lives in the one Postgres
    database now, not in a per-host `job_status.db` sitting beside it. That
    file existed only because a whole-file `gsutil cp` kept overwriting the
    table (CLAUDE.md, Sep 11 2026) — a SQLite-sync failure mode that cannot
    occur here.
    """
    today = now_et().date().isoformat()

    if not _claim(today, name, force):
        return LOCK_HELD

    try:
        summary = fn()
    except Exception as e:
        detail = f"{type(e).__name__}: {e}\n\n{traceback.format_exc()[-1500:]}"
        logger.error(f"job {name} failed: {detail}")
        _set_status(today, name, "error")

        if sender is not None:
            try:
                sender.send_error(detail, context=f"job:{name}")
            except Exception:
                # The report failing must not replace the job's own failure —
                # the caller needs exit 1 for the real error, not a new one.
                logger.error("failed to report job failure to Telegram")

        return 1

    _set_status(today, name, "success")
    logger.info(f"job {name} ok: {summary or ''}")

    if push_url:
        try:
            _ping_freshness_monitor(push_url)
        except Exception:
            # Observability, not a dependency: Uptime Kuma being unreachable
            # must never turn a successful job into a reported failure.
            logger.warning(f"job {name} succeeded but the freshness ping failed")

    return 0
