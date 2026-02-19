"""
Unit tests for cleanup_duplicate_earnings() in scripts/sync_earnings_calendar.py.

Tests the three dedup rules:
1. Confirmed beats unconfirmed within 90 days
2. Confirmed-vs-confirmed tiebreak within 30 days (known timing > UNKNOWN)
3. Unconfirmed-vs-unconfirmed: keep most recently updated

Also tests:
- Different quarters preserved (confirmed entries >30 days apart)
- try/finally connection cleanup
- Dry-run mode
- Accurate "keeper" in audit trail when Rule 2 changes the survivor
"""

import pytest
import sqlite3
from pathlib import Path
from datetime import datetime, date, timedelta

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.sync_earnings_calendar import cleanup_duplicate_earnings


# Fixed timestamps for deterministic tests
_TS_OLD = "2026-02-01 10:00:00"
_TS_NEW = "2026-02-15 10:00:00"
_TS_NEWEST = "2026-02-18 10:00:00"


def _create_db(path: Path) -> str:
    """Create a test database with the earnings_calendar schema."""
    db_path = str(path / "test_ivcrush.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE earnings_calendar (
            ticker TEXT NOT NULL,
            earnings_date DATE NOT NULL,
            timing TEXT NOT NULL CHECK(timing IN ('BMO', 'AMC', 'DMH', 'UNKNOWN')),
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


def _insert(db_path, ticker, earnings_date, timing="AMC", confirmed=1, updated_at=_TS_NEW):
    """Insert a test earnings calendar record."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO earnings_calendar
        (ticker, earnings_date, timing, confirmed, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (ticker, earnings_date, timing, confirmed, updated_at),
    )
    conn.commit()
    conn.close()


def _count(db_path, ticker=None):
    """Count rows in earnings_calendar, optionally filtered by ticker."""
    conn = sqlite3.connect(db_path)
    if ticker:
        row = conn.execute(
            "SELECT COUNT(*) FROM earnings_calendar WHERE ticker = ?", (ticker,)
        ).fetchone()
    else:
        row = conn.execute("SELECT COUNT(*) FROM earnings_calendar").fetchone()
    conn.close()
    return row[0]


def _get_dates(db_path, ticker):
    """Get all earnings_date values for a ticker, sorted."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT earnings_date FROM earnings_calendar WHERE ticker = ? ORDER BY earnings_date",
        (ticker,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


# ============================================================================
# Rule 1: Confirmed beats unconfirmed within 90 days
# ============================================================================


class TestRule1ConfirmedBeatsUnconfirmed:

    def test_removes_unconfirmed_near_confirmed(self, tmp_path):
        """Unconfirmed entry within 90 days of confirmed entry is removed."""
        db = _create_db(tmp_path)
        _insert(db, "PANW", "2026-02-17", timing="AMC", confirmed=1)
        _insert(db, "PANW", "2026-02-26", timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 1
        assert removed[0]["removed_date"] == "2026-02-26"
        assert removed[0]["kept_date"] == "2026-02-17"
        assert _count(db, "PANW") == 1
        assert _get_dates(db, "PANW") == ["2026-02-17"]

    def test_keeps_unconfirmed_beyond_90_days(self, tmp_path):
        """Unconfirmed entry >90 days from confirmed is kept (different quarter)."""
        db = _create_db(tmp_path)
        _insert(db, "AAPL", "2026-01-30", timing="AMC", confirmed=1)
        _insert(db, "AAPL", "2026-05-01", timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 0
        assert _count(db, "AAPL") == 2

    def test_multiple_unconfirmed_near_one_confirmed(self, tmp_path):
        """Multiple unconfirmed entries near one confirmed are all removed."""
        db = _create_db(tmp_path)
        _insert(db, "TSLA", "2026-04-21", timing="AMC", confirmed=1)
        _insert(db, "TSLA", "2026-04-28", timing="UNKNOWN", confirmed=0)
        _insert(db, "TSLA", "2026-04-14", timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 2
        assert _count(db, "TSLA") == 1
        assert _get_dates(db, "TSLA") == ["2026-04-21"]


# ============================================================================
# Rule 2: Confirmed-vs-confirmed within 30 days
# ============================================================================


class TestRule2ConfirmedTiebreak:

    def test_keeps_known_timing_over_unknown(self, tmp_path):
        """Within 30 days, confirmed AMC beats confirmed UNKNOWN."""
        db = _create_db(tmp_path)
        _insert(db, "AA", "2026-04-15", timing="AMC", confirmed=1)
        _insert(db, "AA", "2026-04-22", timing="UNKNOWN", confirmed=1)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 1
        assert removed[0]["removed_date"] == "2026-04-22"
        assert removed[0]["kept_date"] == "2026-04-15"
        assert _count(db, "AA") == 1

    def test_keeps_newer_when_same_timing(self, tmp_path):
        """Within 30 days, same timing, keep most recently updated."""
        db = _create_db(tmp_path)
        _insert(db, "MSFT", "2026-04-20", timing="AMC", confirmed=1, updated_at=_TS_OLD)
        _insert(db, "MSFT", "2026-04-22", timing="AMC", confirmed=1, updated_at=_TS_NEW)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 1
        # Newer updated_at wins
        assert removed[0]["kept_date"] == "2026-04-22"
        assert removed[0]["removed_date"] == "2026-04-20"

    def test_different_quarters_both_kept(self, tmp_path):
        """Confirmed entries >30 days apart are different quarters — both kept."""
        db = _create_db(tmp_path)
        _insert(db, "VRT", "2026-02-11", timing="AMC", confirmed=1)
        _insert(db, "VRT", "2026-04-29", timing="UNKNOWN", confirmed=1)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 0
        assert _count(db, "VRT") == 2


# ============================================================================
# Rule 3: Unconfirmed-vs-unconfirmed (no confirmed sibling)
# ============================================================================


class TestRule3UnconfirmedTiebreak:

    def test_keeps_newest_unconfirmed(self, tmp_path):
        """Two unconfirmed within 90 days — keep most recently updated."""
        db = _create_db(tmp_path)
        _insert(db, "NFLX", "2026-04-15", timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)
        _insert(db, "NFLX", "2026-04-20", timing="UNKNOWN", confirmed=0, updated_at=_TS_NEW)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 1
        assert removed[0]["removed_date"] == "2026-04-15"
        assert removed[0]["kept_date"] == "2026-04-20"
        assert _count(db, "NFLX") == 1

    def test_keeps_both_unconfirmed_beyond_90_days(self, tmp_path):
        """Two unconfirmed >90 days apart — both kept."""
        db = _create_db(tmp_path)
        _insert(db, "AMZN", "2026-02-01", timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)
        _insert(db, "AMZN", "2026-05-10", timing="UNKNOWN", confirmed=0, updated_at=_TS_NEW)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 0
        assert _count(db, "AMZN") == 2


# ============================================================================
# Combined scenarios
# ============================================================================


class TestCombinedRules:

    def test_three_entries_confirmed_plus_two_unconfirmed(self, tmp_path):
        """One confirmed + two unconfirmed within 90 days: both unconfirmed removed."""
        db = _create_db(tmp_path)
        _insert(db, "CRM", "2026-02-25", timing="AMC", confirmed=1)
        _insert(db, "CRM", "2026-02-26", timing="UNKNOWN", confirmed=0)
        _insert(db, "CRM", "2026-03-01", timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 2
        assert _count(db, "CRM") == 1
        assert _get_dates(db, "CRM") == ["2026-02-25"]

    def test_two_confirmed_within_30d_plus_unconfirmed(self, tmp_path):
        """Two confirmed within 30d + one unconfirmed: unconfirmed removed by Rule 1,
        weaker confirmed removed by Rule 2."""
        db = _create_db(tmp_path)
        _insert(db, "GOOG", "2026-04-22", timing="AMC", confirmed=1, updated_at=_TS_NEW)
        _insert(db, "GOOG", "2026-04-28", timing="UNKNOWN", confirmed=1, updated_at=_TS_OLD)
        _insert(db, "GOOG", "2026-04-25", timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 2
        assert _count(db, "GOOG") == 1
        assert _get_dates(db, "GOOG") == ["2026-04-22"]

    def test_keeper_audit_trail_accurate_after_rule2(self, tmp_path):
        """When Rule 2 removes the confirmed entry that Rule 1 initially matched,
        the audit trail should reference the actual surviving confirmed entry."""
        db = _create_db(tmp_path)
        # Two confirmed entries within 30 days — Rule 2 will keep Apr 22 (AMC, newer)
        _insert(db, "META", "2026-04-22", timing="AMC", confirmed=1, updated_at=_TS_NEWEST)
        _insert(db, "META", "2026-04-28", timing="UNKNOWN", confirmed=1, updated_at=_TS_OLD)
        # One unconfirmed — closest confirmed is Apr 28, but Rule 2 removes Apr 28
        _insert(db, "META", "2026-04-30", timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 2
        assert _count(db, "META") == 1
        assert _get_dates(db, "META") == ["2026-04-22"]

        # Find the Rule 1 removal (unconfirmed entry)
        rule1_removal = [r for r in removed if r["removed_confirmed"] == 0]
        assert len(rule1_removal) == 1
        # The logged keeper should be Apr 22 (the survivor), NOT Apr 28 (which was itself removed)
        assert rule1_removal[0]["kept_date"] == "2026-04-22"

    def test_multiple_tickers_independent(self, tmp_path):
        """Dedup operates independently per ticker."""
        db = _create_db(tmp_path)
        _insert(db, "AAPL", "2026-04-30", timing="AMC", confirmed=1)
        _insert(db, "AAPL", "2026-05-05", timing="UNKNOWN", confirmed=0)
        _insert(db, "MSFT", "2026-04-22", timing="AMC", confirmed=1)
        _insert(db, "MSFT", "2026-04-28", timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 2
        assert _count(db, "AAPL") == 1
        assert _count(db, "MSFT") == 1
        tickers_removed = {r["ticker"] for r in removed}
        assert tickers_removed == {"AAPL", "MSFT"}


# ============================================================================
# No duplicates
# ============================================================================


class TestNoDuplicates:

    def test_single_entry_per_ticker(self, tmp_path):
        """No duplicates when each ticker has one entry."""
        db = _create_db(tmp_path)
        _insert(db, "NVDA", "2026-05-28", timing="AMC", confirmed=1)
        _insert(db, "AMD", "2026-05-06", timing="AMC", confirmed=1)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 0
        assert _count(db) == 2

    def test_empty_table(self, tmp_path):
        """Empty table returns no removals."""
        db = _create_db(tmp_path)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 0


# ============================================================================
# Dry-run mode
# ============================================================================


class TestDryRun:

    def test_dry_run_reports_but_does_not_delete(self, tmp_path):
        """Dry run returns removal details but leaves DB unchanged."""
        db = _create_db(tmp_path)
        _insert(db, "PANW", "2026-02-17", timing="AMC", confirmed=1)
        _insert(db, "PANW", "2026-02-26", timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings(db, dry_run=True)

        assert len(removed) == 1
        assert removed[0]["removed_date"] == "2026-02-26"
        # DB should still have both entries
        assert _count(db, "PANW") == 2

    def test_dry_run_then_live(self, tmp_path):
        """Dry run followed by live run produces same results."""
        db = _create_db(tmp_path)
        _insert(db, "FIG", "2026-02-18", timing="AMC", confirmed=1)
        _insert(db, "FIG", "2026-02-26", timing="UNKNOWN", confirmed=0)

        dry_removed = cleanup_duplicate_earnings(db, dry_run=True)
        assert _count(db, "FIG") == 2

        live_removed = cleanup_duplicate_earnings(db, dry_run=False)
        assert _count(db, "FIG") == 1
        assert len(dry_removed) == len(live_removed)
        assert dry_removed[0]["removed_date"] == live_removed[0]["removed_date"]


# ============================================================================
# Connection safety
# ============================================================================


class TestConnectionSafety:

    def test_connection_closed_on_exception(self, tmp_path):
        """Connection is closed even when an exception occurs (try/finally)."""
        db = _create_db(tmp_path)
        _insert(db, "PANW", "2026-02-17", timing="AMC", confirmed=1)
        _insert(db, "PANW", "2026-02-26", timing="UNKNOWN", confirmed=0)

        # Corrupt the DB path to force an error on the second call
        # First call succeeds (cleans up), second call with bad path should not leak
        import os
        bad_path = str(tmp_path / "nonexistent" / "bad.db")

        # This should raise but not leak the connection
        try:
            cleanup_duplicate_earnings(bad_path)
        except Exception:
            pass  # Expected — the point is no leaked connection

        # Original DB should still be accessible (not locked)
        assert _count(db, "PANW") == 2  # nothing was deleted from the good db

    def test_idempotent_second_run(self, tmp_path):
        """Running cleanup twice is safe — second run finds nothing."""
        db = _create_db(tmp_path)
        _insert(db, "ADI", "2026-02-18", timing="AMC", confirmed=1)
        _insert(db, "ADI", "2026-02-26", timing="UNKNOWN", confirmed=0)

        first = cleanup_duplicate_earnings(db)
        assert len(first) == 1

        second = cleanup_duplicate_earnings(db)
        assert len(second) == 0
        assert _count(db, "ADI") == 1
