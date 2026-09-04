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
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.domain.errors import Result, AppError, ErrorCode
from src.domain.enums import EarningsTiming

SCHEMA = """
CREATE TABLE earnings_calendar (
    ticker TEXT NOT NULL,
    earnings_date DATE NOT NULL,
    timing TEXT NOT NULL CHECK(timing IN ('BMO', 'AMC', 'DMH', 'UNKNOWN')),
    confirmed BOOLEAN DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_validated_at DATETIME,
    PRIMARY KEY (ticker, earnings_date)
);
"""


def _make_db(tmp_path):
    db_path = str(tmp_path / "test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    return db_path


def _make_container(db_path):
    container = MagicMock()
    container.config.database.path = db_path
    return container


def test_resolved_ticker_gets_real_date_not_placeholder(tmp_path):
    """A ticker whose real next earnings is months out must be stored with
    that real date, not a today+7 guess that happens to land in-week."""
    from scripts.scan import earnings_fetcher

    db_path = _make_db(tmp_path)
    container = _make_container(db_path)

    real_date = date(2026, 10, 20)  # KO's actual next earnings, far outside the scan week
    mock_yf = MagicMock()
    mock_yf.get_next_earnings_date.return_value = Result.Ok((real_date, EarningsTiming.AMC))

    with patch.object(earnings_fetcher, "_get_yf_earnings", return_value=mock_yf):
        earnings_fetcher.ensure_tickers_in_db(["KO"], container)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT earnings_date, timing, confirmed FROM earnings_calendar WHERE ticker='KO'"
    ).fetchall()
    conn.close()

    assert len(rows) == 1, f"expected exactly one KO row, got {rows}"
    earnings_date_str, timing, confirmed = rows[0]
    placeholder = (date.today() + timedelta(days=7)).isoformat()
    assert earnings_date_str == real_date.isoformat(), (
        f"KO stored as {earnings_date_str}, but real date is {real_date.isoformat()} "
        f"(placeholder would have been {placeholder})"
    )
    assert timing == "AMC"
    assert confirmed == 1


def test_failed_lookup_inserts_no_row(tmp_path):
    """If the real earnings date can't be resolved, do not guess -- insert nothing."""
    from scripts.scan import earnings_fetcher

    db_path = _make_db(tmp_path)
    container = _make_container(db_path)

    mock_yf = MagicMock()
    mock_yf.get_next_earnings_date.return_value = Result.Err(
        AppError(ErrorCode.NODATA, "no calendar data")
    )

    with patch.object(earnings_fetcher, "_get_yf_earnings", return_value=mock_yf):
        earnings_fetcher.ensure_tickers_in_db(["BADTICK"], container)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT * FROM earnings_calendar WHERE ticker='BADTICK'"
    ).fetchall()
    conn.close()

    assert rows == [], f"expected no placeholder row for an unresolvable ticker, got {rows}"


def test_genuine_this_week_ticker_still_resolves_correctly(tmp_path):
    """A ticker that really does report soon should be stored with its real
    (near-term) date -- confirms the fix doesn't just reject everything."""
    from scripts.scan import earnings_fetcher

    db_path = _make_db(tmp_path)
    container = _make_container(db_path)

    near_date = date.today() + timedelta(days=2)
    mock_yf = MagicMock()
    mock_yf.get_next_earnings_date.return_value = Result.Ok((near_date, EarningsTiming.BMO))

    with patch.object(earnings_fetcher, "_get_yf_earnings", return_value=mock_yf):
        earnings_fetcher.ensure_tickers_in_db(["NEWCO"], container)

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT earnings_date, timing, confirmed FROM earnings_calendar WHERE ticker='NEWCO'"
    ).fetchall()
    conn.close()

    assert rows == [(near_date.isoformat(), "BMO", 1)]


def test_already_present_ticker_is_untouched(tmp_path):
    """Tickers already in the DB with a future earnings_date should not trigger
    a lookup or a rewrite at all."""
    from scripts.scan import earnings_fetcher

    db_path = _make_db(tmp_path)
    container = _make_container(db_path)

    existing_date = (date.today() + timedelta(days=15)).isoformat()
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO earnings_calendar (ticker, earnings_date, timing, confirmed) "
        "VALUES ('EXIST', ?, 'AMC', 1)",
        (existing_date,),
    )
    conn.commit()
    conn.close()

    mock_yf = MagicMock()
    with patch.object(earnings_fetcher, "_get_yf_earnings", return_value=mock_yf):
        earnings_fetcher.ensure_tickers_in_db(["EXIST"], container)

    mock_yf.get_next_earnings_date.assert_not_called()
