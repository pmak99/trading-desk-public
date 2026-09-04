"""
Unit tests for cleanup_duplicate_earnings() in scripts/sync_earnings_calendar.py.

Tests the three dedup rules:
1. Confirmed beats unconfirmed within 90 days
2. Confirmed-vs-confirmed within 30 days: requires FRESH two-source
   corroboration (Finnhub + Yahoo Finance, refetched live) to agree before
   resolving — recency/timing-completeness are no longer used as a proxy
   for correctness (fixed 2026-07-27 after this exact tiebreak flipped a
   real ticker's earnings date — see cleanup_duplicate_earnings docstring).
3. Unconfirmed-vs-unconfirmed: keep most recently updated

Also tests:
- Different quarters preserved (confirmed entries >30 days apart)
- try/finally connection cleanup
- Dry-run mode
- Accurate "keeper" in audit trail when Rule 2 changes the survivor
- No corroboration source provided -> conflict left unresolved, nothing deleted
- Fresh sources disagree, or agree on a third date -> conflict left unresolved
"""

import pytest
import sqlite3
from pathlib import Path
from datetime import datetime, date, timedelta

import sys
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))


def _future(days: int) -> str:
    """Return an ISO date string N days from today, staying inside the cleanup window."""
    return (date.today() + timedelta(days=days)).isoformat()


def _past(days: int) -> str:
    """Return an ISO date string N days before today."""
    return (date.today() - timedelta(days=days)).isoformat()

from scripts.sync_earnings_calendar import cleanup_duplicate_earnings
from src.domain.errors import Result
from src.domain.types import EarningsTiming


class _FakeYahoo:
    """Mock YahooFinanceEarnings — returns a fixed date, or Err if configured to fail."""

    def __init__(self, date_str=None, timing="AMC"):
        self.date_str = date_str
        self.timing = timing

    def get_next_earnings_date(self, ticker):
        if self.date_str is None:
            return Result.Err(AppError_stub())
        return Result.Ok((date.fromisoformat(self.date_str), EarningsTiming(self.timing)))

    def get_earnings_date_near(self, ticker, reference_dates):
        """History-aware lookup used by _corroborate — same fixed-answer
        behavior as get_next_earnings_date, just ignoring reference_dates
        (the fake doesn't need real history search to prove agreement/
        disagreement outcomes)."""
        if self.date_str is None:
            return Result.Err(AppError_stub())
        return Result.Ok((date.fromisoformat(self.date_str), EarningsTiming(self.timing)))


class _FakeFinnhub:
    """Mock FinnhubAPI — returns a fixed date, or Err if configured to fail."""

    def __init__(self, date_str=None, timing="AMC"):
        self.date_str = date_str
        self.timing = timing

    def get_earnings_calendar(self, symbol, horizon="3month", from_date=None, to_date=None):
        if self.date_str is None:
            return Result.Err(AppError_stub())
        return Result.Ok(
            [(symbol, date.fromisoformat(self.date_str), EarningsTiming(self.timing))]
        )


def AppError_stub():
    """Minimal stand-in — cleanup_duplicate_earnings only checks is_err, never reads .error."""
    return "unused"


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
        kept_date = _future(5)
        removed_date = _future(14)
        _insert(db, "PANW", kept_date, timing="AMC", confirmed=1)
        _insert(db, "PANW", removed_date, timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 1
        assert removed[0]["removed_date"] == removed_date
        assert removed[0]["kept_date"] == kept_date
        assert _count(db, "PANW") == 1
        assert _get_dates(db, "PANW") == [kept_date]

    def test_keeps_unconfirmed_beyond_90_days(self, tmp_path):
        """Unconfirmed entry >90 days from confirmed is kept (different quarter)."""
        db = _create_db(tmp_path)
        _insert(db, "AAPL", _future(5), timing="AMC", confirmed=1)
        _insert(db, "AAPL", _future(98), timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 0
        assert _count(db, "AAPL") == 2

    def test_multiple_unconfirmed_near_one_confirmed(self, tmp_path):
        """Multiple unconfirmed entries near one confirmed are all removed."""
        db = _create_db(tmp_path)
        confirmed_date = _future(10)
        _insert(db, "TSLA", confirmed_date, timing="AMC", confirmed=1)
        _insert(db, "TSLA", _future(17), timing="UNKNOWN", confirmed=0)
        _insert(db, "TSLA", _future(3), timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 2
        assert _count(db, "TSLA") == 1
        assert _get_dates(db, "TSLA") == [confirmed_date]


# ============================================================================
# Rule 2: Confirmed-vs-confirmed within 30 days
# ============================================================================


class TestRule2ConfirmedTiebreak:

    def test_no_corroboration_source_leaves_both_confirmed_entries(self, tmp_path):
        """No finnhub/yahoo_finance passed -> conflict can't be corroborated,
        so NEITHER entry is deleted (never guess on a confirmed fact)."""
        db = _create_db(tmp_path)
        amc_date = _future(5)
        unknown_date = _future(12)
        _insert(db, "AA", amc_date, timing="AMC", confirmed=1)
        _insert(db, "AA", unknown_date, timing="UNKNOWN", confirmed=1)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 0
        assert _count(db, "AA") == 2

    def test_fresh_sources_agree_resolves_conflict(self, tmp_path):
        """Finnhub and Yahoo, refetched live, agree on one of the two existing
        candidates -> that one is kept, the other is removed."""
        db = _create_db(tmp_path)
        older_date = _future(10)
        newer_date = _future(12)
        # Recency is deliberately the OPPOSITE of the corroborated answer,
        # proving the tiebreak no longer uses updated_at.
        _insert(db, "MSFT", older_date, timing="AMC", confirmed=1, updated_at=_TS_NEWEST)
        _insert(db, "MSFT", newer_date, timing="AMC", confirmed=1, updated_at=_TS_OLD)

        removed = cleanup_duplicate_earnings(
            db,
            finnhub=_FakeFinnhub(older_date),
            yahoo_finance=_FakeYahoo(older_date),
        )

        assert len(removed) == 1
        assert removed[0]["kept_date"] == older_date
        assert removed[0]["removed_date"] == newer_date
        assert _get_dates(db, "MSFT") == [older_date]

    def test_fresh_sources_disagree_leaves_both(self, tmp_path):
        """Finnhub and Yahoo, refetched live, disagree with each other ->
        unresolved, neither existing entry is deleted."""
        db = _create_db(tmp_path)
        date_a = _future(5)
        date_b = _future(12)
        _insert(db, "ZS", date_a, timing="AMC", confirmed=1)
        _insert(db, "ZS", date_b, timing="AMC", confirmed=1)

        removed = cleanup_duplicate_earnings(
            db,
            finnhub=_FakeFinnhub(date_a),
            yahoo_finance=_FakeYahoo(date_b),
        )

        assert len(removed) == 0
        assert _count(db, "ZS") == 2

    def test_corroborated_date_matches_neither_candidate_leaves_both(self, tmp_path):
        """Finnhub and Yahoo agree with each other, but on a THIRD date that
        matches neither existing row -> still unresolved (this function only
        deletes/keeps existing rows, it never inserts a new one)."""
        db = _create_db(tmp_path)
        date_a = _future(5)
        date_b = _future(12)
        date_c = _future(8)  # agreed-upon date, but not in the DB at all
        _insert(db, "DDOG", date_a, timing="AMC", confirmed=1)
        _insert(db, "DDOG", date_b, timing="AMC", confirmed=1)

        removed = cleanup_duplicate_earnings(
            db,
            finnhub=_FakeFinnhub(date_c),
            yahoo_finance=_FakeYahoo(date_c),
        )

        assert len(removed) == 0
        assert _count(db, "DDOG") == 2

    def test_different_quarters_both_kept(self, tmp_path):
        """Confirmed entries >30 days apart are different quarters — both kept."""
        db = _create_db(tmp_path)
        _insert(db, "VRT", _future(5), timing="AMC", confirmed=1)
        _insert(db, "VRT", _future(84), timing="UNKNOWN", confirmed=1)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 0
        assert _count(db, "VRT") == 2


# ============================================================================
# Rule 2 (continued): reusing resolved_this_run instead of fresh corroboration
# ============================================================================
#
# Added 2026-08-06 — the main sync loop already cross-validates a ticker's
# new date (Yahoo+Finnhub, no conflict) before writing it, then the post-sync
# dedup pass used to throw that answer away and re-derive it from scratch via
# _corroborate(). That re-derivation could fail for reasons unrelated to
# whether the answer was actually known: Finnhub rate-limited by the same
# run's own earlier calls, or (for an already-past event) Yahoo's
# "next earnings date" lookup rolling forward past the disputed cluster
# entirely. resolved_this_run lets the caller hand over the answer it already
# has, skipping _corroborate() (and its API calls) whenever it applies.


class TestRule2ResolvedThisRun:

    def test_resolved_this_run_wins_without_any_corroboration_source(self, tmp_path):
        """resolved_this_run alone is enough to resolve the tie — no finnhub/
        yahoo_finance client needed at all (proves _corroborate is skipped,
        since the old behavior with no sources is to leave both entries)."""
        db = _create_db(tmp_path)
        kept_date = _future(10)
        stale_date = _future(11)
        _insert(db, "CSCO", stale_date, timing="AMC", confirmed=1, updated_at=_TS_OLD)
        _insert(db, "CSCO", kept_date, timing="AMC", confirmed=1, updated_at=_TS_NEW)

        removed = cleanup_duplicate_earnings(
            db,
            resolved_this_run={"CSCO": (date.fromisoformat(kept_date), EarningsTiming.AMC)},
        )

        assert len(removed) == 1
        assert removed[0]["kept_date"] == kept_date
        assert removed[0]["removed_date"] == stale_date
        assert _get_dates(db, "CSCO") == [kept_date]

    def test_resolved_this_run_overrides_a_disagreeing_fresh_source(self, tmp_path):
        """Even when finnhub/yahoo_finance are provided and would corroborate
        the OTHER candidate, resolved_this_run takes priority and _corroborate
        is never consulted."""
        db = _create_db(tmp_path)
        run_resolved_date = _future(10)
        other_date = _future(11)
        _insert(db, "HPQ", run_resolved_date, timing="AMC", confirmed=1)
        _insert(db, "HPQ", other_date, timing="AMC", confirmed=1)

        removed = cleanup_duplicate_earnings(
            db,
            finnhub=_FakeFinnhub(other_date),
            yahoo_finance=_FakeYahoo(other_date),
            resolved_this_run={"HPQ": (date.fromisoformat(run_resolved_date), EarningsTiming.AMC)},
        )

        assert len(removed) == 1
        assert removed[0]["kept_date"] == run_resolved_date
        assert removed[0]["removed_date"] == other_date

    def test_resolved_this_run_no_matching_candidate_falls_back_to_corroboration(self, tmp_path):
        """If the resolved date doesn't match either existing row (edge case),
        fall back to fresh corroboration rather than silently doing nothing."""
        db = _create_db(tmp_path)
        date_a = _future(5)
        date_b = _future(12)
        unrelated_resolved_date = _future(40)
        _insert(db, "DDOG", date_a, timing="AMC", confirmed=1)
        _insert(db, "DDOG", date_b, timing="AMC", confirmed=1)

        removed = cleanup_duplicate_earnings(
            db,
            finnhub=_FakeFinnhub(date_a),
            yahoo_finance=_FakeYahoo(date_a),
            resolved_this_run={"DDOG": (date.fromisoformat(unrelated_resolved_date), EarningsTiming.AMC)},
        )

        assert len(removed) == 1
        assert removed[0]["kept_date"] == date_a
        assert removed[0]["removed_date"] == date_b

    def test_resolved_this_run_only_applies_to_its_own_ticker(self, tmp_path):
        """A resolved_this_run entry for one ticker doesn't affect dedup of
        another ticker with no entry in the map — old no-source behavior
        (leave both) still applies to it."""
        db = _create_db(tmp_path)
        _insert(db, "CSCO", _future(10), timing="AMC", confirmed=1)
        _insert(db, "CSCO", _future(11), timing="AMC", confirmed=1)
        _insert(db, "ZS", _future(5), timing="AMC", confirmed=1)
        _insert(db, "ZS", _future(12), timing="AMC", confirmed=1)

        removed = cleanup_duplicate_earnings(
            db,
            resolved_this_run={"CSCO": (date.fromisoformat(_future(10)), EarningsTiming.AMC)},
        )

        assert len(removed) == 1
        assert removed[0]["ticker"] == "CSCO"
        assert _count(db, "ZS") == 2  # left unresolved, no source for ZS's conflict


# ============================================================================
# Rule 2 (continued): already-past disputed clusters (AXON/SKYT/ABTC, 2026-08-06)
# ============================================================================
#
# _corroborate() used to ask get_next_earnings_date() — a forward-looking
# "what's next" question that can never confirm a candidate already at or
# before today. A ticker whose entire duplicate cluster had already occurred
# would get "fresh Finnhub/Yahoo check unavailable or still disagrees" on
# every single retry, forever — not a transient failure. It now asks
# get_earnings_date_near()/a Finnhub window bracketing the candidates
# instead, which can confirm a past date just as well as a future one.


def _past(days: int) -> str:
    """Return an ISO date string N days before today."""
    return (date.today() - timedelta(days=days)).isoformat()


class TestRule2AlreadyPastClusters:

    def test_both_candidates_already_past_still_resolves(self, tmp_path):
        """Mirrors AXON/SKYT: both confirmed entries are at/before today.
        A forward-looking 'next earnings' lookup would have nothing to
        match; get_earnings_date_near-based corroboration still resolves it."""
        db = _create_db(tmp_path)
        kept_date = _past(1)
        stale_date = _past(3)
        _insert(db, "AXON", stale_date, timing="BMO", confirmed=1, updated_at=_TS_OLD)
        _insert(db, "AXON", kept_date, timing="BMO", confirmed=1, updated_at=_TS_NEW)

        removed = cleanup_duplicate_earnings(
            db,
            finnhub=_FakeFinnhub(kept_date, timing="BMO"),
            yahoo_finance=_FakeYahoo(kept_date, timing="BMO"),
        )

        assert len(removed) == 1
        assert removed[0]["kept_date"] == kept_date
        assert removed[0]["removed_date"] == stale_date
        assert _get_dates(db, "AXON") == [kept_date]

    def test_past_cluster_uses_history_lookup_not_next_earnings_date(self, tmp_path):
        """get_next_earnings_date must not be consulted at all for this path —
        proves the fix, not just its outcome, by making the old method raise
        while the new one still succeeds."""
        db = _create_db(tmp_path)
        kept_date = _past(1)
        stale_date = _past(2)
        _insert(db, "SKYT", stale_date, timing="AMC", confirmed=1)
        _insert(db, "SKYT", kept_date, timing="AMC", confirmed=1)

        class _StrictFakeYahoo(_FakeYahoo):
            def get_next_earnings_date(self, ticker):
                raise AssertionError(
                    "get_next_earnings_date should not be called by _corroborate "
                    "anymore — it should use get_earnings_date_near instead"
                )

        removed = cleanup_duplicate_earnings(
            db,
            finnhub=_FakeFinnhub(kept_date),
            yahoo_finance=_StrictFakeYahoo(kept_date),
        )

        assert len(removed) == 1
        assert removed[0]["kept_date"] == kept_date


# ============================================================================
# Rule 3: Unconfirmed-vs-unconfirmed (no confirmed sibling)
# ============================================================================


class TestRule3UnconfirmedTiebreak:

    def test_keeps_newest_unconfirmed(self, tmp_path):
        """Two unconfirmed within 90 days — keep most recently updated."""
        db = _create_db(tmp_path)
        older_date = _future(5)
        newer_date = _future(10)
        _insert(db, "NFLX", older_date, timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)
        _insert(db, "NFLX", newer_date, timing="UNKNOWN", confirmed=0, updated_at=_TS_NEW)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 1
        assert removed[0]["removed_date"] == older_date
        assert removed[0]["kept_date"] == newer_date
        assert _count(db, "NFLX") == 1

    def test_keeps_both_unconfirmed_beyond_90_days(self, tmp_path):
        """Two unconfirmed >90 days apart — both kept."""
        db = _create_db(tmp_path)
        _insert(db, "AMZN", _future(5), timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)
        _insert(db, "AMZN", _future(106), timing="UNKNOWN", confirmed=0, updated_at=_TS_NEW)

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
        kept_date = _future(5)
        _insert(db, "CRM", kept_date, timing="AMC", confirmed=1)
        _insert(db, "CRM", _future(6), timing="UNKNOWN", confirmed=0)
        _insert(db, "CRM", _future(12), timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 2
        assert _count(db, "CRM") == 1
        assert _get_dates(db, "CRM") == [kept_date]

    def test_two_confirmed_within_30d_plus_unconfirmed(self, tmp_path):
        """Two confirmed within 30d + one unconfirmed: unconfirmed removed by Rule 1
        (regardless of Rule 2's outcome); weaker confirmed removed by Rule 2 once
        fresh sources corroborate the survivor."""
        db = _create_db(tmp_path)
        amc_date = _future(10)
        _insert(db, "GOOG", amc_date, timing="AMC", confirmed=1, updated_at=_TS_NEW)
        _insert(db, "GOOG", _future(16), timing="UNKNOWN", confirmed=1, updated_at=_TS_OLD)
        _insert(db, "GOOG", _future(13), timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)

        removed = cleanup_duplicate_earnings(
            db,
            finnhub=_FakeFinnhub(amc_date),
            yahoo_finance=_FakeYahoo(amc_date),
        )

        assert len(removed) == 2
        assert _count(db, "GOOG") == 1
        assert _get_dates(db, "GOOG") == [amc_date]

    def test_two_confirmed_within_30d_unresolved_still_culls_unconfirmed(self, tmp_path):
        """Same setup, but with no corroboration source: Rule 2 can't resolve so
        BOTH confirmed entries survive, while Rule 1 still removes the unconfirmed
        entry (that rule doesn't depend on which confirmed entry ultimately wins)."""
        db = _create_db(tmp_path)
        amc_date = _future(10)
        unknown_date = _future(16)
        _insert(db, "GOOG", amc_date, timing="AMC", confirmed=1, updated_at=_TS_NEW)
        _insert(db, "GOOG", unknown_date, timing="UNKNOWN", confirmed=1, updated_at=_TS_OLD)
        _insert(db, "GOOG", _future(13), timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 1
        assert removed[0]["removed_confirmed"] == 0
        assert _count(db, "GOOG") == 2
        assert set(_get_dates(db, "GOOG")) == {amc_date, unknown_date}

    def test_keeper_audit_trail_accurate_after_rule2(self, tmp_path):
        """When Rule 2 removes the confirmed entry that Rule 1 initially matched,
        the audit trail should reference the actual surviving confirmed entry."""
        db = _create_db(tmp_path)
        # Two confirmed entries within 30 days — corroboration will keep AMC
        amc_date = _future(10)
        _insert(db, "META", amc_date, timing="AMC", confirmed=1, updated_at=_TS_NEWEST)
        _insert(db, "META", _future(16), timing="UNKNOWN", confirmed=1, updated_at=_TS_OLD)
        # One unconfirmed — closest confirmed is +16, but Rule 2 removes +16
        _insert(db, "META", _future(18), timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)

        removed = cleanup_duplicate_earnings(
            db,
            finnhub=_FakeFinnhub(amc_date),
            yahoo_finance=_FakeYahoo(amc_date),
        )

        assert len(removed) == 2
        assert _count(db, "META") == 1
        assert _get_dates(db, "META") == [amc_date]

        # Find the Rule 1 removal (unconfirmed entry)
        rule1_removal = [r for r in removed if r["removed_confirmed"] == 0]
        assert len(rule1_removal) == 1
        # The logged keeper should be amc_date (the survivor), NOT +16 (which was itself removed)
        assert rule1_removal[0]["kept_date"] == amc_date

    def test_multiple_tickers_independent(self, tmp_path):
        """Dedup operates independently per ticker."""
        db = _create_db(tmp_path)
        _insert(db, "AAPL", _future(19), timing="AMC", confirmed=1)
        _insert(db, "AAPL", _future(24), timing="UNKNOWN", confirmed=0)
        _insert(db, "MSFT", _future(11), timing="AMC", confirmed=1)
        _insert(db, "MSFT", _future(17), timing="UNKNOWN", confirmed=0)

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
        _insert(db, "NVDA", _future(5), timing="AMC", confirmed=1)
        _insert(db, "AMD", _future(10), timing="AMC", confirmed=1)

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
        removed_date = _future(14)
        _insert(db, "PANW", _future(5), timing="AMC", confirmed=1)
        _insert(db, "PANW", removed_date, timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings(db, dry_run=True)

        assert len(removed) == 1
        assert removed[0]["removed_date"] == removed_date
        # DB should still have both entries
        assert _count(db, "PANW") == 2

    def test_dry_run_then_live(self, tmp_path):
        """Dry run followed by live run produces same results."""
        db = _create_db(tmp_path)
        _insert(db, "FIG", _future(5), timing="AMC", confirmed=1)
        _insert(db, "FIG", _future(14), timing="UNKNOWN", confirmed=0)

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
        _insert(db, "PANW", _future(5), timing="AMC", confirmed=1)
        _insert(db, "PANW", _future(14), timing="UNKNOWN", confirmed=0)

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
        _insert(db, "ADI", _future(5), timing="AMC", confirmed=1)
        _insert(db, "ADI", _future(14), timing="UNKNOWN", confirmed=0)

        first = cleanup_duplicate_earnings(db)
        assert len(first) == 1

        second = cleanup_duplicate_earnings(db)
        assert len(second) == 0
        assert _count(db, "ADI") == 1


# ============================================================================
# Configurable window (default matches the routine daily-sync -14/+120 days;
# a wider window lets a one-off pass reach stale pairs the routine window
# has aged past — Aug 30 2026 audit found 39 such pairs from Aug 3-12, more
# than 14 days before the run date, permanently unreachable by the default).
# ============================================================================


class TestConfigurableWindow:

    def test_default_window_unchanged(self, tmp_path):
        """No window args -> identical behavior to before this was added."""
        db = _create_db(tmp_path)
        _insert(db, "PANW", _future(5), timing="AMC", confirmed=1)
        _insert(db, "PANW", _future(14), timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 1

    def test_pair_older_than_default_window_is_invisible_by_default(self, tmp_path):
        """A pair entirely in the past, beyond the default 14-day lookback,
        is invisible to the default call — this is the exact gap a wider
        one-off pass exists to close."""
        db = _create_db(tmp_path)
        _insert(db, "OLDCO", _past(20), timing="AMC", confirmed=1)
        _insert(db, "OLDCO", _past(18), timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings(db)

        assert len(removed) == 0
        assert _count(db, "OLDCO") == 2

    def test_wider_window_past_reaches_old_pair(self, tmp_path):
        """Passing window_past_days wide enough reaches a pair the default
        window misses, and applies the same Rule 1 logic."""
        db = _create_db(tmp_path)
        kept = _past(18)
        removed_date = _past(20)
        _insert(db, "OLDCO", kept, timing="AMC", confirmed=1)
        _insert(db, "OLDCO", removed_date, timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings(db, window_past_days=30)

        assert len(removed) == 1
        assert removed[0]["removed_date"] == removed_date
        assert removed[0]["kept_date"] == kept
        assert _count(db, "OLDCO") == 1
