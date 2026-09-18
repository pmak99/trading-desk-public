"""scripts/integrity_report.py against real Postgres.

Every defect this report looks for was found by hand on 2026-09-13, after it had
sat unnoticed: six strategies dated days after their report, 93 rows with no
trade_type, three traded events with no move row, 29 move clusters, strategies
whose P&L disagreed with their legs. Each check gets a row that trips it and a
clean book that trips nothing.

Two checks (2026-09-15) watch the `positions` view: rule_evidence.py's Python merge
refused a trade twice in one account, and a pair across accounts that differed only
in earnings date or parent; the view leaves both unmerged, so this report names them.
"""

from datetime import date

import pytest

from common.db import connect
from common.pgtesting import baseline_ddl

pytestmark = pytest.mark.pg

TODAY = date(2026, 9, 13)


@pytest.fixture
def db(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("strategies", "trade_journal", "historical_moves",
                                  "earnings_calendar", "positions", "analysis_log"))
        conn.commit()
    return pg_schema


def _move(conn, ticker, day):
    conn.execute(
        "INSERT INTO historical_moves (ticker, earnings_date, prev_close, earnings_open,"
        " earnings_high, earnings_low, earnings_close) VALUES (%s, %s, 100, 103, 104, 99, 102)",
        (ticker, day))


def _event(conn, ticker, day, timing="AMC"):
    conn.execute("INSERT INTO earnings_calendar (ticker, earnings_date, timing, confirmed)"
                 " VALUES (%s, %s, %s, true)", (ticker, day, timing))


def _analyzed(conn, ticker, day):
    """A ticker /analyze, scan or whisper actually ran VRP on -- never traded."""
    conn.execute(
        "INSERT INTO analysis_log (timestamp, ticker, earnings_date, expiration,"
        " implied_move_pct, historical_intraday_mean_pct, vrp_intraday_ratio,"
        " recommendation, vrp_convention)"
        " VALUES (now(), %s, %s, %s, 5.0, 2.0, 2.5, 'good', 'intraday')",
        (ticker, day, day))


def _strategy(conn, symbol, acquired, sale, *, gain=100.0, stype="SPREAD", earnings=None,
              trade_type="NEW", parent=None, expiration=None, legs=True, account="TAXABLE"):
    sid = conn.execute(
        "INSERT INTO strategies (symbol, strategy_type, acquired_date, sale_date, expiration,"
        " gain_loss, is_winner, earnings_date, trade_type, parent_strategy_id, account_type)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (symbol, stype, acquired, sale, expiration or sale, gain, gain > 0, earnings,
         trade_type, parent, account)).fetchone()[0]
    if legs:
        _leg(conn, symbol, acquired, sale, gain, sid, account=account)
    return sid


def _leg(conn, symbol, acquired, sale, gain, sid, strike=100.0, account="TAXABLE"):
    # Cost basis from the strategy id: the journal's dedup key would refuse a second
    # lot identical to the first, and a trade twice in one account needs one.
    basis = sid or 0
    conn.execute(
        "INSERT INTO trade_journal (symbol, acquired_date, sale_date, option_type, strike,"
        " quantity, cost_basis, proceeds, gain_loss, is_winner, strategy_id, account_type)"
        " VALUES (%s, %s, %s, 'PUT', %s, 1, %s, %s, %s, %s, %s, %s)",
        (symbol, acquired, sale, strike, basis, basis + gain, gain, gain > 0, sid, account))


def _clean_book(conn):
    _event(conn, "AAA", "2026-05-04")
    _move(conn, "AAA", "2026-05-04")
    _event(conn, "AAA", "2026-08-04")
    _move(conn, "AAA", "2026-08-04")
    _strategy(conn, "AAA", "2026-05-04", "2026-05-05", earnings="2026-05-04")


def _counts():
    from scripts.integrity_report import run_checks

    with connect() as conn:
        return {r.check.name: len(r.rows) for r in run_checks(conn, TODAY)}


def test_a_clean_book_trips_nothing(db):
    with connect() as conn:
        _clean_book(conn)
        conn.commit()
    assert set(_counts().values()) == {0}


@pytest.mark.parametrize("name, plant", [
    ("strategy_pnl_differs_from_legs",
     lambda c: c.execute("UPDATE strategies SET gain_loss = gain_loss + 5 WHERE symbol='AAA'")),
    ("strategy_without_legs",
     lambda c: _strategy(c, "BBB", "2026-03-02", "2026-03-03", legs=False)),
    ("leg_without_strategy",
     lambda c: _leg(c, "CCC", "2026-03-02", "2026-03-03", 10.0, None)),
    ("strategy_dates_inverted",
     lambda c: _strategy(c, "DDD", "2026-03-05", "2026-03-03")),
    ("earnings_date_without_move",
     lambda c: _strategy(c, "AAA", "2026-08-04", "2026-08-06", earnings="2026-08-06")),
    ("trade_type_missing",
     lambda c: _strategy(c, "EEE", "2026-03-02", "2026-03-03", trade_type=None)),
    ("adjustment_label_disagrees_with_parent",
     lambda c: _strategy(c, "FFF", "2026-03-02", "2026-03-03", trade_type="ROLL")),
    # The clean book's AAA spread again in the IRA, dated by the journal to another event:
    # the view keeps them apart, so it is two positions where the trade was one.
    ("mirrored_trade_not_merged",
     lambda c: _strategy(c, "AAA", "2026-05-04", "2026-05-05", earnings="2026-08-04",
                         account="IRA")),
    ("same_trade_twice_in_one_account",
     lambda c: _strategy(c, "AAA", "2026-05-04", "2026-05-05", earnings="2026-05-04")),
    ("undated_trade_held_across_event",
     lambda c: _strategy(c, "AAA", "2026-08-03", "2026-08-05", stype="SINGLE")),
    ("move_cluster_15d",
     lambda c: _move(c, "AAA", "2026-08-12")),
    ("calendar_cluster_15d",
     lambda c: _event(c, "AAA", "2026-08-10", timing="UNKNOWN")),
    ("move_without_calendar_row",
     lambda c: _move(c, "GGG", "2026-02-10")),
    ("past_event_timing_unknown",
     lambda c: _event(c, "HHH", "2026-06-01", timing="UNKNOWN")),
    ("traded_event_without_move",
     lambda c: _event(c, "AAA", "2026-02-03")),
    # A quarter absent from BOTH tables: 2025-11-04 -> 2026-05-04 is 181 days,
    # so the Feb 2026 report was never stored anywhere. No other check can see it.
    ("quarter_missing_from_calendar_and_moves",
     lambda c: (_event(c, "AAA", "2025-11-04"), _move(c, "AAA", "2025-11-04"))),
])
def test_each_check_finds_its_defect(db, name, plant):
    with connect() as conn:
        _clean_book(conn)
        plant(conn)
        conn.commit()
    counts = _counts()
    assert counts[name] >= 1, counts
    # A planted defect trips its own check; name the others it also trips so a
    # check that fires on everything can't pass.
    assert {k for k, v in counts.items() if v} <= {name} | COLLATERAL.get(name, set()), counts


# Defects that genuinely imply a second finding.
COLLATERAL = {
    "calendar_cluster_15d": {"past_event_timing_unknown", "traded_event_without_move"},
    "move_cluster_15d": {"move_without_calendar_row"},
    "leg_without_strategy": set(),
    "traded_event_without_move": set(),
}


def test_a_trade_the_view_merges_is_not_a_finding(db):
    with connect() as conn:
        _clean_book(conn)
        _strategy(conn, "AAA", "2026-05-04", "2026-05-05", earnings="2026-05-04", account="IRA")
        conn.commit()
        assert conn.execute("SELECT count(*) FROM positions").fetchone()[0] == 1
    assert set(_counts().values()) == {0}


def test_an_unmerged_pair_names_both_rows_and_what_differs(db):
    from scripts.integrity_report import run_checks

    with connect() as conn:
        _clean_book(conn)
        [first] = conn.execute("SELECT id FROM strategies").fetchone()
        parent = _strategy(conn, "AAA", "2026-03-02", "2026-03-03")
        second = _strategy(conn, "AAA", "2026-05-04", "2026-05-05", earnings="2026-05-04",
                           account="IRA", trade_type="ROLL", parent=parent)
        conn.commit()
        result = next(r for r in run_checks(conn, TODAY)
                      if r.check.name == "mirrored_trade_not_merged")
    [row] = result.rows
    found = row._asdict()
    assert (found["first_id"], found["second_id"]) == (first, second)
    assert (found["first_account"], found["second_account"]) == ("TAXABLE", "IRA")
    assert (found["first_earnings"], found["second_earnings"]) == (date(2026, 5, 4),) * 2
    assert (found["first_parent"], found["second_parent"]) == (None, parent)


def test_a_gap_is_measured_across_either_table_and_a_late_quarter_is_not_flagged(db):
    """A date in only one table still closes a gap, so a missing quarter is one
    absent from both. QUARTER_GAP_DAYS (150) sits in the valley of the live
    distribution (2026-09-13, traded tickers: 130 gaps at 100-114 days, 5 at 119-139,
    22 at 175-280): a late fiscal-year report is ~105-115 days, a 16-week fourth
    quarter ~119, a lost quarter ~180."""
    from scripts.integrity_report import run_checks

    with connect() as conn:
        _clean_book(conn)
        _move(conn, "AAA", "2026-01-28")          # move row only: fills the Feb quarter
        _move(conn, "AAA", "2025-10-06")          # 114 days before 01-28: a late report
        _event(conn, "BBB", "2026-01-10")         # untraded AND unanalyzed: never checked
        _event(conn, "BBB", "2026-09-01")
        _strategy(conn, "SPY", "2026-08-03", "2026-08-05", stype="SINGLE")
        _event(conn, "SPY", "2025-01-02")         # index: excluded
        _event(conn, "SPY", "2026-08-04")
        _move(conn, "SPY", "2026-08-04")
        _move(conn, "SPY", "2025-01-02")
        conn.commit()
        results = {r.check.name: r for r in run_checks(conn, TODAY)}
    assert results["quarter_missing_from_calendar_and_moves"].rows == []


def test_a_missing_quarter_row_names_both_neighbours(db):
    from scripts.integrity_report import run_checks

    with connect() as conn:
        _clean_book(conn)
        _event(conn, "AAA", "2025-11-04")
        _move(conn, "AAA", "2025-11-04")
        conn.commit()
        result = next(r for r in run_checks(conn, TODAY)
                      if r.check.name == "quarter_missing_from_calendar_and_moves")
    assert [tuple(row) for row in result.rows] == [
        ("AAA", date(2025, 11, 4), date(2026, 5, 4), 181, True)]


def test_a_gap_on_an_analyzed_but_never_traded_ticker_is_now_flagged(db):
    """LEN: 12 quarters on file, never traded, two of them six months stale --
    invisible to this check before 2026-09-16 because it scanned _TRADED alone.
    An analysis_log row (what /analyze, scan and whisper each write) is enough
    to bring a ticker into scope, marked traded=False so it still sorts behind
    the tickers with real money on them."""
    from scripts.integrity_report import run_checks

    with connect() as conn:
        _clean_book(conn)
        _event(conn, "ZZZ", "2025-11-04")
        _move(conn, "ZZZ", "2025-11-04")
        _event(conn, "ZZZ", "2026-05-04")         # the quarter after the gap
        _move(conn, "ZZZ", "2026-05-04")
        _analyzed(conn, "ZZZ", "2026-09-15")      # never traded, only ever analyzed
        conn.commit()
        result = next(r for r in run_checks(conn, TODAY)
                      if r.check.name == "quarter_missing_from_calendar_and_moves")
    assert [tuple(row) for row in result.rows if row.ticker == "ZZZ"] == [
        ("ZZZ", date(2025, 11, 4), date(2026, 5, 4), 181, False)]


def test_index_and_future_events_are_not_flagged(db):
    with connect() as conn:
        _clean_book(conn)
        _event(conn, "AAA", "2026-11-04", timing="UNKNOWN")      # future: timing not due yet
        _strategy(conn, "SPY", "2026-08-03", "2026-08-05", stype="SINGLE")  # index option
        _event(conn, "SPY", "2026-08-04")
        _move(conn, "SPY", "2026-08-04")
        conn.commit()
    assert set(_counts().values()) == {0}
