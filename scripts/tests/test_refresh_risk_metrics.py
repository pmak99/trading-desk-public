"""Trailing Sharpe/Sortino, recomputed into `risk_metrics_snapshot`.

Why this exists at all: the netting rule in `net_campaigns()` — each option leg
claimed by at most one overlapping single-name stock campaign — is not
expressible as a SQL aggregate, so Grafana cannot compute it in a panel. It is
computed in Python and read back with a plain SELECT.

Moved here from test_grafana_snapshot.py at the Postgres port: the metrics used
to be written into a disposable SQLite snapshot, and now go straight into the
one database Grafana reads.
"""


import pytest

from common.db import connect  # noqa: E402
from common.pgtesting import baseline_ddl  # noqa: E402
from scripts.refresh_risk_metrics import (  # noqa: E402
    RISK_WINDOWS,
    refresh_risk_metrics,
)

pytestmark = pytest.mark.pg


@pytest.fixture
def db(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("strategies", "trade_journal", "positions", "risk_metrics_snapshot"))
        conn.execute(
            "INSERT INTO strategies (symbol, strategy_type, acquired_date,"
            " sale_date, gain_loss, is_winner, account_type) VALUES"
            " ('AAPL','SPREAD','2026-01-05','2026-01-09', 1200.0, true, 'IRA'),"
            " ('MSFT','SPREAD','2026-02-05','2026-02-09', -400.0, false,'IRA'),"
            " ('NVDA','SPREAD','2026-03-05','2026-03-09',  900.0, true, 'IRA')"
        )
        conn.commit()
    return pg_schema


def test_creates_one_row_per_window(db):
    """One row per window even when a window has too little data for a ratio —
    otherwise the panel renders blank simply because a window is quiet, which
    is indistinguishable from the job never having run."""
    with connect() as conn:
        refresh_risk_metrics(conn)
        conn.commit()
        rows = conn.execute(
            "SELECT window_months FROM risk_metrics_snapshot ORDER BY window_months"
        ).fetchall()
    assert [r.window_months for r in rows] == sorted(RISK_WINDOWS)


def test_computed_values_match_the_pure_functions(db):
    """The table must hold what bootstrap_sharpe computes, not a reimplementation."""
    import bootstrap_sharpe as bs

    with connect() as conn:
        refresh_risk_metrics(conn)
        conn.commit()
        stored = {
            r.window_months: (r.sharpe, r.sortino, r.n_months)
            for r in conn.execute(
                "SELECT window_months, sharpe, sortino, n_months"
                " FROM risk_metrics_snapshot").fetchall()
        }
        monthly = bs.monthly_series(bs.net_campaigns(bs.load_positions(conn)))

    for months in RISK_WINDOWS:
        series = bs.trailing_window_series(monthly, months)
        assert stored[months] == (
            bs.sharpe_ratio(series), bs.sortino_ratio(series), len(series))


def test_refresh_is_idempotent(db):
    """It runs weekly. A second run must replace, never accumulate."""
    with connect() as conn:
        refresh_risk_metrics(conn)
        refresh_risk_metrics(conn)
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM risk_metrics_snapshot").fetchone()[0]
    assert n == len(RISK_WINDOWS)


def test_every_row_carries_the_time_it_was_computed(db):
    """Migration 8. Without this the panel cannot tell a current Sharpe from
    one left behind by a refresh that stopped running weeks ago — the table is
    precomputed, so a stalled job looks exactly like a healthy one."""
    with connect() as conn:
        refresh_risk_metrics(conn)
        conn.commit()
        stamps = [
            r.computed_at
            for r in conn.execute(
                "SELECT computed_at FROM risk_metrics_snapshot").fetchall()
        ]
    assert len(stamps) == len(RISK_WINDOWS)
    assert all(s is not None for s in stamps), (
        "a row with no computed_at defeats the column: it reads as 'never "
        "refreshed' on the panel")


def test_computed_at_has_no_column_default(db):
    """Deliberately no DEFAULT now() (migration 8): the value must come from
    the refresh that produced the row. A DEFAULT would stamp any INSERT --
    including a hand-written one or a restore -- as though a refresh had just
    run, which is the single thing this column exists to rule out."""
    with connect() as conn:
        default = conn.execute(
            "SELECT column_default FROM information_schema.columns"
            " WHERE table_name = 'risk_metrics_snapshot'"
            "   AND column_name = 'computed_at'"
            "   AND table_schema = current_schema()"
        ).fetchone()[0]
    assert default is None, (
        f"computed_at gained a column DEFAULT ({default!r}); it must be written "
        "by refresh_risk_metrics.py so a stalled refresh shows as a stale stamp")


def test_a_later_refresh_advances_the_stamp(db):
    """now() is transaction time, so the advance is only observable across
    transactions -- which is exactly how the weekly job runs."""
    with connect() as conn:
        refresh_risk_metrics(conn)
        conn.commit()
        first = conn.execute(
            "SELECT max(computed_at) FROM risk_metrics_snapshot").fetchone()[0]

    with connect() as conn:
        refresh_risk_metrics(conn)
        conn.commit()
        second = conn.execute(
            "SELECT max(computed_at) FROM risk_metrics_snapshot").fetchone()[0]

    assert second > first, (
        "a second refresh reused the first one's timestamp: the panel would "
        "show a stale stamp as fresh")
