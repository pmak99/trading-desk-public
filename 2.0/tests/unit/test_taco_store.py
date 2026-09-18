"""Unit tests for TACO store CRUD (scripts/taco/store.py).

Migration 018 is gone -- the SQLite lineage collapsed into
2.0/sql/baseline_schema.sql at the Postgres port, and the shape of these
tables there is covered by scripts/tests/test_baseline_schema.py. What is
left is the CRUD contract, which is what actually records a TACO trade.
"""


import pytest

from common.db import connect  # noqa: E402

pytestmark = pytest.mark.pg

# Built FROM baseline_schema.sql, not transcribed. The transcribed copy that
# lived here still had text dates, a float contract count and an integer
# rule_compliant after production moved on -- a fixture that drifts from the
# real schema tests the wrong thing while staying green.
from common.pgtesting import baseline_ddl  # noqa: E402


@pytest.fixture
def db_path(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("taco_log", "taco_positions"))
        conn.commit()
    return pg_schema


from datetime import date  # noqa: E402

from scripts.taco.market_data import EntryRefs  # noqa: E402
from scripts.taco.store import (  # noqa: E402
    close_position,
    log_run,
    open_positions,
    record_position,
)

CALL_REFS = EntryRefs("CALL", date(2026, 6, 26), 100.0, 90.0, None, None, 15.0)


class TestStore:
    def test_log_run_inserts(self, db_path):
        rowid = log_run(mode="check", direction="CALL", spot=7546.0,
                        drawdown_pct=3.2, vix=22.0, vix_spike=1.3,
                        vix_term_ratio=1.02, event_date="2026-07-10",
                        event_type="GEOPOLITICAL",
                        event_rationale="test headline", score=61.0,
                        tier="HALF", recommendation="HALF ~$84k SPX calls")
        with connect() as con:
            row = con.execute('SELECT "timestamp" FROM taco_log WHERE id=%s', (rowid,)).fetchone()
        assert row.timestamp.tzinfo is not None, "timestamp must be an aware instant"
        assert rowid > 0

    def test_log_run_rejects_unknown_fields(self, db_path):
        with pytest.raises(ValueError):
            log_run(mode="check", bogus_column=1)

    def test_record_close_roundtrip(self, db_path):
        pid = record_position(direction="CALL", symbol="SPX", contracts=5,
            strike=7800.0, expiration="2027-06-18", premium_paid=150_000.0,
            entry_date="2026-07-14", event_date="2026-07-10", refs=CALL_REFS)
        opens = open_positions()
        assert len(opens) == 1
        assert opens[0]["panic_low"] == 90.0
        assert opens[0]["rule_compliant"] is True
        # Dates come back as dates now; the CLI reads them through _parse_date.
        assert opens[0]["expiration"] == date(2027, 6, 18)

        pnl = close_position(pid, "2026-07-20", 180_000.0)
        assert pnl == 30_000.0
        assert open_positions() == []

    def test_close_missing_position_raises(self, db_path):
        with pytest.raises(ValueError):
            close_position(999, "2026-07-20", 1.0)

    def test_rule_compliant_zero_persists(self, db_path):
        record_position(direction="PUT", symbol="SPX", contracts=12,
            strike=7450.0, expiration="2027-04-16", premium_paid=401_650.0,
            entry_date="2026-07-01", event_date="2026-06-25",
            refs=EntryRefs("PUT", date(2026, 6, 25), 7500.0, None, 7550.0,
                           7400.0, 17.0),
            rule_compliant=0)
        assert open_positions()[0]["rule_compliant"] is False

    def test_log_run_accepts_cross_asset_fields(self, db_path):
        """migration 019: cross-asset forward-validation columns."""
        rid = log_run(mode="check", direction="CALL",
                      cross_asset_count=2, cross_asset_available=3,
                      cross_asset_detail='[{"symbol": "TLT"}]')
        with connect() as con:
            row = con.execute("SELECT * FROM taco_log WHERE id=%s", (rid,)).fetchone()
        assert row.cross_asset_count == 2
        assert row.cross_asset_available == 3
        assert "TLT" in row.cross_asset_detail


class TestMinDte:
    """DTE >= 90 is part of the strategy's definition. It used to be checked
    only in the CLI, so any other caller of record_position could store a
    short-dated index trade as a TACO position."""

    def _record(self, expiration, entry_date="2026-07-14"):
        return record_position(direction="CALL", symbol="SPX", contracts=5,
            strike=7800.0, expiration=expiration, premium_paid=150_000.0,
            entry_date=entry_date, event_date="2026-07-10", refs=CALL_REFS)

    def test_rejects_below_the_minimum_and_writes_nothing(self, db_path):
        with pytest.raises(ValueError, match=r"DTE 89 .*minimum .*90"):
            self._record("2026-10-11")                    # 89 days
        assert open_positions() == []

    def test_accepts_exactly_the_minimum(self, db_path):
        self._record("2026-10-12")                        # 90 days
        assert open_positions()[0]["expiration"] == date(2026, 10, 12)

    def test_accepts_date_objects(self, db_path):
        self._record(date(2027, 6, 18), entry_date=date(2026, 7, 14))
        assert open_positions()[0]["entry_date"] == date(2026, 7, 14)

    def test_rejects_an_unparseable_date(self, db_path):
        with pytest.raises(ValueError, match="expiration"):
            self._record("06/18/2027")
        assert open_positions() == []

    def test_rule_is_one_function_shared_with_the_cli(self):
        from scripts.taco import cli, store
        assert cli.require_min_dte is store.require_min_dte
        assert store.require_min_dte(date(2026, 7, 14), date(2027, 6, 18)) == 339
