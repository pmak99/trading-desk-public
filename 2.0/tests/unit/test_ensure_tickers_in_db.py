"""
Regression tests for ensure_tickers_in_db's earnings_date handling.

Root cause (2026-07-29): ensure_tickers_in_db wrote an unconditional
`date.today() + 7 days` placeholder for every newly-discovered ticker,
with timing='UNKNOWN', confirmed=0. Because the follow-on sync step only
processes tickers that appear in `strategies`/`trade_journal` (the
"focused" universe), untraded tickers whose real earnings date is months
away (they already reported this quarter) kept the fake near-term date
permanently -- and that fake date fed a bogus VRP/implied-move calc in
whisper mode, since it always fell inside the scan week by construction.

Fix: ensure_tickers_in_db must look up the real earnings date (Yahoo
Finance) for each newly-discovered ticker *before* inserting anything.
A resolved date is written as confirmed. A failed lookup means no row
is inserted at all -- never a guessed date.
"""
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from common.db import connect
from src.domain.enums import EarningsTiming
from src.domain.errors import AppError, Err, ErrorCode, Ok
from src.infrastructure.data_sources.yahoo_finance_earnings import YahooFinanceEarnings

pytestmark = pytest.mark.pg


def _yahoo_returns(result):
    """Yahoo's next-earnings lookup answers `result`. The vendor method is the
    seam, whichever instance earnings_fetcher holds."""
    return patch.object(YahooFinanceEarnings, "get_next_earnings_date", return_value=result)

SCHEMA = """
CREATE TABLE earnings_calendar (
    ticker text NOT NULL,
    earnings_date date NOT NULL,
    timing text NOT NULL CHECK(timing IN ('BMO', 'AMC', 'DMH', 'UNKNOWN')),
    confirmed boolean DEFAULT false,
    created_at timestamptz DEFAULT now(),
    updated_at timestamptz DEFAULT now(),
    last_validated_at timestamptz,
    PRIMARY KEY (ticker, earnings_date)
);
"""


def _make_db(schema):
    with connect() as conn:
        conn.execute(SCHEMA)
        conn.commit()
    return schema


def _make_container(db_path):
    container = MagicMock()
    return container


def test_resolved_ticker_gets_real_date_not_placeholder(pg_schema):
    """A ticker whose real next earnings is months out must be stored with
    that real date, not a today+7 guess that happens to land in-week."""
    from scripts.scan import earnings_fetcher

    db_path = _make_db(pg_schema)
    container = _make_container(db_path)

    real_date = date(2026, 10, 20)  # KO's actual next earnings, far outside the scan week
    with _yahoo_returns(Ok((real_date, EarningsTiming.AMC))):
        earnings_fetcher.ensure_tickers_in_db(["KO"], container)

    with connect() as conn:
        rows = conn.execute(
            "SELECT earnings_date, timing, confirmed FROM earnings_calendar"
            " WHERE ticker='KO'"
        ).fetchall()

    assert len(rows) == 1, f"expected exactly one KO row, got {rows}"
    earnings_date, timing, confirmed = rows[0]
    earnings_date_str = earnings_date.isoformat()
    placeholder = (date.today() + timedelta(days=7)).isoformat()
    assert earnings_date_str == real_date.isoformat(), (
        f"KO stored as {earnings_date_str}, but real date is {real_date.isoformat()} "
        f"(placeholder would have been {placeholder})"
    )
    assert timing == "AMC"
    assert confirmed == 1


def test_failed_lookup_inserts_no_row(pg_schema):
    """If the real earnings date can't be resolved, do not guess -- insert nothing."""
    from scripts.scan import earnings_fetcher

    db_path = _make_db(pg_schema)
    container = _make_container(db_path)

    with _yahoo_returns(Err(AppError(ErrorCode.NODATA, "no calendar data"))):
        earnings_fetcher.ensure_tickers_in_db(["BADTICK"], container)

    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM earnings_calendar WHERE ticker='BADTICK'"
        ).fetchall()

    assert rows == [], f"expected no placeholder row for an unresolvable ticker, got {rows}"


def test_genuine_this_week_ticker_still_resolves_correctly(pg_schema):
    """A ticker that really does report soon should be stored with its real
    (near-term) date -- confirms the fix doesn't just reject everything."""
    from scripts.scan import earnings_fetcher

    db_path = _make_db(pg_schema)
    container = _make_container(db_path)

    near_date = date.today() + timedelta(days=2)
    with _yahoo_returns(Ok((near_date, EarningsTiming.BMO))):
        earnings_fetcher.ensure_tickers_in_db(["NEWCO"], container)

    with connect() as conn:
        rows = conn.execute(
            "SELECT earnings_date, timing, confirmed FROM earnings_calendar"
            " WHERE ticker='NEWCO'"
        ).fetchall()

    assert [tuple(r) for r in rows] == [(near_date, "BMO", True)]


def test_already_present_ticker_is_untouched(pg_schema):
    """Tickers already in the DB with a future earnings_date should not trigger
    a lookup or a rewrite at all."""
    from scripts.scan import earnings_fetcher

    db_path = _make_db(pg_schema)
    container = _make_container(db_path)

    existing_date = (date.today() + timedelta(days=15)).isoformat()
    with connect() as conn:
        conn.execute(
            "INSERT INTO earnings_calendar (ticker, earnings_date, timing, confirmed) "
            "VALUES ('EXIST', %s, 'AMC', true)",
            (existing_date,),
        )
        conn.commit()

    with _yahoo_returns(None) as yahoo_lookup:
        earnings_fetcher.ensure_tickers_in_db(["EXIST"], container)

    yahoo_lookup.assert_not_called()


@pytest.mark.parametrize("stored_days_ago,yahoo_days_ahead,inserted", [
    (1, 1, False),     # Yahoo's date for the event that just printed, one day over
    (3, 10, False),    # 13 days apart: still the same quarter
    (80, 10, True),    # a real next quarter
])
def test_a_date_beside_a_stored_event_is_not_inserted(
        pg_schema, stored_days_ago, yahoo_days_ahead, inserted):
    """The only check was 'no FUTURE row', so a ticker whose event printed
    yesterday got Yahoo's date for that same event inserted as a second,
    confirmed row. Yahoo files pre-market reports as the previous evening, so
    that happens -- it is the duplicate quarter backfill_historical's 15-day
    guard was added for (2026-09-13)."""
    from scripts.scan import earnings_fetcher

    _make_db(pg_schema)
    stored = date.today() - timedelta(days=stored_days_ago)
    yahoo = date.today() + timedelta(days=yahoo_days_ahead)
    with connect() as conn:
        conn.execute("INSERT INTO earnings_calendar (ticker, earnings_date, timing, confirmed)"
                     " VALUES ('NEAR', %s, 'BMO', true)", (stored,))
        conn.commit()

    with _yahoo_returns(Ok((yahoo, EarningsTiming.AMC))):
        earnings_fetcher.ensure_tickers_in_db(["NEAR"], MagicMock())

    with connect() as conn:
        dates = [r[0] for r in conn.execute(
            "SELECT earnings_date FROM earnings_calendar WHERE ticker='NEAR' ORDER BY 1")]
    assert dates == ([stored, yahoo] if inserted else [stored])
