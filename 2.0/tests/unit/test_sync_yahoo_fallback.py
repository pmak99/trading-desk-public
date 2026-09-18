"""The Yahoo-only fallback in scripts/sync_earnings_calendar.py.

When Finnhub's bulk calendar fails, the sync falls back to Yahoo per ticker.
Until 2026-09-13 that path wrote Yahoo's date whenever it differed from the
stored one: an INSERT keyed on (ticker, earnings_date), so a different date
became a SECOND row -- a duplicate quarter on one source, the exact mechanism
the two-source rule and backfill_historical's 15-day guard exist to stop.

With one source there is nothing to corroborate a change against, so the
fallback must never write one. A disagreement is logged NEEDS REVIEW and
skipped; the next sync with Finnhub up resolves it through the guarded path.
"""

from datetime import date, timedelta

import pytest

from common.db import connect  # noqa: E402
from common.pgtesting import baseline_ddl  # noqa: E402
from src.domain.errors import Ok  # noqa: E402
from src.domain.types import EarningsTiming  # noqa: E402

pytestmark = pytest.mark.pg

STORED = date.today() + timedelta(days=20)


class _Yahoo:
    def __init__(self, answer):
        self.answer = answer

    def get_next_earnings_date(self, ticker):
        return Ok((self.answer, EarningsTiming.AMC))


@pytest.fixture
def calendar(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("earnings_calendar"))
        conn.execute("INSERT INTO earnings_calendar (ticker, earnings_date, timing, confirmed)"
                     " VALUES ('AAPL', %s, 'AMC', true)", (STORED,))
        conn.commit()
    return pg_schema


def _rows():
    with connect() as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT ticker, earnings_date, timing FROM earnings_calendar ORDER BY 2")]


def _run(monkeypatch, yahoo_date):
    import scripts.sync_earnings_calendar as sync

    monkeypatch.setattr(sync, "YahooFinanceEarnings", lambda: _Yahoo(yahoo_date))
    return sync._sync_via_yahoo_fallback(stats=sync.SyncStats())


@pytest.mark.parametrize("offset", [1, 6, 40])
def test_a_yahoo_only_disagreement_is_skipped_not_written(calendar, monkeypatch, offset):
    stats = _run(monkeypatch, STORED + timedelta(days=offset))

    assert _rows() == [("AAPL", STORED, "AMC")]
    assert stats.new_dates == 0 and stats.updated_dates == 0
    assert stats.conflicts_detected == 1


def test_agreement_counts_as_unchanged(calendar, monkeypatch):
    stats = _run(monkeypatch, STORED)

    assert _rows() == [("AAPL", STORED, "AMC")]
    assert stats.unchanged_dates == 1
    assert stats.conflicts_detected == 0
