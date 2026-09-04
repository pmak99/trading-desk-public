"""
Unit tests for fetch_earnings_for_ticker()'s stale-date corroboration logic
in scripts/scan/earnings_fetcher.py.

This function is called on every /whisper and ./trade.sh TICKER DATE run.
Fixed 2026-07-27: when a ticker's earnings are within 7 days and the DB
row hasn't been re-checked in 24h, a single Yahoo Finance read used to be
enough to overwrite an already-validated earnings_calendar row outright.
That's the same single-source-overwrite defect that corrupted PYPL's date
in scripts/sync_earnings_calendar.py (see test_cleanup_duplicate_earnings.py)
-- except here there wasn't even a documented "conflict" check to bypass;
the overwrite was unconditional. Now a second source (Finnhub) must agree
with Yahoo before the DB row changes.
"""

import sys
import sqlite3
from pathlib import Path
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.domain.errors import Result
from src.domain.types import EarningsTiming
from scripts.scan.earnings_fetcher import fetch_earnings_for_ticker


def _create_db(path: Path) -> str:
    db_path = str(path / "test_ivcrush.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE earnings_calendar (
            ticker TEXT NOT NULL,
            earnings_date DATE NOT NULL,
            timing TEXT NOT NULL,
            confirmed BOOLEAN DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_validated_at DATETIME,
            PRIMARY KEY (ticker, earnings_date)
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_stale_near_term(db_path, ticker, earnings_date, timing="BMO"):
    """Insert a row that's within 7 days out and last validated >24h ago,
    so fetch_earnings_for_ticker's revalidation branch fires."""
    stale_ts = (datetime.now() - timedelta(hours=30)).isoformat(sep=" ")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO earnings_calendar
           (ticker, earnings_date, timing, confirmed, updated_at, last_validated_at)
           VALUES (?, ?, ?, 1, ?, ?)""",
        (ticker, earnings_date.isoformat(), timing, stale_ts, stale_ts),
    )
    conn.commit()
    conn.close()


def _get_row(db_path, ticker):
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT earnings_date, timing, confirmed FROM earnings_calendar WHERE ticker = ?",
        (ticker,),
    ).fetchone()
    conn.close()
    return row


def _make_container(db_path, finnhub_date=None, finnhub_timing="BMO"):
    container = MagicMock()
    container.config.database.path = db_path
    if finnhub_date is None:
        container.finnhub.get_earnings_calendar.return_value = Result.Err(
            "unused"  # only .is_err is checked
        )
    else:
        container.finnhub.get_earnings_calendar.return_value = Result.Ok(
            [("TICKER", finnhub_date, EarningsTiming(finnhub_timing))]
        )
    return container


class TestCorroborationRequired:

    def test_finnhub_agrees_with_yahoo_updates_db(self, tmp_path):
        """Yahoo proposes a new date; Finnhub (fresh) agrees -> DB is updated."""
        db = _create_db(tmp_path)
        old_date = date.today() + timedelta(days=2)
        new_date = date.today() + timedelta(days=3)
        _insert_stale_near_term(db, "AAPL", old_date, timing="BMO")

        container = _make_container(db, finnhub_date=new_date, finnhub_timing="AMC")
        mock_yahoo = MagicMock()
        mock_yahoo.get_next_earnings_date.return_value = Result.Ok(
            (new_date, EarningsTiming.AMC)
        )

        with patch("scripts.scan.earnings_fetcher._get_yf_earnings", return_value=mock_yahoo):
            result = fetch_earnings_for_ticker(container, "AAPL")

        assert result == (new_date, EarningsTiming.AMC)
        row = _get_row(db, "AAPL")
        assert row[0] == new_date.isoformat()
        assert row[1] == "AMC"

    def test_finnhub_disagrees_keeps_old_date(self, tmp_path):
        """Yahoo proposes a new date; Finnhub (fresh) disagrees -> DB unchanged,
        the OLD date is returned for this run (never guess on a single source)."""
        db = _create_db(tmp_path)
        old_date = date.today() + timedelta(days=2)
        yahoo_date = date.today() + timedelta(days=3)
        finnhub_date = date.today() + timedelta(days=4)  # neither matches yahoo nor old
        _insert_stale_near_term(db, "MSFT", old_date, timing="BMO")

        container = _make_container(db, finnhub_date=finnhub_date, finnhub_timing="BMO")
        mock_yahoo = MagicMock()
        mock_yahoo.get_next_earnings_date.return_value = Result.Ok(
            (yahoo_date, EarningsTiming.AMC)
        )

        with patch("scripts.scan.earnings_fetcher._get_yf_earnings", return_value=mock_yahoo):
            result = fetch_earnings_for_ticker(container, "MSFT")

        assert result == (old_date, EarningsTiming.BMO)
        row = _get_row(db, "MSFT")
        assert row[0] == old_date.isoformat()

    def test_finnhub_call_fails_keeps_old_date(self, tmp_path):
        """Yahoo proposes a new date; Finnhub errors out -> can't corroborate,
        DB unchanged, old date returned."""
        db = _create_db(tmp_path)
        old_date = date.today() + timedelta(days=2)
        yahoo_date = date.today() + timedelta(days=3)
        _insert_stale_near_term(db, "GOOGL", old_date, timing="BMO")

        container = _make_container(db, finnhub_date=None)  # Finnhub errors
        mock_yahoo = MagicMock()
        mock_yahoo.get_next_earnings_date.return_value = Result.Ok(
            (yahoo_date, EarningsTiming.AMC)
        )

        with patch("scripts.scan.earnings_fetcher._get_yf_earnings", return_value=mock_yahoo):
            result = fetch_earnings_for_ticker(container, "GOOGL")

        assert result == (old_date, EarningsTiming.BMO)
        row = _get_row(db, "GOOGL")
        assert row[0] == old_date.isoformat()

    def test_yahoo_confirms_same_date_no_corroboration_needed(self, tmp_path):
        """Yahoo agrees with the existing DB date -> no change branch at all,
        just a validation-timestamp touch. Finnhub should not even be consulted."""
        db = _create_db(tmp_path)
        same_date = date.today() + timedelta(days=2)
        _insert_stale_near_term(db, "TSLA", same_date, timing="BMO")

        container = _make_container(db, finnhub_date=None)
        mock_yahoo = MagicMock()
        mock_yahoo.get_next_earnings_date.return_value = Result.Ok(
            (same_date, EarningsTiming.BMO)
        )

        with patch("scripts.scan.earnings_fetcher._get_yf_earnings", return_value=mock_yahoo):
            result = fetch_earnings_for_ticker(container, "TSLA")

        assert result == (same_date, EarningsTiming.BMO)
        container.finnhub.get_earnings_calendar.assert_not_called()

    def test_different_quarter_gap_still_skips_without_corroboration_call(self, tmp_path):
        """A >=45 day gap is treated as a different-quarter mismatch and
        already skips (pre-existing behavior) -- must not attempt corroboration
        or overwrite in that branch either."""
        db = _create_db(tmp_path)
        old_date = date.today() + timedelta(days=2)
        far_date = date.today() + timedelta(days=95)
        _insert_stale_near_term(db, "NFLX", old_date, timing="BMO")

        container = _make_container(db, finnhub_date=None)
        mock_yahoo = MagicMock()
        mock_yahoo.get_next_earnings_date.return_value = Result.Ok(
            (far_date, EarningsTiming.AMC)
        )

        with patch("scripts.scan.earnings_fetcher._get_yf_earnings", return_value=mock_yahoo):
            result = fetch_earnings_for_ticker(container, "NFLX")

        assert result is None
        container.finnhub.get_earnings_calendar.assert_not_called()
        row = _get_row(db, "NFLX")
        assert row[0] == old_date.isoformat()  # unchanged


class TestColdPathNearDuplicateOrdering:
    """Ticker not in DB at all -> Finnhub-only fallback. Verifies it picks
    the chronologically nearest entry rather than trusting API list order."""

    def test_picks_earliest_when_api_returns_later_entry_first(self, tmp_path):
        db = _create_db(tmp_path)  # empty -- ticker not in DB
        near_date = date.today() + timedelta(days=5)
        far_date = date.today() + timedelta(days=40)

        container = MagicMock()
        container.config.database.path = db
        # API returns the LATER date first, deliberately out of order
        container.finnhub.get_earnings_calendar.return_value = Result.Ok(
            [
                ("NEWCO", far_date, EarningsTiming.AMC),
                ("NEWCO", near_date, EarningsTiming.BMO),
            ]
        )

        result = fetch_earnings_for_ticker(container, "NEWCO")

        assert result == (near_date, EarningsTiming.BMO)
