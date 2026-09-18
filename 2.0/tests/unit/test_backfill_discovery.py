"""Yahoo discovery in scripts/backfill_historical.py, against real Postgres.

From the 2026-09-12 cutover until this test existed, discovery could not insert
a row: it bound the literal 1 to the boolean `confirmed` and wrote '' into the
CHECK-constrained `timing`. And when it did work, under SQLite, it checked only
for an exact date, so a Yahoo date one day off a stored event created a
duplicate cluster.
"""

import sys
import types
from datetime import date

import pandas as pd
import pytest

from common.db import connect  # noqa: E402
from common.pgtesting import baseline_ddl  # noqa: E402

pytestmark = pytest.mark.pg


def _earnings_dates(entries):
    """A yfinance-shaped earnings_dates frame: tz-aware index, Reported EPS."""
    idx = pd.DatetimeIndex([pd.Timestamp(ts, tz="America/New_York") for ts, _ in entries])
    return pd.DataFrame({"Reported EPS": [eps for _, eps in entries]}, index=idx)


@pytest.fixture
def discover(pg_schema, monkeypatch):
    with connect() as conn:
        conn.execute(baseline_ddl("earnings_calendar"))
        conn.execute("INSERT INTO earnings_calendar (ticker, earnings_date, timing, confirmed)"
                     " VALUES ('ASML', '2026-01-28', 'BMO', true)")
        conn.commit()

    def run(entries):
        fake = types.SimpleNamespace(
            Ticker=lambda _t: types.SimpleNamespace(earnings_dates=_earnings_dates(entries)))
        monkeypatch.setitem(sys.modules, "yfinance", fake)
        from scripts.backfill_historical import discover_earnings_from_yahoo
        return discover_earnings_from_yahoo("ASML")

    return run


def _calendar():
    with connect() as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT earnings_date, timing, confirmed FROM earnings_calendar ORDER BY 1")]


def test_inserts_new_quarters_with_valid_timing_and_boolean_confirmed(discover):
    n = discover([
        ("2025-10-15 07:00", 5.4),   # BMO
        ("2025-07-16 12:00", 5.1),   # midday -> UNKNOWN
        ("2025-04-16 16:30", 6.0),   # AMC
    ])
    assert n == 3
    assert _calendar() == [
        (date(2025, 4, 16), "AMC", True),
        (date(2025, 7, 16), "UNKNOWN", True),
        (date(2025, 10, 15), "BMO", True),
        (date(2026, 1, 28), "BMO", True),
    ]


def test_a_date_beside_a_stored_event_is_the_same_quarter_and_is_skipped(discover):
    """Yahoo files ASML's 2026-01-28 pre-market report as 01-27 after close."""
    assert discover([("2026-01-27 17:00", 7.3)]) == 0
    assert _calendar() == [(date(2026, 1, 28), "BMO", True)]


def test_future_and_unreported_dates_are_ignored(discover):
    assert discover([("2026-10-14 07:00", float("nan"))]) == 0
    assert _calendar() == [(date(2026, 1, 28), "BMO", True)]


def test_discovery_dry_run_counts_but_inserts_nothing(pg_schema, monkeypatch):
    with connect() as conn:
        conn.execute(baseline_ddl("earnings_calendar"))
        conn.commit()
    fake = types.SimpleNamespace(Ticker=lambda _t: types.SimpleNamespace(
        earnings_dates=_earnings_dates([("2025-10-15 07:00", 5.4)])))
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    from scripts.backfill_historical import discover_earnings_from_yahoo

    assert discover_earnings_from_yahoo("ASML", dry_run=True) == 1
    assert _calendar() == []


def test_backfill_dry_run_never_saves(monkeypatch):
    import scripts.backfill_historical as bh

    event = bh.EarningsEvent("MU", date(2026, 6, 24), bh.EarningsTiming.AMC)
    move = types.SimpleNamespace(gap_move_pct=3.0, close_move_pct=4.0)
    monkeypatch.setattr(bh, "get_db_earnings", lambda *a, **k: [event] * 4)
    monkeypatch.setattr(bh, "has_move_history", lambda _t: True)
    monkeypatch.setattr(bh, "get_twelve_data_prices", lambda *a, **k: ["price"])
    monkeypatch.setattr(bh, "calculate_earnings_move", lambda e, p: move)

    def save(_move):
        raise AssertionError("save_to_database called during a dry run")
    monkeypatch.setattr(bh, "save_to_database", save)

    assert bh.backfill_ticker("MU", dry_run=True) == 4


# --- when discovery runs: a ticker new to the system, inside the requested window ---

@pytest.fixture
def backfill(pg_schema, monkeypatch):
    """backfill_ticker against real earnings_calendar and historical_moves, with a
    fake Yahoo that offers `offered` and records whether it was asked. No prices,
    so no move is computed: these tests are about calendar rows only."""
    import scripts.backfill_historical as bh

    with connect() as conn:
        conn.execute(baseline_ddl("earnings_calendar"))
        conn.execute(baseline_ddl("historical_moves"))
        conn.commit()
    asked = []

    def run(ticker, offered, **kwargs):
        class FakeTicker:
            def __init__(self, symbol):
                self.symbol = symbol

            @property
            def earnings_dates(self):
                asked.append(self.symbol)
                return _earnings_dates(offered)

        monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=FakeTicker))
        monkeypatch.setattr(bh, "get_twelve_data_prices", lambda *a, **k: [])
        return bh.backfill_ticker(ticker, **kwargs)

    run.asked = asked
    return run


def _store(sql, *params):
    with connect() as conn:
        conn.execute(sql, params)
        conn.commit()


def _calendar_of(ticker):
    with connect() as conn:
        return [r[0] for r in conn.execute(
            "SELECT earnings_date FROM earnings_calendar WHERE ticker = %s ORDER BY 1", (ticker,))]


# Confirmed Yahoo quarters around a stored 2026-03-18 AMC report.
YAHOO_QUARTERS = [
    ("2025-12-17 16:05", 3.1),
    ("2026-03-18 16:05", 4.8),
    ("2026-06-24 16:05", 5.2),
    ("2026-09-02 16:05", 6.0),
]


def test_a_single_event_run_on_a_ticker_with_history_asks_yahoo_nothing(backfill):
    """`--start-date D --end-date D` holds one event, fewer than the old 4, so every
    one-event run discovered and inserted each confirmed Yahoo date on or after D."""
    _store("INSERT INTO earnings_calendar (ticker, earnings_date, timing, confirmed)"
           " VALUES ('MU', '2025-12-17', 'AMC', true), ('MU', '2026-03-18', 'AMC', true)")
    _store("INSERT INTO historical_moves (ticker, earnings_date, prev_close, earnings_open,"
           " earnings_high, earnings_low, earnings_close)"
           " VALUES ('MU', '2025-12-17', 100, 104, 106, 103, 105)")
    d = date(2026, 3, 18)

    backfill("MU", YAHOO_QUARTERS, start_date=d, end_date=d)

    assert backfill.asked == []
    assert _calendar_of("MU") == [date(2025, 12, 17), date(2026, 3, 18)]


def test_a_single_event_run_on_a_ticker_with_no_moves_adds_nothing_outside_its_day(backfill):
    """A traded ticker with no move row still discovers, but only inside the window
    it was given, and its one day is already stored."""
    _store("INSERT INTO earnings_calendar (ticker, earnings_date, timing, confirmed)"
           " VALUES ('SKYT', '2026-03-18', 'AMC', true)")
    d = date(2026, 3, 18)

    backfill("SKYT", YAHOO_QUARTERS, start_date=d, end_date=d)

    assert _calendar_of("SKYT") == [d]


def test_a_single_event_report_only_run_proposes_no_calendar_date(backfill, caplog):
    """/maintenance Step 7b and /backfill refuse a run that prints `Would add`."""
    _store("INSERT INTO earnings_calendar (ticker, earnings_date, timing, confirmed)"
           " VALUES ('SKYT', '2026-03-18', 'AMC', true)")
    d = date(2026, 3, 18)

    with caplog.at_level("INFO"):
        backfill("SKYT", YAHOO_QUARTERS, start_date=d, end_date=d, dry_run=True)

    assert "Would add" not in caplog.text


def test_a_ticker_new_to_the_system_discovers_its_history_inside_the_window(backfill):
    """The auto-backfill (trade.sh, the scan) asks for three years to yesterday for a
    ticker with no move row: discovery is how it gets any history at all."""
    _store("INSERT INTO earnings_calendar (ticker, earnings_date, timing, confirmed)"
           " VALUES ('NEWCO', '2026-12-09', 'AMC', false)")          # the upcoming report

    backfill("NEWCO", YAHOO_QUARTERS, start_date=date(2025, 10, 1), end_date=date(2026, 6, 30))

    assert backfill.asked == ["NEWCO"]
    assert _calendar_of("NEWCO") == [date(2025, 12, 17), date(2026, 3, 18),
                                     date(2026, 6, 24), date(2026, 12, 9)]
