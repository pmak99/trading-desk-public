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

from datetime import date, timedelta
from pathlib import Path

import pytest

from common.db import connect
from scripts.sync_earnings_calendar import cleanup_duplicate_earnings
from src.domain.errors import Err, Ok
from src.domain.types import EarningsTiming

pytestmark = pytest.mark.pg


def _future(days: int) -> str:
    """Return an ISO date string N days from today, staying inside the cleanup window."""
    return (date.today() + timedelta(days=days)).isoformat()


def _past(days: int) -> str:
    """Return an ISO date string N days before today."""
    return (date.today() - timedelta(days=days)).isoformat()


class _FakeYahoo:
    """Mock YahooFinanceEarnings — returns a fixed date, or Err if configured to fail."""

    def __init__(self, date_str=None, timing="AMC"):
        self.date_str = date_str
        self.timing = timing

    def get_next_earnings_date(self, ticker):
        if self.date_str is None:
            return Err(AppError_stub())
        return Ok((date.fromisoformat(self.date_str), EarningsTiming(self.timing)))

    def get_earnings_date_near(self, ticker, reference_dates):
        """History-aware lookup used by _corroborate — same fixed-answer
        behavior as get_next_earnings_date, just ignoring reference_dates
        (the fake doesn't need real history search to prove agreement/
        disagreement outcomes)."""
        if self.date_str is None:
            return Err(AppError_stub())
        return Ok((date.fromisoformat(self.date_str), EarningsTiming(self.timing)))


class _FakeFinnhub:
    """Mock FinnhubAPI — returns a fixed date, or Err if configured to fail."""

    def __init__(self, date_str=None, timing="AMC"):
        self.date_str = date_str
        self.timing = timing

    def get_earnings_calendar(self, symbol, horizon="3month", from_date=None, to_date=None):
        if self.date_str is None:
            return Err(AppError_stub())
        return Ok(
            [(symbol, date.fromisoformat(self.date_str), EarningsTiming(self.timing))]
        )


def AppError_stub():
    """Minimal stand-in — cleanup_duplicate_earnings only checks is_err, never reads .error."""
    return "unused"


# Fixed timestamps for deterministic tests
_TS_OLD = "2026-02-01 10:00:00"
_TS_NEW = "2026-02-15 10:00:00"
_TS_NEWEST = "2026-02-18 10:00:00"


def _create_db(schema) -> str:
    """earnings_calendar in the test's throwaway schema.

    `pg_schema` sets PGOPTIONS, so the script under test connects here too.
    Column types match baseline_schema.sql: earnings_date is a real date and
    updated_at a real timestamptz, which is why the helpers below convert on
    the way out rather than comparing ISO strings.
    """
    with connect() as conn:
        conn.execute("""
            CREATE TABLE earnings_calendar (
                ticker text NOT NULL,
                earnings_date date NOT NULL,
                timing text NOT NULL CHECK(timing IN ('BMO', 'AMC', 'DMH', 'UNKNOWN')),
                confirmed boolean DEFAULT false,
                created_at timestamptz DEFAULT now(),
                updated_at timestamptz DEFAULT now(),
                last_validated_at timestamptz,
                PRIMARY KEY (ticker, earnings_date)
            )
        """)
        conn.commit()
    return schema


def _insert(schema, ticker, earnings_date, timing="AMC", confirmed=1, updated_at=_TS_NEW):
    """Insert a test earnings calendar record."""
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO earnings_calendar
            (ticker, earnings_date, timing, confirmed, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (ticker, earnings_date, timing, bool(confirmed), updated_at),
        )
        conn.commit()


def _count(schema, ticker=None):
    """Count rows in earnings_calendar, optionally filtered by ticker."""
    with connect() as conn:
        if ticker:
            row = conn.execute(
                "SELECT COUNT(*) FROM earnings_calendar WHERE ticker = %s", (ticker,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) FROM earnings_calendar").fetchone()
    return row[0]


def _get_dates(schema, ticker):
    """Get all earnings_date values for a ticker, sorted."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT earnings_date FROM earnings_calendar WHERE ticker = %s"
            " ORDER BY earnings_date",
            (ticker,),
        ).fetchall()
    return [r[0].isoformat() for r in rows]


# ============================================================================
# Rule 1: Confirmed beats unconfirmed within 90 days
# ============================================================================


class TestRule1ConfirmedBeatsUnconfirmed:

    def test_removes_unconfirmed_near_confirmed(self, pg_schema):
        """Unconfirmed entry within 90 days of confirmed entry is removed."""
        db = _create_db(pg_schema)
        kept_date = _future(5)
        removed_date = _future(14)
        _insert(db, "PANW", kept_date, timing="AMC", confirmed=1)
        _insert(db, "PANW", removed_date, timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings()

        assert len(removed) == 1
        assert removed[0]["removed_date"] == removed_date
        assert removed[0]["kept_date"] == kept_date
        assert _count(db, "PANW") == 1
        assert _get_dates(db, "PANW") == [kept_date]

    def test_keeps_unconfirmed_beyond_90_days(self, pg_schema):
        """Unconfirmed entry >90 days from confirmed is kept (different quarter)."""
        db = _create_db(pg_schema)
        _insert(db, "AAPL", _future(5), timing="AMC", confirmed=1)
        _insert(db, "AAPL", _future(98), timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings()

        assert len(removed) == 0
        assert _count(db, "AAPL") == 2

    def test_multiple_unconfirmed_near_one_confirmed(self, pg_schema):
        """Multiple unconfirmed entries near one confirmed are all removed."""
        db = _create_db(pg_schema)
        confirmed_date = _future(10)
        _insert(db, "TSLA", confirmed_date, timing="AMC", confirmed=1)
        _insert(db, "TSLA", _future(17), timing="UNKNOWN", confirmed=0)
        _insert(db, "TSLA", _future(3), timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)

        removed = cleanup_duplicate_earnings()

        assert len(removed) == 2
        assert _count(db, "TSLA") == 1
        assert _get_dates(db, "TSLA") == [confirmed_date]


# ============================================================================
# Rule 2: Confirmed-vs-confirmed within 30 days
# ============================================================================


class TestRule2ConfirmedTiebreak:

    def test_no_corroboration_source_leaves_both_confirmed_entries(self, pg_schema):
        """No finnhub/yahoo_finance passed -> conflict can't be corroborated,
        so NEITHER entry is deleted (never guess on a confirmed fact)."""
        db = _create_db(pg_schema)
        amc_date = _future(5)
        unknown_date = _future(12)
        _insert(db, "AA", amc_date, timing="AMC", confirmed=1)
        _insert(db, "AA", unknown_date, timing="UNKNOWN", confirmed=1)

        removed = cleanup_duplicate_earnings()

        assert len(removed) == 0
        assert _count(db, "AA") == 2

    def test_fresh_sources_agree_resolves_conflict(self, pg_schema):
        """Finnhub and Yahoo, refetched live, agree on one of the two existing
        candidates -> that one is kept, the other is removed."""
        db = _create_db(pg_schema)
        older_date = _future(10)
        newer_date = _future(12)
        # Recency is deliberately the OPPOSITE of the corroborated answer,
        # proving the tiebreak no longer uses updated_at.
        _insert(db, "MSFT", older_date, timing="AMC", confirmed=1, updated_at=_TS_NEWEST)
        _insert(db, "MSFT", newer_date, timing="AMC", confirmed=1, updated_at=_TS_OLD)

        removed = cleanup_duplicate_earnings(
            finnhub=_FakeFinnhub(older_date),
            yahoo_finance=_FakeYahoo(older_date),
        )

        assert len(removed) == 1
        assert removed[0]["kept_date"] == older_date
        assert removed[0]["removed_date"] == newer_date
        assert _get_dates(db, "MSFT") == [older_date]

    def test_fresh_sources_disagree_leaves_both(self, pg_schema):
        """Finnhub and Yahoo, refetched live, disagree with each other ->
        unresolved, neither existing entry is deleted."""
        db = _create_db(pg_schema)
        date_a = _future(5)
        date_b = _future(12)
        _insert(db, "ZS", date_a, timing="AMC", confirmed=1)
        _insert(db, "ZS", date_b, timing="AMC", confirmed=1)

        removed = cleanup_duplicate_earnings(
            finnhub=_FakeFinnhub(date_a),
            yahoo_finance=_FakeYahoo(date_b),
        )

        assert len(removed) == 0
        assert _count(db, "ZS") == 2

    def test_corroborated_date_matches_neither_candidate_leaves_both(self, pg_schema):
        """Finnhub and Yahoo agree with each other, but on a THIRD date that
        matches neither existing row -> still unresolved (this function only
        deletes/keeps existing rows, it never inserts a new one)."""
        db = _create_db(pg_schema)
        date_a = _future(5)
        date_b = _future(12)
        date_c = _future(8)  # agreed-upon date, but not in the DB at all
        _insert(db, "DDOG", date_a, timing="AMC", confirmed=1)
        _insert(db, "DDOG", date_b, timing="AMC", confirmed=1)

        removed = cleanup_duplicate_earnings(
            finnhub=_FakeFinnhub(date_c),
            yahoo_finance=_FakeYahoo(date_c),
        )

        assert len(removed) == 0
        assert _count(db, "DDOG") == 2

    def test_different_quarters_both_kept(self, pg_schema):
        """Confirmed entries >30 days apart are different quarters — both kept."""
        db = _create_db(pg_schema)
        _insert(db, "VRT", _future(5), timing="AMC", confirmed=1)
        _insert(db, "VRT", _future(84), timing="UNKNOWN", confirmed=1)

        removed = cleanup_duplicate_earnings()

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

    def test_resolved_this_run_wins_without_any_corroboration_source(self, pg_schema):
        """resolved_this_run alone is enough to resolve the tie — no finnhub/
        yahoo_finance client needed at all (proves _corroborate is skipped,
        since the old behavior with no sources is to leave both entries)."""
        db = _create_db(pg_schema)
        kept_date = _future(10)
        stale_date = _future(11)
        _insert(db, "CSCO", stale_date, timing="AMC", confirmed=1, updated_at=_TS_OLD)
        _insert(db, "CSCO", kept_date, timing="AMC", confirmed=1, updated_at=_TS_NEW)

        removed = cleanup_duplicate_earnings(
            resolved_this_run={"CSCO": (date.fromisoformat(kept_date), EarningsTiming.AMC)},
        )

        assert len(removed) == 1
        assert removed[0]["kept_date"] == kept_date
        assert removed[0]["removed_date"] == stale_date
        assert _get_dates(db, "CSCO") == [kept_date]

    def test_resolved_this_run_overrides_a_disagreeing_fresh_source(self, pg_schema):
        """Even when finnhub/yahoo_finance are provided and would corroborate
        the OTHER candidate, resolved_this_run takes priority and _corroborate
        is never consulted."""
        db = _create_db(pg_schema)
        run_resolved_date = _future(10)
        other_date = _future(11)
        _insert(db, "HPQ", run_resolved_date, timing="AMC", confirmed=1)
        _insert(db, "HPQ", other_date, timing="AMC", confirmed=1)

        removed = cleanup_duplicate_earnings(
            finnhub=_FakeFinnhub(other_date),
            yahoo_finance=_FakeYahoo(other_date),
            resolved_this_run={"HPQ": (date.fromisoformat(run_resolved_date), EarningsTiming.AMC)},
        )

        assert len(removed) == 1
        assert removed[0]["kept_date"] == run_resolved_date
        assert removed[0]["removed_date"] == other_date

    def test_resolved_this_run_no_matching_candidate_falls_back_to_corroboration(self, pg_schema):
        """If the resolved date doesn't match either existing row (edge case),
        fall back to fresh corroboration rather than silently doing nothing."""
        db = _create_db(pg_schema)
        date_a = _future(5)
        date_b = _future(12)
        unrelated_resolved_date = _future(40)
        _insert(db, "DDOG", date_a, timing="AMC", confirmed=1)
        _insert(db, "DDOG", date_b, timing="AMC", confirmed=1)

        removed = cleanup_duplicate_earnings(
            finnhub=_FakeFinnhub(date_a),
            yahoo_finance=_FakeYahoo(date_a),
            resolved_this_run={"DDOG": (date.fromisoformat(unrelated_resolved_date), EarningsTiming.AMC)},
        )

        assert len(removed) == 1
        assert removed[0]["kept_date"] == date_a
        assert removed[0]["removed_date"] == date_b

    def test_resolved_this_run_only_applies_to_its_own_ticker(self, pg_schema):
        """A resolved_this_run entry for one ticker doesn't affect dedup of
        another ticker with no entry in the map — old no-source behavior
        (leave both) still applies to it."""
        db = _create_db(pg_schema)
        _insert(db, "CSCO", _future(10), timing="AMC", confirmed=1)
        _insert(db, "CSCO", _future(11), timing="AMC", confirmed=1)
        _insert(db, "ZS", _future(5), timing="AMC", confirmed=1)
        _insert(db, "ZS", _future(12), timing="AMC", confirmed=1)

        removed = cleanup_duplicate_earnings(
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

    def test_both_candidates_already_past_still_resolves(self, pg_schema):
        """Mirrors AXON/SKYT: both confirmed entries are at/before today.
        A forward-looking 'next earnings' lookup would have nothing to
        match; get_earnings_date_near-based corroboration still resolves it."""
        db = _create_db(pg_schema)
        kept_date = _past(1)
        stale_date = _past(3)
        _insert(db, "AXON", stale_date, timing="BMO", confirmed=1, updated_at=_TS_OLD)
        _insert(db, "AXON", kept_date, timing="BMO", confirmed=1, updated_at=_TS_NEW)

        removed = cleanup_duplicate_earnings(
            finnhub=_FakeFinnhub(kept_date, timing="BMO"),
            yahoo_finance=_FakeYahoo(kept_date, timing="BMO"),
        )

        assert len(removed) == 1
        assert removed[0]["kept_date"] == kept_date
        assert removed[0]["removed_date"] == stale_date
        assert _get_dates(db, "AXON") == [kept_date]

    def test_past_cluster_uses_history_lookup_not_next_earnings_date(self, pg_schema):
        """get_next_earnings_date must not be consulted at all for this path —
        proves the fix, not just its outcome, by making the old method raise
        while the new one still succeeds."""
        db = _create_db(pg_schema)
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
            finnhub=_FakeFinnhub(kept_date),
            yahoo_finance=_StrictFakeYahoo(kept_date),
        )

        assert len(removed) == 1
        assert removed[0]["kept_date"] == kept_date


# ============================================================================
# Rule 3: Unconfirmed-vs-unconfirmed (no confirmed sibling)
# ============================================================================


class TestRule3UnconfirmedTiebreak:

    def test_keeps_newest_unconfirmed(self, pg_schema):
        """Two unconfirmed within 90 days — keep most recently updated."""
        db = _create_db(pg_schema)
        older_date = _future(5)
        newer_date = _future(10)
        _insert(db, "NFLX", older_date, timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)
        _insert(db, "NFLX", newer_date, timing="UNKNOWN", confirmed=0, updated_at=_TS_NEW)

        removed = cleanup_duplicate_earnings()

        assert len(removed) == 1
        assert removed[0]["removed_date"] == older_date
        assert removed[0]["kept_date"] == newer_date
        assert _count(db, "NFLX") == 1

    def test_keeps_both_unconfirmed_beyond_90_days(self, pg_schema):
        """Two unconfirmed >90 days apart — both kept."""
        db = _create_db(pg_schema)
        _insert(db, "AMZN", _future(5), timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)
        _insert(db, "AMZN", _future(106), timing="UNKNOWN", confirmed=0, updated_at=_TS_NEW)

        removed = cleanup_duplicate_earnings()

        assert len(removed) == 0
        assert _count(db, "AMZN") == 2


# ============================================================================
# Combined scenarios
# ============================================================================


class TestCombinedRules:

    def test_three_entries_confirmed_plus_two_unconfirmed(self, pg_schema):
        """One confirmed + two unconfirmed within 90 days: both unconfirmed removed."""
        db = _create_db(pg_schema)
        kept_date = _future(5)
        _insert(db, "CRM", kept_date, timing="AMC", confirmed=1)
        _insert(db, "CRM", _future(6), timing="UNKNOWN", confirmed=0)
        _insert(db, "CRM", _future(12), timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)

        removed = cleanup_duplicate_earnings()

        assert len(removed) == 2
        assert _count(db, "CRM") == 1
        assert _get_dates(db, "CRM") == [kept_date]

    def test_two_confirmed_within_30d_plus_unconfirmed(self, pg_schema):
        """Two confirmed within 30d + one unconfirmed: unconfirmed removed by Rule 1
        (regardless of Rule 2's outcome); weaker confirmed removed by Rule 2 once
        fresh sources corroborate the survivor."""
        db = _create_db(pg_schema)
        amc_date = _future(10)
        _insert(db, "GOOG", amc_date, timing="AMC", confirmed=1, updated_at=_TS_NEW)
        _insert(db, "GOOG", _future(16), timing="UNKNOWN", confirmed=1, updated_at=_TS_OLD)
        _insert(db, "GOOG", _future(13), timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)

        removed = cleanup_duplicate_earnings(
            finnhub=_FakeFinnhub(amc_date),
            yahoo_finance=_FakeYahoo(amc_date),
        )

        assert len(removed) == 2
        assert _count(db, "GOOG") == 1
        assert _get_dates(db, "GOOG") == [amc_date]

    def test_two_confirmed_within_30d_unresolved_still_culls_unconfirmed(self, pg_schema):
        """Same setup, but with no corroboration source: Rule 2 can't resolve so
        BOTH confirmed entries survive, while Rule 1 still removes the unconfirmed
        entry (that rule doesn't depend on which confirmed entry ultimately wins)."""
        db = _create_db(pg_schema)
        amc_date = _future(10)
        unknown_date = _future(16)
        _insert(db, "GOOG", amc_date, timing="AMC", confirmed=1, updated_at=_TS_NEW)
        _insert(db, "GOOG", unknown_date, timing="UNKNOWN", confirmed=1, updated_at=_TS_OLD)
        _insert(db, "GOOG", _future(13), timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)

        removed = cleanup_duplicate_earnings()

        assert len(removed) == 1
        assert removed[0]["removed_confirmed"] == 0
        assert _count(db, "GOOG") == 2
        assert set(_get_dates(db, "GOOG")) == {amc_date, unknown_date}

    def test_keeper_audit_trail_accurate_after_rule2(self, pg_schema):
        """When Rule 2 removes the confirmed entry that Rule 1 initially matched,
        the audit trail should reference the actual surviving confirmed entry."""
        db = _create_db(pg_schema)
        # Two confirmed entries within 30 days — corroboration will keep AMC
        amc_date = _future(10)
        _insert(db, "META", amc_date, timing="AMC", confirmed=1, updated_at=_TS_NEWEST)
        _insert(db, "META", _future(16), timing="UNKNOWN", confirmed=1, updated_at=_TS_OLD)
        # One unconfirmed — closest confirmed is +16, but Rule 2 removes +16
        _insert(db, "META", _future(18), timing="UNKNOWN", confirmed=0, updated_at=_TS_OLD)

        removed = cleanup_duplicate_earnings(
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

    def test_multiple_tickers_independent(self, pg_schema):
        """Dedup operates independently per ticker."""
        db = _create_db(pg_schema)
        _insert(db, "AAPL", _future(19), timing="AMC", confirmed=1)
        _insert(db, "AAPL", _future(24), timing="UNKNOWN", confirmed=0)
        _insert(db, "MSFT", _future(11), timing="AMC", confirmed=1)
        _insert(db, "MSFT", _future(17), timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings()

        assert len(removed) == 2
        assert _count(db, "AAPL") == 1
        assert _count(db, "MSFT") == 1
        tickers_removed = {r["ticker"] for r in removed}
        assert tickers_removed == {"AAPL", "MSFT"}


# ============================================================================
# No duplicates
# ============================================================================


class TestNoDuplicates:

    def test_single_entry_per_ticker(self, pg_schema):
        """No duplicates when each ticker has one entry."""
        db = _create_db(pg_schema)
        _insert(db, "NVDA", _future(5), timing="AMC", confirmed=1)
        _insert(db, "AMD", _future(10), timing="AMC", confirmed=1)

        removed = cleanup_duplicate_earnings()

        assert len(removed) == 0
        assert _count(db) == 2

    def test_empty_table(self, pg_schema):
        """Empty table returns no removals."""
        _create_db(pg_schema)

        removed = cleanup_duplicate_earnings()

        assert len(removed) == 0


# ============================================================================
# Dry-run mode
# ============================================================================


class TestDryRun:

    def test_dry_run_reports_but_does_not_delete(self, pg_schema):
        """Dry run returns removal details but leaves DB unchanged."""
        db = _create_db(pg_schema)
        removed_date = _future(14)
        _insert(db, "PANW", _future(5), timing="AMC", confirmed=1)
        _insert(db, "PANW", removed_date, timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings(dry_run=True)

        assert len(removed) == 1
        assert removed[0]["removed_date"] == removed_date
        # DB should still have both entries
        assert _count(db, "PANW") == 2

    def test_dry_run_then_live(self, pg_schema):
        """Dry run followed by live run produces same results."""
        db = _create_db(pg_schema)
        _insert(db, "FIG", _future(5), timing="AMC", confirmed=1)
        _insert(db, "FIG", _future(14), timing="UNKNOWN", confirmed=0)

        dry_removed = cleanup_duplicate_earnings(dry_run=True)
        assert _count(db, "FIG") == 2

        live_removed = cleanup_duplicate_earnings(dry_run=False)
        assert _count(db, "FIG") == 1
        assert len(dry_removed) == len(live_removed)
        assert dry_removed[0]["removed_date"] == live_removed[0]["removed_date"]


# ============================================================================
# Connection safety
# ============================================================================


class TestConnectionSafety:

    # test_connection_closed_on_exception was deleted, not ported. It forced
    # an error by passing a corrupt DB *path*, and there is no path any more --
    # the target is PG* in the environment. Connection cleanup is now the
    # `with connect()` context manager rather than a hand-written try/finally,
    # so the leak it guarded against has no way to occur.

    def test_idempotent_second_run(self, pg_schema):
        """Running cleanup twice is safe — second run finds nothing."""
        db = _create_db(pg_schema)
        _insert(db, "ADI", _future(5), timing="AMC", confirmed=1)
        _insert(db, "ADI", _future(14), timing="UNKNOWN", confirmed=0)

        first = cleanup_duplicate_earnings()
        assert len(first) == 1

        second = cleanup_duplicate_earnings()
        assert len(second) == 0
        assert _count(db, "ADI") == 1


# ============================================================================
# Configurable window (default matches the routine daily-sync -14/+120 days;
# a wider window lets a one-off pass reach stale pairs the routine window
# has aged past — Aug 30 2026 audit found 39 such pairs from Aug 3-12, more
# than 14 days before the run date, permanently unreachable by the default).
# ============================================================================


class TestConfigurableWindow:

    def test_default_window_unchanged(self, pg_schema):
        """No window args -> identical behavior to before this was added."""
        db = _create_db(pg_schema)
        _insert(db, "PANW", _future(5), timing="AMC", confirmed=1)
        _insert(db, "PANW", _future(14), timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings()

        assert len(removed) == 1

    def test_pair_older_than_default_window_is_invisible_by_default(self, pg_schema):
        """A pair entirely in the past, beyond the default 14-day lookback,
        is invisible to the default call — this is the exact gap a wider
        one-off pass exists to close."""
        db = _create_db(pg_schema)
        _insert(db, "OLDCO", _past(20), timing="AMC", confirmed=1)
        _insert(db, "OLDCO", _past(18), timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings()

        assert len(removed) == 0
        assert _count(db, "OLDCO") == 2

    def test_wider_window_past_reaches_old_pair(self, pg_schema):
        """Passing window_past_days wide enough reaches a pair the default
        window misses, and applies the same Rule 1 logic."""
        db = _create_db(pg_schema)
        kept = _past(18)
        removed_date = _past(20)
        _insert(db, "OLDCO", kept, timing="AMC", confirmed=1)
        _insert(db, "OLDCO", removed_date, timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings(window_past_days=30)

        assert len(removed) == 1
        assert removed[0]["removed_date"] == removed_date
        assert removed[0]["kept_date"] == kept
        assert _count(db, "OLDCO") == 1


# ============================================================================
# Historical backlog: the scan window, not the rules, is what leaves old
# duplicate pairs in the database forever.
#
# Root-caused 2026-09-08. A routine sync only scans today-14d .. today+120d,
# so once both members of a duplicate pair are more than 14 days old they stop
# being examined and the pair becomes permanent. This is silent: the default
# call reports "no duplicates" while the backlog keeps growing every quarter.
#
# Found live at 507 stale clusters across 448 tickers, 505 of them entirely in
# the past. The downstream damage is in historical_moves, which gets a row per
# calendar row: the spurious rows land on ordinary non-earnings days, carry
# near-zero moves, and so dilute the mean historical move that is the VRP
# denominator — inflating VRP and pushing tickers into better tiers than they
# deserve.
# ============================================================================


class TestHistoricalBacklogWindow:

    def test_historical_pair_is_invisible_to_default_window(self, pg_schema):
        """A pair entirely older than the default 14-day lookback is not examined.

        Regression guard for the trap itself: this asserts the *limitation*, so
        that if the default window is ever widened this test fails loudly and
        forces a deliberate decision rather than a silent behaviour change.
        """
        db = _create_db(pg_schema)
        _insert(db, "STALE", _past(200), timing="AMC", confirmed=1)
        _insert(db, "STALE", _past(207), timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings()

        assert removed == []
        assert _count(db, "STALE") == 2

    def test_historical_pair_is_resolved_when_window_is_widened(self, pg_schema):
        """The same pair resolves under rule 1 once the scan window reaches it.

        No corroboration source is needed: confirmed beats unconfirmed.
        """
        db = _create_db(pg_schema)
        kept = _past(200)
        stale = _past(207)
        _insert(db, "STALE", kept, timing="AMC", confirmed=1)
        _insert(db, "STALE", stale, timing="UNKNOWN", confirmed=0)

        removed = cleanup_duplicate_earnings(window_past_days=365, quarter_window_days=45)

        assert len(removed) == 1
        assert removed[0]["removed_date"] == stale
        assert removed[0]["kept_date"] == kept
        assert _get_dates(db, "STALE") == [kept]

    def test_widened_window_still_respects_confirmed_vs_confirmed_guard(self, pg_schema):
        """Widening the window must not weaken rule 2.

        Two confirmed historical entries with no corroboration source available
        stay put — a wide sweep is not a licence to guess at ties.
        """
        db = _create_db(pg_schema)
        _insert(db, "TIE", _past(200), timing="AMC", confirmed=1)
        _insert(db, "TIE", _past(205), timing="AMC", confirmed=1)

        removed = cleanup_duplicate_earnings(window_past_days=365, quarter_window_days=45)

        assert removed == []
        assert _count(db, "TIE") == 2

    def test_widened_window_keeps_separate_quarters(self, pg_schema):
        """A wide window must not collapse genuinely different quarters."""
        db = _create_db(pg_schema)
        q1 = _past(300)
        q2 = _past(210)
        _insert(db, "QQ", q1, timing="AMC", confirmed=1)
        _insert(db, "QQ", q2, timing="AMC", confirmed=1)

        removed = cleanup_duplicate_earnings(window_past_days=400, quarter_window_days=45)

        assert removed == []
        assert _get_dates(db, "QQ") == [q1, q2]


class TestCleanupDupesCLIWindowFlag:

    def test_cli_exposes_window_past_days(self, pg_schema):
        """--cleanup-dupes must be able to reach the historical backlog.

        Without a CLI flag the wider pass is unreachable in practice: the
        function supports window_past_days but every real invocation goes
        through this entry point, which hardcoded the 14-day default.
        """
        import os
        import subprocess
        import sys as _sys

        db = _create_db(pg_schema)
        kept = _past(200)
        stale = _past(207)
        _insert(db, "STALE", kept, timing="AMC", confirmed=1)
        _insert(db, "STALE", stale, timing="UNKNOWN", confirmed=0)

        # No DB_PATH: the subprocess reaches Postgres through PG* (and the
        # pg_schema fixture's PGOPTIONS, inherited via os.environ).
        env = dict(os.environ)
        proc = subprocess.run(
            [_sys.executable, "scripts/sync_earnings_calendar.py",
             "--cleanup-dupes", "--window-past-days", "365",
             "--quarter-window-days", "45", "--execute"],
            cwd=str(Path(__file__).parent.parent.parent),
            env=env, capture_output=True, text=True, timeout=120,
        )

        assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
        assert _get_dates(db, "STALE") == [kept]


class TestWideScanGuard:
    """CLAUDE.md: never widen the dedup scan window without tightening the
    quarter window. Rules 1 and 3 delete entries up to 90 days apart, and real
    consecutive quarters are ~90 days apart: a 1000-day dry run proposed
    removing 2,093 rows against 507 genuine clusters. The flag's own help text
    suggested --window-past-days 1000 with the rules unchanged."""

    def test_a_wide_scan_with_the_quarter_window_untouched_is_refused(self, pg_schema):
        _create_db(pg_schema)
        with pytest.raises(ValueError, match="quarter"):
            cleanup_duplicate_earnings(dry_run=True, window_past_days=365)

    def test_a_wide_scan_with_a_tightened_quarter_window_runs(self, pg_schema):
        _create_db(pg_schema)
        assert cleanup_duplicate_earnings(
            dry_run=True, window_past_days=365, quarter_window_days=45) == []

    def test_the_quarter_window_is_honoured(self, pg_schema):
        db = _create_db(pg_schema)
        _insert(db, "WIDE", _past(100), timing="AMC", confirmed=1)
        _insert(db, "WIDE", _past(40), timing="UNKNOWN", confirmed=0)   # 60 days apart
        cleanup_duplicate_earnings(dry_run=False, window_past_days=365, quarter_window_days=45)
        assert _get_dates(db, "WIDE") == [_past(100), _past(40)]

    def test_the_cli_refuses_too(self, pg_schema):
        import os
        import subprocess
        import sys as _sys
        _create_db(pg_schema)
        proc = subprocess.run(
            [_sys.executable, "scripts/sync_earnings_calendar.py",
             "--cleanup-dupes", "--window-past-days", "365"],
            cwd=str(Path(__file__).parent.parent.parent),
            env=dict(os.environ), capture_output=True, text=True, timeout=120,
        )
        assert proc.returncode == 2
        assert "--quarter-window-days" in proc.stderr
