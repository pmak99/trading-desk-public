"""jobs/record_moves.py -- append each newly reported quarter to historical_moves.

Root cause it answers (2026-09-13): nothing scheduled recorded a new quarter for a
ticker that already had history, so VRP/TRR baselines went a quarter stale for
every name nobody happened to /analyze. backfill_historical.py could not simply be
scheduled: with <4 events in range it runs Yahoo discovery (calendar inserts), it
infers UNKNOWN timing from prices and writes that to the calendar as confirmed,
and a bare ticker re-derives every calendar row.

So this job writes a missing move row only when every gate passes, and one thing
to the calendar -- a timing tag, when Yahoo and Finnhub both report the stored
date with the same BMO/AMC. Its first --execute run (2026-09-13) Telegrammed 50
"needs review" events nobody would read and held Kuma red; the job now fixes what
two sources agree on, tells a human only about traded tickers, and pushes Kuma
up after each finished run (a failure is reported once, by run_job's Telegram).

Each gate below has a test that trips it; nothing here makes a network call
(Yahoo, Finnhub, Twelve Data, Telegram and Kuma are all injected or patched, and
an autouse fixture refuses any HTTP request).
"""

from datetime import date, timedelta
from unittest.mock import Mock, patch

import pytest
import requests

from common.db import connect
from common.pgtesting import baseline_ddl
from scripts import earnings_moves as em
from scripts.jobs import record_moves as rm
from src.domain.market_calendar import is_trading_day
from src.infrastructure.api.finnhub import FinnhubAPI
from src.infrastructure.data_sources.yahoo_finance_earnings import YahooFinanceEarnings

MOD = "scripts.jobs.record_moves"


@pytest.fixture(autouse=True)
def _no_migrations_in_job_tests(monkeypatch):
    """The job migrates at start; a test must not migrate whatever PGDATABASE names
    (the session guard in production_guard.py refuses it against production)."""
    monkeypatch.setattr("scripts.jobs.record_moves.apply_migrations", lambda: 0)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("record_moves tests must not make a network call")

    monkeypatch.setattr(requests.sessions.Session, "request", refuse)
    # yfinance does not go through requests; refuse the two vendor calls the job makes.
    monkeypatch.setattr(YahooFinanceEarnings, "get_earnings_date_near", refuse)
    monkeypatch.setattr(FinnhubAPI, "get_earnings_calendar", refuse)


def _cand(ticker="RH", day="2026-09-10", timing="AMC", near_calendar=(), near_moves=()):
    return rm.Candidate(ticker, date.fromisoformat(day), timing,
                        tuple(date.fromisoformat(d) for d in near_calendar),
                        tuple(date.fromisoformat(d) for d in near_moves))


def _d(s):
    return date.fromisoformat(s)


# --- gate (a): reaction session complete, strictly before today (ET) -----------

@pytest.mark.parametrize("day, timing, today, reference, reaction", [
    # BMO on a Thursday, run Friday: reference is Wednesday's close.
    ("2026-09-10", "BMO", "2026-09-11", "2026-09-09", "2026-09-10"),
    # AMC on a Thursday, run Saturday: reaction Friday.
    ("2026-09-10", "AMC", "2026-09-12", "2026-09-10", "2026-09-11"),
    # Friday AMC -> Monday reaction, recorded by Tuesday's run.
    ("2026-08-28", "AMC", "2026-09-01", "2026-08-28", "2026-08-31"),
    # Friday AMC before Labor Day (Mon 2026-09-07) -> Tuesday reaction.
    ("2026-09-04", "AMC", "2026-09-09", "2026-09-04", "2026-09-08"),
    # BMO the day after Labor Day -> reference is the Friday before.
    ("2026-09-08", "BMO", "2026-09-09", "2026-09-04", "2026-09-08"),
    # Thursday AMC before the observed Independence Day (Fri 2026-07-03).
    ("2026-07-02", "AMC", "2026-07-07", "2026-07-02", "2026-07-06"),
])
def test_ready_events_get_the_exact_reference_and_reaction_sessions(day, timing, today,
                                                                     reference, reaction):
    c = _cand(day=day, timing=timing)
    assert rm.screen(c, _d(today)) is None
    assert rm.sessions_for(c) == rm.Sessions(_d(reference), _d(reaction))


@pytest.mark.parametrize("timing", ["UNKNOWN", "DMH"])
def test_sessions_are_never_derived_from_a_timing_that_is_not_bmo_or_amc(timing):
    with pytest.raises(ValueError):
        rm.sessions_for(_cand(timing=timing))


@pytest.mark.parametrize("day, timing, today", [
    ("2026-09-11", "BMO", "2026-09-11"),   # BMO today: the session is still trading
    ("2026-09-10", "AMC", "2026-09-11"),   # AMC yesterday: reaction is today, mid-session
    ("2026-08-28", "AMC", "2026-08-29"),   # Friday AMC, Saturday run: Monday not yet traded
    ("2026-08-28", "AMC", "2026-08-31"),   # ...and on Monday itself
    ("2026-09-04", "AMC", "2026-09-08"),   # Labor Day pushes the reaction to Tuesday
])
def test_an_incomplete_reaction_session_waits_it_is_not_a_leftover(day, timing, today):
    """Not due is not wrong. Listing tomorrow's work as a leftover would put every
    normal event in front of a human once, and train them to ignore it."""
    got = rm.screen(_cand(day=day, timing=timing), _d(today))
    assert isinstance(got, rm.Outcome)
    assert (got.status, got.kind) == (rm.PENDING, "not_due")


@pytest.mark.parametrize("day, timing", [
    ("2026-09-07", "BMO"),   # Labor Day
    ("2026-09-07", "AMC"),
    ("2026-09-05", "AMC"),   # Saturday
    ("2026-07-03", "BMO"),   # observed Independence Day
])
def test_an_event_dated_on_a_closed_market_day_is_left_over(day, timing):
    """The calculator would silently move a closed-day date to the nearest bar --
    a Sunday AMC becomes Monday close -> Tuesday open, the wrong reaction."""
    got = rm.screen(_cand(day=day, timing=timing), _d("2026-09-20"))
    assert (got.status, got.kind) == (rm.LEFT, "session")
    assert day in got.reason


# --- timing: not a database gate any more; resolved by two sources --------------

@pytest.mark.parametrize("timing", ["UNKNOWN", "DMH"])
def test_unknown_timing_passes_the_database_gates_to_be_resolved_by_yahoo_and_finnhub(timing):
    assert rm.screen(_cand(day="2026-09-02", timing=timing), _d("2026-09-10")) is None


def test_gates_run_in_order_a_closed_day_is_reported_before_unknown_timing():
    got = rm.screen(_cand(day="2026-09-07", timing="UNKNOWN"), _d("2026-09-20"))
    assert got.kind == "session"


def test_unknown_timing_is_not_due_before_its_latest_possible_reaction_is_done():
    got = rm.screen(_cand(day="2026-09-10", timing="UNKNOWN"), _d("2026-09-11"))
    assert (got.status, got.kind) == (rm.PENDING, "not_due")


# --- gate (c): clusters --------------------------------------------------------

@pytest.mark.parametrize("near_calendar, near_moves, shown", [
    (("2026-09-03",), (), "2026-09-03"),     # a second calendar row a week earlier
    ((), ("2026-08-27",), "2026-08-27"),     # a move row 14 days earlier
    (("2026-09-24",), (), "2026-09-24"),     # a calendar row 14 days later
])
def test_another_row_within_15_days_is_left_over(near_calendar, near_moves, shown):
    got = rm.screen(_cand(day="2026-09-10", timing="AMC", near_calendar=near_calendar,
                          near_moves=near_moves), _d("2026-09-20"))
    assert (got.status, got.kind) == (rm.LEFT, "cluster")
    assert shown in got.reason


# --- gate (d): the bars --------------------------------------------------------

def _bar(day, open_=100.0, close=101.0, volume=1000):
    return em.DailyPrice(_d(day), open_, max(open_, close) + 1, min(open_, close) - 1, close, volume)


WEEK = [_bar("2026-09-08", 95, 96), _bar("2026-09-09", 96, 97), _bar("2026-09-10", 97, 100),
        _bar("2026-09-11", 108, 110), _bar("2026-09-14", 110, 111)]


def test_bars_present_give_the_move_from_the_expected_sessions():
    move = rm.check_bars(_cand(), rm.Sessions(_d("2026-09-10"), _d("2026-09-11")), WEEK)
    assert isinstance(move, em.EarningsMove)
    assert (move.prev_close, move.reaction_open, move.reaction_close) == (100.0, 108.0, 110.0)
    assert move.earnings_date == _d("2026-09-10")


def test_bmo_move_uses_the_previous_session_close():
    move = rm.check_bars(_cand(timing="BMO"), rm.Sessions(_d("2026-09-09"), _d("2026-09-10")), WEEK)
    assert (move.prev_close, move.reaction_open) == (97.0, 97.0)


@pytest.mark.parametrize("prices, words", [
    ([b for b in WEEK if b.date != _d("2026-09-11")], "reaction"),
    ([b for b in WEEK if b.date != _d("2026-09-10")], "reference"),
    ([_bar("2026-09-10", 97, 100, volume=0)] + WEEK[3:], "volume"),
    ([], "reference"),
])
def test_missing_or_zero_volume_bars_are_left_over(prices, words):
    got = rm.check_bars(_cand(), rm.Sessions(_d("2026-09-10"), _d("2026-09-11")), prices)
    assert (got.status, got.kind) == (rm.LEFT, "bars")
    assert words in got.reason


def test_an_extra_bar_between_the_sessions_is_caught_not_measured():
    """If Twelve Data has a bar on a day the holiday table calls closed, the
    calculator would measure from it. The cross-check refuses the move."""
    # AMC Friday 09-04 -> Tuesday 09-08; plant a bar on Labor Day 09-07.
    prices = [_bar("2026-09-03", 90, 91), _bar("2026-09-04", 91, 92), _bar("2026-09-07", 99, 99),
              _bar("2026-09-08", 105, 106)]
    got = rm.check_bars(_cand(day="2026-09-04"), rm.Sessions(_d("2026-09-04"), _d("2026-09-08")),
                        prices)
    assert (got.status, got.kind) == (rm.LEFT, "bars")
    assert "2026-09-07" in got.reason


# --- the two-source retag decision (pure) ----------------------------------------

@pytest.mark.parametrize("stored, found, y_timing, words", [
    ("AMC", "2026-12-17", "AMC", "2026-12-17"),   # OCC: Yahoo reports another date
    ("UNKNOWN", "2026-09-09", "AMC", "2026-09-09"),
    ("UNKNOWN", "2026-09-10", "DMH", "DMH"),       # Yahoo's hour says neither
    ("AMC", "2026-09-10", "DMH", "DMH"),
    ("DMH", "2026-09-10", "DMH", "DMH"),           # agreeing on DMH is not agreeing on a session
])
def test_yahoo_rules_out_a_retag_on_another_date_or_a_timing_that_is_not_bmo_or_amc(
        stored, found, y_timing, words):
    got = rm.yahoo_verdict(_cand(day="2026-09-10", timing=stored), _d(found), y_timing)
    assert got.status == rm.LEFT
    assert words in got.reason
    assert len(got.brief) <= rm.BRIEF_CHARS


@pytest.mark.parametrize("stored, y_timing", [("AMC", "AMC"), ("BMO", "BMO"),
                                              ("UNKNOWN", "AMC"), ("AMC", "BMO")])
def test_yahoo_on_the_stored_date_with_bmo_or_amc_is_not_ruled_out(stored, y_timing):
    assert rm.yahoo_verdict(_cand(day="2026-09-10", timing=stored), _d("2026-09-10"), y_timing) is None


@pytest.mark.parametrize("entries, words", [
    ([("2026-09-10", "UNKNOWN")], "hour blank"),          # Finnhub's hour field empty
    ([("2026-09-10", "BMO")], "BMO"),                     # Finnhub BMO, Yahoo AMC
    ([("2026-09-09", "AMC")], "2026-09-09"),              # Finnhub dates it a day earlier
    ([("2026-09-10", "AMC"), ("2026-09-11", "AMC")], "2026-09-11"),   # two dates: ambiguous
    ([], "no report"),                                     # NODATA
])
def test_finnhub_must_list_exactly_the_stored_date_with_yahoos_timing(entries, words):
    got = rm.finnhub_verdict(_cand(day="2026-09-10", timing="UNKNOWN"), "AMC",
                             [(_d(d), t) for d, t in entries])
    assert got.status == rm.LEFT and got.kind == "retag"
    assert words in got.reason
    assert len(got.brief) <= rm.BRIEF_CHARS


def test_finnhub_agreeing_with_yahoo_on_the_stored_date_passes():
    assert rm.finnhub_verdict(_cand(day="2026-09-10", timing="UNKNOWN"), "AMC",
                              [(_d("2026-09-10"), "AMC")]) is None


# --- the database: candidates, writes ------------------------------------------

pg = pytest.mark.pg
TODAY = _d("2026-09-15")   # a Tuesday


@pytest.fixture
def db(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("historical_moves", "earnings_calendar", "strategies",
                                  "trade_journal"))
        conn.commit()
    return pg_schema


def _event(conn, ticker, day, timing="AMC", confirmed=True):
    conn.execute("INSERT INTO earnings_calendar (ticker, earnings_date, timing, confirmed)"
                 " VALUES (%s, %s, %s, %s)", (ticker, day, timing, confirmed))


def _move_row(conn, ticker, day, prev=100.0, open_=103.0, close=102.0):
    conn.execute(
        "INSERT INTO historical_moves (ticker, earnings_date, prev_close, earnings_open,"
        " earnings_high, earnings_low, earnings_close) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (ticker, day, prev, open_, max(open_, close) + 1, min(open_, close) - 1, close))


def _known(conn, *tickers):
    """History a quarter back, so the ticker is known to the system."""
    for t in tickers:
        _event(conn, t, "2026-06-10")
        _move_row(conn, t, "2026-06-10")


def _traded(conn, symbol, table="strategies"):
    if table == "strategies":
        conn.execute("INSERT INTO strategies (symbol, strategy_type, acquired_date, sale_date,"
                     " gain_loss, is_winner) VALUES (%s, 'SPREAD', '2026-01-05', '2026-01-06',"
                     " 100, true)", (symbol,))
    else:
        conn.execute("INSERT INTO trade_journal (symbol, sale_date, cost_basis, proceeds,"
                     " gain_loss, is_winner) VALUES (%s, '2026-01-06', 1, 2, 1, true)", (symbol,))


def _bars_around(day, *, gap=5.0):
    """Two weeks of bars around `day` with a gap on the session after it."""
    out, d, price = [], _d(day) - timedelta(days=10), 100.0
    while d <= _d(day) + timedelta(days=10):
        if is_trading_day(d):
            open_ = price * (1 + gap / 100) if out and out[-1].date == _d(day) else price
            out.append(em.DailyPrice(d, open_, open_ + 1, open_ - 1, open_ + 0.5, 1000))
            price = open_ + 0.5
        d += timedelta(days=1)
    return out


class FakeFetch:
    """Stands in for Twelve Data. Records calls; returns bars around the window."""

    def __init__(self, raise_on=None, exc=None):
        self.calls = []
        self.raise_on, self.exc = raise_on, exc

    def __call__(self, ticker, start, end):
        self.calls.append((ticker, start, end))
        if ticker == self.raise_on:
            raise self.exc
        mid = start + (end - start) / 2
        return _bars_around(mid.isoformat())


class FakeYahoo:
    def __init__(self, answers):
        self.answers, self.calls = answers, []

    def __call__(self, ticker, day):
        self.calls.append((ticker, day))
        answer = self.answers[ticker]
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeFinnhub:
    """Stands in for Finnhub's symbol calendar: (date, timing) entries within 3 days."""

    def __init__(self, answers, clock=None):
        self.answers, self.calls, self.times = answers, [], []
        self.clock = clock

    def __call__(self, ticker, day):
        self.calls.append((ticker, day))
        if self.clock is not None:
            self.times.append(self.clock())
        answer = self.answers[ticker]
        if isinstance(answer, Exception):
            raise answer
        return [(_d(d), t) for d, t in answer]


def _run(conn, **kw):
    kw.setdefault("fetch", FakeFetch())
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("corroborate", None)
    kw.setdefault("finnhub", None)
    return rm.record_moves(conn, TODAY, **kw)


def _moves(conn):
    return conn.execute("SELECT ticker, earnings_date, prev_close, earnings_open FROM historical_moves"
                        " ORDER BY ticker, earnings_date").fetchall()


def _calendar(conn):
    return conn.execute("SELECT ticker, earnings_date, timing, confirmed, updated_at, last_validated_at"
                        " FROM earnings_calendar ORDER BY ticker, earnings_date").fetchall()


@pg
def test_candidates_are_recent_calendar_events_of_known_tickers_with_no_move(db):
    with connect() as conn:
        _known(conn, "AAA", "BBB", "CCC")
        _event(conn, "AAA", "2026-09-10")                  # candidate
        _event(conn, "BBB", "2026-09-02", timing="UNKNOWN")  # candidate (timing resolved later)
        _event(conn, "CCC", "2026-09-10")
        _move_row(conn, "CCC", "2026-09-10")               # already recorded
        _event(conn, "NEW", "2026-09-10")                  # unknown ticker: no history
        _event(conn, "AAA", "2026-08-24")                  # 22 days ago: outside
        _event(conn, "BBB", "2026-09-15")                  # today: not a past event
        conn.commit()
        got = rm.load_candidates(conn, TODAY)
    assert [(c.ticker, c.earnings_date.isoformat(), c.timing) for c in got] == [
        ("BBB", "2026-09-02", "UNKNOWN"), ("AAA", "2026-09-10", "AMC")]
    # AAA's 08-24 row is 17 days away: not a neighbour. BBB's today row is 13 days away.
    assert got[0].near_calendar == (_d("2026-09-15"),)
    assert got[1].near_calendar == () and got[1].near_moves == ()


@pg
def test_execute_records_the_move_and_writes_nothing_to_the_calendar(db):
    with connect() as conn:
        _known(conn, "AAA")
        _event(conn, "AAA", "2026-09-10", timing="AMC", confirmed=False)
        conn.commit()
        calendar_before = conn.execute("SELECT * FROM earnings_calendar ORDER BY 1, 2").fetchall()

        report = _run(conn, execute=True)

        assert [o.status for o in report.outcomes] == [rm.RECORDED]
        rows = _moves(conn)
        assert [(r.ticker, r.earnings_date) for r in rows] == [
            ("AAA", _d("2026-06-10")), ("AAA", _d("2026-09-10"))]
        new = rows[1]
        assert round((new.earnings_open - new.prev_close) / new.prev_close * 100, 6) == 5.0
        assert conn.execute("SELECT * FROM earnings_calendar ORDER BY 1, 2").fetchall() == calendar_before


@pg
def test_a_row_that_appears_mid_run_is_left_untouched(db):
    """Insert-only: if a manual backfill writes the row between selection and
    insert, the job's ON CONFLICT DO NOTHING keeps that row exactly as written."""
    with connect() as conn:
        _known(conn, "AAA")
        _event(conn, "AAA", "2026-09-10")
        conn.commit()

        def fetch(ticker, start, end):
            with connect() as other:
                _move_row(other, "AAA", "2026-09-10", prev=50.0, open_=51.0, close=52.0)
            return _bars_around("2026-09-10")

        report = _run(conn, execute=True, fetch=fetch)

        assert [o.status for o in report.outcomes] == [rm.ALREADY_PRESENT]
        row = [r for r in _moves(conn) if r.earnings_date == _d("2026-09-10")][0]
        assert (row.prev_close, row.earnings_open) == (50.0, 51.0)


@pytest.mark.parametrize("execute", [True, False])
@pg
def test_no_transaction_is_held_open_while_waiting_on_the_network(db, execute):
    """The fetch loop waits on vendor rate limits for up to 23 minutes. A connection
    left idle in transaction that long holds a snapshot open across the nightly
    dump window and any VACUUM. A retag commits before the next call too."""
    from psycopg.pq import TransactionStatus

    with connect() as conn:
        _known(conn, "AAA", "BBB")
        _event(conn, "AAA", "2026-09-09", timing="UNKNOWN")
        _event(conn, "BBB", "2026-09-10")
        conn.commit()
        seen = []

        def fetch(ticker, start, end):
            seen.append(conn.info.transaction_status)
            return _bars_around("2026-09-09" if ticker == "AAA" else "2026-09-10")

        class Finnhub(FakeFinnhub):
            def __call__(self, ticker, day):
                seen.append(conn.info.transaction_status)
                return super().__call__(ticker, day)

        _run(conn, execute=execute, fetch=fetch,
             corroborate=FakeYahoo({"AAA": (_d("2026-09-09"), "AMC"),
                                    "BBB": (_d("2026-09-10"), "AMC")}),
             finnhub=Finnhub({"AAA": [("2026-09-09", "AMC")]}))

    assert seen == [TransactionStatus.IDLE] * 3


@pg
def test_report_only_fetches_and_computes_but_writes_nothing(db):
    with connect() as conn:
        _known(conn, "AAA")
        _event(conn, "AAA", "2026-09-10")
        conn.commit()
        before = _moves(conn)
        fetch = FakeFetch()

        report = _run(conn, execute=False, fetch=fetch)

        assert [o.status for o in report.outcomes] == [rm.WOULD_RECORD]
        assert report.outcomes[0].move is not None
        assert len(fetch.calls) == 1
    with connect() as fresh:
        assert _moves(fresh) == before


@pg
def test_every_gate_failure_is_left_over_and_never_fetched(db):
    with connect() as conn:
        _known(conn, "HOL", "UNK", "CLU", "OK")
        _event(conn, "HOL", "2026-09-07", timing="BMO")        # Labor Day
        _event(conn, "UNK", "2026-09-02", timing="UNKNOWN")    # no Yahoo: never inferred
        _event(conn, "CLU", "2026-09-02", timing="AMC")
        _event(conn, "CLU", "2026-09-09", timing="UNKNOWN")    # its neighbour, also a candidate
        _event(conn, "OK", "2026-09-10", timing="AMC")
        conn.commit()
        fetch = FakeFetch()

        report = _run(conn, execute=True, fetch=fetch)

    by = {(o.candidate.ticker, o.candidate.earnings_date.isoformat()): o for o in report.outcomes}
    assert (by[("HOL", "2026-09-07")].status, by[("HOL", "2026-09-07")].kind) == (rm.LEFT, "session")
    assert (by[("UNK", "2026-09-02")].status, by[("UNK", "2026-09-02")].kind) == (rm.LEFT, "retag")
    assert by[("CLU", "2026-09-02")].kind == "cluster"
    assert by[("OK", "2026-09-10")].status == rm.RECORDED
    assert [c[0] for c in fetch.calls] == ["OK"]
    assert all(len(o.brief) <= rm.BRIEF_CHARS for o in report.leftovers())


@pg
def test_one_call_per_ticker_oldest_event_first_and_no_spacing_of_its_own(db):
    """Twelve Data's 8-a-minute limit and Yahoo's one a second are kept by the
    calls themselves (the process's vendor gates). The loop used to sleep 7.5 s
    between Twelve Data calls and 1.0 s between Yahoo calls as well, which would
    now stack on the gates' waits."""
    with connect() as conn:
        _known(conn, "AAA", "BBB", "CCC")
        _event(conn, "CCC", "2026-09-10")
        _event(conn, "AAA", "2026-09-03")
        _event(conn, "BBB", "2026-08-26")
        conn.commit()

        clock = {"t": 0.0}
        sleeps = []

        def sleep(s):
            sleeps.append(s)
            clock["t"] += s

        fetch = FakeFetch()
        yahoo = FakeYahoo({"AAA": (_d("2026-09-03"), "AMC"), "BBB": (_d("2026-08-26"), "AMC"),
                           "CCC": (_d("2026-09-10"), "AMC")})
        _run(conn, execute=False, fetch=fetch, corroborate=yahoo, sleep=sleep,
             clock=lambda: clock["t"])

    assert [c[0] for c in yahoo.calls] == ["BBB", "AAA", "CCC"]

    assert [c[0] for c in fetch.calls] == ["BBB", "AAA", "CCC"]
    assert sleeps == []
    assert fetch.calls[0][1:] == (_d("2026-08-16"), _d("2026-09-05"))   # event -10 .. +10


def test_the_scheduled_fetch_waits_on_the_twelve_data_gate(monkeypatch):
    from src.utils import rate_limiter
    from src.utils.rate_limiter import TWELVE_DATA

    class SpyGate:
        calls = 0

        def call(self, fn, retry_after):
            SpyGate.calls += 1
            return {"status": "ok", "values": []}

    monkeypatch.setattr(rate_limiter, "_gates", {TWELVE_DATA: SpyGate()})
    monkeypatch.setenv("TWELVE_DATA_KEY", "k")
    assert rm._twelve_data_fetch("AAA", _d("2026-09-01"), _d("2026-09-20")) == []
    assert SpyGate.calls == 1


@pg
def test_the_budget_stops_the_run_and_carries_the_rest_as_pending(db):
    with connect() as conn:
        _known(conn, "AAA", "BBB", "CCC")
        for t in ("AAA", "BBB", "CCC"):
            _event(conn, t, "2026-09-10")
        conn.commit()
        fetch = FakeFetch()

        report = _run(conn, execute=True, fetch=fetch, budget=1)

        assert len(fetch.calls) == 1
        statuses = {o.candidate.ticker: (o.status, o.kind) for o in report.outcomes}
        assert statuses == {"AAA": (rm.RECORDED, ""), "BBB": (rm.PENDING, "budget"),
                            "CCC": (rm.PENDING, "budget")}
        assert "budget" in report.stopped
        assert len(_moves(conn)) == 3 + 1


@pytest.mark.parametrize("exc", [
    em.TwelveDataRateLimited("You have run out of API credits for the day."),
])
@pg
def test_a_rate_limit_stops_the_run_and_nothing_after_it_is_fetched(db, exc):
    with connect() as conn:
        _known(conn, "AAA", "BBB", "CCC")
        _event(conn, "AAA", "2026-09-08")
        _event(conn, "BBB", "2026-09-09")
        _event(conn, "CCC", "2026-09-10")
        conn.commit()
        fetch = FakeFetch(raise_on="BBB", exc=exc)

        report = _run(conn, execute=True, fetch=fetch)

    assert [c[0] for c in fetch.calls] == ["AAA", "BBB"]
    statuses = {o.candidate.ticker: (o.status, o.kind) for o in report.outcomes}
    assert statuses == {"AAA": (rm.RECORDED, ""), "BBB": (rm.PENDING, "rate_limit"),
                        "CCC": (rm.PENDING, "rate_limit")}
    assert "credits" in report.stopped


@pg
def test_a_bad_symbol_is_left_over_and_a_network_error_waits_neither_stops_the_run(db):
    with connect() as conn:
        _known(conn, "AAA", "BAD", "NET")
        _event(conn, "BAD", "2026-09-08")
        _event(conn, "NET", "2026-09-09")
        _event(conn, "AAA", "2026-09-10")
        conn.commit()

        base = FakeFetch()

        def fetch(ticker, start, end):
            if ticker == "BAD":
                raise em.TwelveDataError("**symbol** not found: BAD")
            if ticker == "NET":
                raise requests.ConnectionError("connection reset")
            return base(ticker, start, end)

        report = _run(conn, execute=True, fetch=fetch)

    statuses = {o.candidate.ticker: (o.status, o.kind) for o in report.outcomes}
    assert statuses == {"BAD": (rm.LEFT, "api"), "NET": (rm.PENDING, "request"),
                        "AAA": (rm.RECORDED, "")}
    assert report.stopped is None


@pg
def test_the_runtime_limit_stops_before_the_next_call(db):
    with connect() as conn:
        _known(conn, "AAA", "BBB")
        _event(conn, "AAA", "2026-09-09")
        _event(conn, "BBB", "2026-09-10")
        conn.commit()
        clock = {"t": 0.0}

        def fetch(ticker, start, end):
            clock["t"] += 100.0      # a slow call
            return _bars_around("2026-09-10" if ticker == "BBB" else "2026-09-09")

        report = _run(conn, execute=True, fetch=fetch, clock=lambda: clock["t"],
                      max_runtime_sec=60)

    statuses = {o.candidate.ticker: (o.status, o.kind) for o in report.outcomes}
    assert statuses == {"AAA": (rm.RECORDED, ""), "BBB": (rm.PENDING, "deadline")}


@pg
def test_limit_and_tickers_narrow_the_run(db):
    with connect() as conn:
        _known(conn, "AAA", "BBB", "CCC")
        for t in ("AAA", "BBB", "CCC"):
            _event(conn, t, "2026-09-10")
        conn.commit()

        fetch = FakeFetch()
        report = _run(conn, execute=False, fetch=fetch, tickers=["CCC", "BBB"], limit=1)

    assert [c[0] for c in fetch.calls] == ["BBB"]
    statuses = {o.candidate.ticker: (o.status, o.kind) for o in report.outcomes}
    assert statuses == {"BBB": (rm.WOULD_RECORD, ""), "CCC": (rm.PENDING, "limit")}


# --- Yahoo corroboration ---------------------------------------------------------
#
# Built after the first report-only production run (2026-09-13): of the four events
# it would have recorded, DKS, TIGR and RY were stored as AMC with confirmed=true
# while Yahoo's reported timestamps read 07:00, 04:00 and 06:00 ET. Recording them
# as AMC measures the session AFTER the reaction -- a near-zero move that drags the
# VRP denominator down. A stored BMO/AMC tag is not evidence on its own.


@pg
def test_yahoo_agreeing_on_date_and_timing_lets_the_move_through_without_asking_finnhub(db):
    with connect() as conn:
        _known(conn, "AAA")
        _event(conn, "AAA", "2026-09-10", timing="AMC")
        conn.commit()
        yahoo = FakeYahoo({"AAA": (_d("2026-09-10"), "AMC")})
        finnhub = FakeFinnhub({})
        report = _run(conn, execute=True, corroborate=yahoo, finnhub=finnhub)
    assert [o.status for o in report.outcomes] == [rm.RECORDED]
    assert report.outcomes[0].retagged_from is None
    assert yahoo.calls == [("AAA", _d("2026-09-10"))]
    assert finnhub.calls == []
    assert report.yahoo is True


@pytest.mark.parametrize("answer, kind, words", [
    ((_d("2026-09-10"), "BMO"), "retag", "BMO"),       # contradicted, and Finnhub is off
    ((_d("2026-09-09"), "AMC"), "yahoo", "2026-09-09"),  # Yahoo files it a day earlier
    ((_d("2026-09-10"), "DMH"), "retag", "DMH"),       # an hour that says neither
    (rm.YahooNoData("No earnings_dates history for AAA"), "yahoo", "no earnings history"),
])
@pg
def test_yahoo_disagreeing_is_left_over_and_spends_no_twelve_data_call(db, answer, kind, words):
    with connect() as conn:
        _known(conn, "AAA")
        _event(conn, "AAA", "2026-09-10", timing="AMC")
        conn.commit()
        fetch = FakeFetch()
        report = _run(conn, execute=True, fetch=fetch, corroborate=FakeYahoo({"AAA": answer}))
        assert len(_moves(conn)) == 1
    [o] = report.outcomes
    assert (o.status, o.kind) == (rm.LEFT, kind)
    assert words in o.reason
    assert fetch.calls == []


@pg
def test_yahoo_unreachable_waits_for_the_next_run_it_never_writes_uncorroborated(db):
    with connect() as conn:
        _known(conn, "AAA", "BBB")
        _event(conn, "AAA", "2026-09-09")
        _event(conn, "BBB", "2026-09-10")
        conn.commit()
        fetch = FakeFetch()
        yahoo = FakeYahoo({"AAA": rm.YahooUnavailable("Yahoo Finance error for AAA: 429"),
                           "BBB": (_d("2026-09-10"), "AMC")})
        report = _run(conn, execute=True, fetch=fetch, corroborate=yahoo)
    statuses = {o.candidate.ticker: (o.status, o.kind) for o in report.outcomes}
    assert statuses == {"AAA": (rm.PENDING, "yahoo_unavailable"), "BBB": (rm.RECORDED, "")}
    assert [c[0] for c in fetch.calls] == ["BBB"]


@pg
def test_limit_counts_twelve_data_calls_not_tickers_yahoo_turned_away(db):
    with connect() as conn:
        _known(conn, "AAA", "BBB", "CCC")
        _event(conn, "AAA", "2026-09-08")
        _event(conn, "BBB", "2026-09-09")
        _event(conn, "CCC", "2026-09-10")
        conn.commit()
        fetch = FakeFetch()
        yahoo = FakeYahoo({"AAA": (_d("2026-09-07"), "BMO"), "BBB": (_d("2026-09-09"), "AMC"),
                           "CCC": (_d("2026-09-10"), "AMC")})
        report = _run(conn, execute=False, fetch=fetch, corroborate=yahoo, limit=1)
    statuses = {o.candidate.ticker: (o.status, o.kind) for o in report.outcomes}
    assert statuses == {"AAA": (rm.LEFT, "yahoo"), "BBB": (rm.WOULD_RECORD, ""),
                        "CCC": (rm.PENDING, "limit")}


def test_yahoo_report_near_maps_the_shared_yahoo_source():
    """The corroborator is YahooFinanceEarnings.get_earnings_date_near -- the one
    CLAUDE.md names for historical dates -- with its hour classification and
    placeholder correction. NODATA is a leftover; anything else is transient."""
    from src.domain.enums import EarningsTiming
    from src.domain.errors import AppError, Err, ErrorCode, Ok

    with patch.object(YahooFinanceEarnings, "get_earnings_date_near",
                      return_value=Ok((_d("2026-08-25"), EarningsTiming.BMO))) as near:
        assert rm.yahoo_report_near("DKS", _d("2026-08-25")) == (_d("2026-08-25"), "BMO")
        near.assert_called_once_with("DKS", [_d("2026-08-25")])

        near.return_value = Err(AppError(ErrorCode.NODATA, "none"))
        with pytest.raises(rm.YahooNoData):
            rm.yahoo_report_near("PVH", _d("2026-09-02"))

        near.return_value = Err(AppError(ErrorCode.EXTERNAL, "429"))
        with pytest.raises(rm.YahooUnavailable):
            rm.yahoo_report_near("PVH", _d("2026-09-02"))


# --- retag: Finnhub and Yahoo agree -> fix the calendar's timing, then record ----

def _retag_setup(conn, ticker="AAA", day="2026-09-10", stored="UNKNOWN", confirmed=False):
    _known(conn, ticker)
    _event(conn, ticker, day, timing=stored, confirmed=confirmed)
    conn.commit()


@pg
def test_report_only_would_retag_and_record_but_writes_nothing(db):
    with connect() as conn:
        _retag_setup(conn)
        calendar_before, moves_before = _calendar(conn), _moves(conn)
        fetch = FakeFetch()
        report = _run(conn, execute=False, fetch=fetch,
                      corroborate=FakeYahoo({"AAA": (_d("2026-09-10"), "AMC")}),
                      finnhub=FakeFinnhub({"AAA": [("2026-09-10", "AMC")]}))
    with connect() as fresh:
        assert _calendar(fresh) == calendar_before
        assert _moves(fresh) == moves_before
    [o] = report.outcomes
    assert (o.status, o.retagged_from, o.candidate.timing) == (rm.WOULD_RECORD, "UNKNOWN", "AMC")
    assert len(fetch.calls) == 1
    assert "would retag 1" in rm.format_summary(report)


@pg
def test_execute_retags_the_calendar_and_records_the_move_in_the_same_run(db):
    with connect() as conn:
        _retag_setup(conn)
        report = _run(conn, execute=True,
                      corroborate=FakeYahoo({"AAA": (_d("2026-09-10"), "AMC")}),
                      finnhub=FakeFinnhub({"AAA": [("2026-09-10", "AMC")]}))
        [o] = report.outcomes
        assert (o.status, o.retagged_from, o.candidate.timing) == (rm.RECORDED, "UNKNOWN", "AMC")
        row = conn.execute("SELECT timing, confirmed, updated_at, created_at, last_validated_at"
                           " FROM earnings_calendar WHERE ticker = 'AAA'"
                           " AND earnings_date = '2026-09-10'").fetchone()
        assert (row.timing, row.confirmed) == ("AMC", True)
        assert row.last_validated_at is not None and row.updated_at >= row.created_at
        new = [r for r in _moves(conn) if r.earnings_date == _d("2026-09-10")][0]
        # AMC: measured from the event day's close to the next session's open.
        assert round((new.earnings_open - new.prev_close) / new.prev_close * 100, 6) == 5.0


@pg
def test_a_stored_amc_that_both_sources_call_bmo_is_retagged_and_measured_as_bmo(db):
    """DKS 08-25, TIGR 08-26, RY 08-27, CGNT 09-09: stored AMC/confirmed, reported
    before the open. Measured as BMO: previous session's close -> event day's open."""
    with connect() as conn:
        _retag_setup(conn, stored="AMC", confirmed=True)
        report = _run(conn, execute=True,
                      corroborate=FakeYahoo({"AAA": (_d("2026-09-10"), "BMO")}),
                      finnhub=FakeFinnhub({"AAA": [("2026-09-10", "BMO")]}))
        [o] = report.outcomes
        assert (o.status, o.retagged_from, o.candidate.timing) == (rm.RECORDED, "AMC", "BMO")
        assert conn.execute("SELECT timing FROM earnings_calendar WHERE ticker = 'AAA'"
                            " AND earnings_date = '2026-09-10'").fetchone().timing == "BMO"
        new = [r for r in _moves(conn) if r.earnings_date == _d("2026-09-10")][0]
    bars = {b.date: b for b in FakeFetch()("AAA", _d("2026-08-31"), _d("2026-09-20"))}
    assert (new.prev_close, new.earnings_open) == (bars[_d("2026-09-09")].close,
                                                   bars[_d("2026-09-10")].open)


@pytest.mark.parametrize("yahoo, finnhub, words", [
    ((_d("2026-09-10"), "AMC"), [("2026-09-10", "UNKNOWN")], "hour blank"),
    ((_d("2026-09-10"), "AMC"), [("2026-09-10", "BMO")], "BMO"),
    ((_d("2026-09-10"), "AMC"), [("2026-09-09", "AMC")], "2026-09-09"),
    ((_d("2026-09-09"), "AMC"), [("2026-09-09", "AMC")], "2026-09-09"),   # both say another date
    ((_d("2026-09-10"), "AMC"), rm.FinnhubUnavailable("503 Server Error"), "503"),
    ((_d("2026-09-10"), "AMC"), rm.FinnhubNoData("No earnings found for AAA"), "no report"),
])
@pg
def test_no_retag_without_two_source_agreement_on_the_stored_date(db, yahoo, finnhub, words):
    with connect() as conn:
        _retag_setup(conn)
        calendar_before = _calendar(conn)
        fetch = FakeFetch()
        report = _run(conn, execute=True, fetch=fetch,
                      corroborate=FakeYahoo({"AAA": yahoo}), finnhub=FakeFinnhub({"AAA": finnhub}))
        assert _calendar(conn) == calendar_before
        assert len(_moves(conn)) == 1
    [o] = report.outcomes
    assert (o.status, o.retagged_from) == (rm.LEFT, None)
    assert words in o.reason
    assert fetch.calls == []


@pg
def test_a_retag_never_changes_a_date_or_touches_another_row(db):
    with connect() as conn:
        _known(conn, "AAA", "BBB")
        _event(conn, "AAA", "2026-09-10", timing="UNKNOWN", confirmed=False)
        _event(conn, "AAA", "2026-12-10", timing="UNKNOWN", confirmed=False)   # next quarter
        _event(conn, "BBB", "2026-09-10", timing="UNKNOWN", confirmed=False)   # Finnhub disagrees
        conn.commit()
        before = {(r.ticker, r.earnings_date): r for r in _calendar(conn)}

        _run(conn, execute=True,
             corroborate=FakeYahoo({"AAA": (_d("2026-09-10"), "AMC"),
                                    "BBB": (_d("2026-09-10"), "AMC")}),
             # Finnhub's window for BBB holds a report two days later: not the stored date.
             finnhub=FakeFinnhub({"AAA": [("2026-09-10", "AMC")], "BBB": [("2026-09-12", "AMC")]}))

        after = {(r.ticker, r.earnings_date): r for r in _calendar(conn)}
    assert set(after) == set(before)
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {("AAA", _d("2026-09-10"))}
    assert (after[("AAA", _d("2026-09-10"))].timing, after[("AAA", _d("2026-09-10"))].confirmed) == ("AMC", True)


@pg
def test_finnhub_is_asked_only_for_events_that_need_a_retag_at_least_1_1_seconds_apart(db):
    with connect() as conn:
        _known(conn, "AAA", "BBB", "CCC")
        _event(conn, "AAA", "2026-09-08", timing="UNKNOWN")
        _event(conn, "BBB", "2026-09-09", timing="AMC")       # Yahoo agrees: no Finnhub call
        _event(conn, "CCC", "2026-09-10", timing="DMH")
        conn.commit()
        clock = {"t": 0.0}

        def sleep(s):
            clock["t"] += s

        finnhub = FakeFinnhub({"AAA": [("2026-09-08", "BMO")], "CCC": [("2026-09-10", "AMC")]},
                              clock=lambda: clock["t"])
        report = _run(conn, execute=False, sleep=sleep, clock=lambda: clock["t"],
                      corroborate=FakeYahoo({"AAA": (_d("2026-09-08"), "BMO"),
                                             "BBB": (_d("2026-09-09"), "AMC"),
                                             "CCC": (_d("2026-09-10"), "AMC")}),
                      finnhub=finnhub)
    assert [c[0] for c in finnhub.calls] == ["AAA", "CCC"]
    assert finnhub.times[1] - finnhub.times[0] >= 1.1
    statuses = {o.candidate.ticker: (o.status, o.retagged_from) for o in report.outcomes}
    assert statuses == {"AAA": (rm.WOULD_RECORD, "UNKNOWN"), "BBB": (rm.WOULD_RECORD, None),
                        "CCC": (rm.WOULD_RECORD, "DMH")}


@pytest.mark.parametrize("why", ["rate limit", "auth refused (401)"])
@pg
def test_a_finnhub_rate_limit_or_auth_error_stops_retagging_for_the_run(db, why):
    with connect() as conn:
        _known(conn, "AAA", "BBB", "CCC", "DDD")
        _event(conn, "AAA", "2026-09-08", timing="UNKNOWN")
        _event(conn, "BBB", "2026-09-09", timing="UNKNOWN")
        _event(conn, "CCC", "2026-09-09", timing="AMC")       # corroborated: still recorded
        _event(conn, "DDD", "2026-09-10", timing="UNKNOWN")
        conn.commit()
        calendar_before = _calendar(conn)
        finnhub = FakeFinnhub({"AAA": rm.FinnhubStopped(why), "BBB": [("2026-09-09", "AMC")],
                               "DDD": [("2026-09-10", "AMC")]})
        report = _run(conn, execute=True, finnhub=finnhub,
                      corroborate=FakeYahoo({"AAA": (_d("2026-09-08"), "AMC"),
                                             "BBB": (_d("2026-09-09"), "AMC"),
                                             "CCC": (_d("2026-09-09"), "AMC"),
                                             "DDD": (_d("2026-09-10"), "AMC")}))
        assert _calendar(conn) == calendar_before
    assert [c[0] for c in finnhub.calls] == ["AAA"]
    statuses = {o.candidate.ticker: (o.status, o.kind) for o in report.outcomes}
    assert statuses == {"AAA": (rm.LEFT, "retag"), "BBB": (rm.LEFT, "retag"),
                        "CCC": (rm.RECORDED, ""), "DDD": (rm.LEFT, "retag")}
    assert why in report.retag_stopped
    assert "retag stopped" in rm.counts_line(report)


@pg
def test_a_retagged_event_must_still_have_a_complete_reaction_for_its_new_timing(db):
    """Stored BMO on Monday 09-14 is due on Tuesday; as AMC it reacts Tuesday, still
    trading. The retag is written; the move waits for the next run."""
    with connect() as conn:
        _retag_setup(conn, day="2026-09-14", stored="BMO", confirmed=True)
        fetch = FakeFetch()
        report = _run(conn, execute=True, fetch=fetch,
                      corroborate=FakeYahoo({"AAA": (_d("2026-09-14"), "AMC")}),
                      finnhub=FakeFinnhub({"AAA": [("2026-09-14", "AMC")]}))
        timing = conn.execute("SELECT timing FROM earnings_calendar WHERE ticker = 'AAA'"
                              " AND earnings_date = '2026-09-14'").fetchone().timing
    [o] = report.outcomes
    assert (o.status, o.kind, o.retagged_from) == (rm.PENDING, "not_due", "BMO")
    assert timing == "AMC"
    assert fetch.calls == []


@pg
def test_skip_yahoo_never_retags_unknown_timing(db):
    with connect() as conn:
        _retag_setup(conn)
        finnhub = FakeFinnhub({"AAA": [("2026-09-10", "AMC")]})
        report = _run(conn, execute=True, corroborate=None, finnhub=finnhub)
    [o] = report.outcomes
    assert (o.status, o.kind) == (rm.LEFT, "retag")
    assert finnhub.calls == []


def test_finnhub_report_near_asks_a_three_day_window_and_sorts_errors():
    from src.domain.enums import EarningsTiming
    from src.domain.errors import AppError, Err, ErrorCode, Ok

    listed = Ok([
        ("CGNT", _d("2026-09-09"), EarningsTiming.BMO), ("OTHER", _d("2026-09-09"), EarningsTiming.AMC)])
    with patch.object(FinnhubAPI, "get_earnings_calendar", return_value=listed) as calendar, \
         patch.dict("os.environ", {"FINNHUB_API_KEY": "k"}):
        assert rm.finnhub_report_near("CGNT", _d("2026-09-09")) == [(_d("2026-09-09"), "BMO")]
        calendar.assert_called_once_with(
            symbol="CGNT", from_date=_d("2026-09-06"), to_date=_d("2026-09-12"))

        for error, raised in [
            (AppError(ErrorCode.RATELIMIT, "Finnhub rate limit exceeded"), rm.FinnhubStopped),
            (AppError(ErrorCode.EXTERNAL, "401 Client Error: Unauthorized for url"), rm.FinnhubStopped),
            (AppError(ErrorCode.EXTERNAL, "403 Client Error: Forbidden for url"), rm.FinnhubStopped),
            (AppError(ErrorCode.NODATA, "No earnings found for CGNT"), rm.FinnhubNoData),
            (AppError(ErrorCode.EXTERNAL, "503 Server Error"), rm.FinnhubUnavailable),
            (AppError(ErrorCode.TIMEOUT, "Finnhub request timeout"), rm.FinnhubUnavailable),
        ]:
            calendar.return_value = Err(error)
            with pytest.raises(raised):
                rm.finnhub_report_near("CGNT", _d("2026-09-09"))


def test_finnhub_report_near_without_a_key_stops_retagging():
    with patch.dict("os.environ", {"FINNHUB_API_KEY": ""}):
        with pytest.raises(rm.FinnhubStopped):
            rm.finnhub_report_near("CGNT", _d("2026-09-09"))


# --- traded tickers --------------------------------------------------------------

@pg
def test_leftovers_are_marked_traded_from_strategies_or_the_journal(db):
    with connect() as conn:
        _known(conn, "STRAT", "JRNL", "NEVER")
        for t in ("STRAT", "JRNL", "NEVER"):
            _event(conn, t, "2026-09-07")                   # Labor Day: always left over
        _traded(conn, "STRAT", "strategies")
        _traded(conn, "JRNL", "trade_journal")
        conn.commit()
        report = _run(conn, execute=True)
    assert report.traded == frozenset({"STRAT", "JRNL"})
    assert sorted(o.candidate.ticker for o in report.traded_leftovers()) == ["JRNL", "STRAT"]


# --- output: summary, Telegram, Kuma -------------------------------------------

def _report(execute=True, traded=(), **counts):
    outcomes = []
    move = em.EarningsMove("RH", _d("2026-09-10"), em.EarningsTiming.AMC, 100, 105, 106, 104, 105.5,
                           5.0, 0.48, 5.5, 10, 20)
    for i in range(counts.get("recorded", 0)):
        outcomes.append(rm.Outcome(_cand(ticker=f"R{i}"), rm.RECORDED, move=move))
    for i in range(counts.get("retagged", 0)):
        outcomes.append(rm.Outcome(_cand(ticker=f"T{i}"), rm.RECORDED, move=move,
                                   retagged_from="UNKNOWN"))
    for i in range(counts.get("left", 0)):
        outcomes.append(rm.Outcome(_cand(ticker=f"L{i}", timing="UNKNOWN"), rm.LEFT,
                                   "Finnhub hour blank for 2026-09-10; Yahoo reads AMC -- no "
                                   "second source for the timing", "retag",
                                   short="Finnhub hour blank; Yahoo reads AMC"))
    for i in range(counts.get("pending", 0)):
        outcomes.append(rm.Outcome(_cand(ticker=f"P{i}"), rm.PENDING, "not yet", "not_due"))
    return rm.Report(TODAY, execute, outcomes, calls=1, budget=700, stopped=None,
                     traded=frozenset(traded))


def test_counts_line_is_recorded_retagged_left_and_traded():
    report = _report(recorded=1, retagged=2, left=3, pending=4, traded=("L0",))
    assert rm.counts_line(report) == "recorded 3 · retagged 2 · left 3, of which 1 traded"
    report.execute = False
    for o in report.outcomes:
        if o.status == rm.RECORDED:
            o.status = rm.WOULD_RECORD
    assert rm.counts_line(report) == "would record 3 · would retag 2 · left 3, of which 1 traded"


def test_summary_lists_every_row_traded_or_not_with_its_full_reason():
    text = rm.format_summary(_report(recorded=1, retagged=1, left=2, pending=1, traded=("L1",)))
    assert "recorded 2 · retagged 1 · left 2, of which 1 traded" in text
    for t in ("R0", "T0", "L0", "L1", "P0"):
        assert t in text
    assert "no second source for the timing" in text and "+5.00%" in text
    assert "UNKNOWN -> AMC" in text
    assert "[traded]" in text


def test_summary_says_when_yahoo_corroboration_was_off():
    report = _report(recorded=1)
    report.yahoo = False
    assert "Yahoo corroboration OFF" in rm.format_summary(report)
    report.yahoo = True
    assert "Yahoo corroboration on" in rm.format_summary(report)


@pytest.mark.parametrize("counts, traded, sent", [
    ({}, (), False),                                   # clean run
    ({"recorded": 3, "retagged": 2}, (), False),       # work done, nothing for a human
    ({"pending": 3}, (), False),
    ({"left": 5}, (), False),                          # untraded leftovers: the log only
    ({"left": 5}, ("L2",), True),
])
def test_telegram_only_when_a_traded_ticker_has_a_leftover(counts, traded, sent):
    sender = Mock()
    sender.send.return_value = True
    assert rm.notify(_report(traded=traded, **counts), sender) is sent
    assert sender.send.called is sent


def test_telegram_is_one_counts_line_then_only_the_traded_leftovers():
    sender = Mock()
    sender.send.return_value = True
    rm.notify(_report(recorded=4, retagged=1, left=3, traded=("L1",)), sender)
    lines = sender.send.call_args.args[0].split("\n")
    assert len(lines) == 2
    assert "record_moves 2026-09-15" in lines[0]
    assert "recorded 5 · retagged 1 · left 3, of which 1 traded" in lines[0]
    assert lines[1] == "L1 2026-09-10 UNKNOWN — Finnhub hour blank; Yahoo reads AMC"


def test_telegram_reason_is_brief_even_when_the_full_reason_is_long():
    report = _report(traded=("X",))
    report.outcomes.append(rm.Outcome(_cand(ticker="X"), rm.LEFT, "y" * 300, "api"))
    sender = Mock()
    rm.notify(report, sender)
    line = sender.send.call_args.args[0].split("\n")[1]
    reason = line.split(" — ", 1)[1]
    assert len(reason) <= rm.BRIEF_CHARS


def test_no_telegram_configured_is_a_skip_not_an_error():
    assert rm.notify(_report(left=1, traded=("L0",)), None) is False


def test_telegram_html_is_escaped():
    report = _report(traded=("X<Y",))
    report.outcomes.append(rm.Outcome(_cand(ticker="X<Y"), rm.LEFT, "reason <b>", "api",
                                      short="reason <b>"))
    sender = Mock()
    rm.notify(report, sender)
    body = sender.send.call_args.args[0]
    assert "X&lt;Y" in body and "reason &lt;b&gt;" in body


@pytest.mark.parametrize("status", ["up", "down"])
def test_kuma_push_sends_the_status_and_message_it_is_given(status, tmp_path):
    ca = tmp_path / "ca.crt"
    ca.write_text("pem")
    get = Mock()
    rm.push_kuma("https://kuma.internal/api/push/TOKEN?status=up&msg=OK&ping=", status, "m" * 500,
                 get=get, ca_path=str(ca))
    url = get.call_args.args[0]
    params = get.call_args.kwargs["params"]
    assert url == "https://kuma.internal/api/push/TOKEN?ping="
    assert params["status"] == status
    assert params["msg"] == "m" * 200
    assert get.call_args.kwargs["verify"] == str(ca)


def test_kuma_push_verifies_tls_with_system_roots_when_the_ca_is_absent(tmp_path):
    get = Mock()
    rm.push_kuma("https://kuma.internal/api/push/T", "up", "ok", get=get,
                 ca_path=str(tmp_path / "missing.crt"))
    assert get.call_args.kwargs["verify"] is True


def test_a_failed_kuma_push_does_not_fail_the_job():
    get = Mock(side_effect=requests.ConnectionError("down"))
    assert rm.push_kuma("https://kuma.internal/api/push/T", "up", "ok", get=get) is False


# --- CLI and runner integration ------------------------------------------------

@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("TWELVE_DATA_KEY", "k")
    monkeypatch.setenv("FINNHUB_API_KEY", "k")


class _Core:
    """record_moves() -- the job's core -- replaced, and each call's arguments and
    connection mode kept. The connection main() opens for it is real."""

    def __init__(self):
        self.result = _report(execute=False)
        self.calls = []

    def __call__(self, conn, today, **kwargs):
        self.calls.append(dict(kwargs, read_only=conn.read_only))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    @property
    def last(self):
        (call,) = self.calls
        return call


@pytest.fixture
def core(pg_schema, monkeypatch):
    fake = _Core()
    monkeypatch.setattr(rm, "record_moves", fake)
    return fake


def test_report_only_is_the_default_and_bypasses_the_runner(capsys, keys, core):
    """Like morning_digest's --dry-run: a preview must not claim the day, and it
    must not Telegram or push Kuma. Its connection is read-only, so a write
    reaching it would fail rather than land."""
    sender = Mock()
    core.result = _report(execute=False, left=1, traded=("L0",))
    with patch(f"{MOD}.run_job") as run_job, \
         patch(f"{MOD}.push_kuma") as push:
        rc = rm.main([], sender=sender)
    assert rc == 0
    run_job.assert_not_called()
    push.assert_not_called()
    sender.send.assert_not_called()
    assert (core.last["execute"], core.last["read_only"]) == (False, True)
    assert core.last["corroborate"] is rm.yahoo_report_near
    assert core.last["finnhub"] is rm.finnhub_report_near
    assert "REPORT ONLY" in capsys.readouterr().out


def test_skip_yahoo_is_an_explicit_flag_and_only_then_is_corroboration_off(keys, core):
    rm.main(["--skip-yahoo", "--tickers", "PVH"])
    assert core.last["corroborate"] is None
    assert core.last["tickers"] == ["PVH"]


def _fake_run_job(captured):
    def fake(name, fn, **kw):
        captured.update(name=name, **kw)
        captured["summary"] = fn()
        return 0
    return fake


@pytest.mark.parametrize("counts, traded", [
    ({}, ()),
    ({"recorded": 2, "left": 40}, ()),
    ({"left": 40}, ("L3",)),
])
def test_execute_pushes_kuma_up_with_the_counts_whatever_is_left_over(monkeypatch, keys, core,
                                                                     counts, traded):
    monkeypatch.setenv("RECORD_MOVES_PUSH_URL", "https://kuma.internal/api/push/T")
    sender = Mock()
    sender.send.return_value = True
    captured = {}
    report = core.result = _report(traded=traded, **counts)
    with patch(f"{MOD}.run_job", side_effect=_fake_run_job(captured)), \
         patch(f"{MOD}.push_kuma") as push:
        rc = rm.main(["--execute", "--force"], sender=sender)

    assert rc == 0
    assert captured["name"] == "record-moves" and captured["force"] is True
    assert captured["sender"] is sender
    assert "push_url" not in captured or captured["push_url"] is None
    assert core.last["execute"] is True and not core.last["read_only"]   # server default
    assert core.last["corroborate"] is rm.yahoo_report_near
    push.assert_called_once()
    assert push.call_args.args[1:] == ("up", rm.counts_line(report))
    assert captured["summary"] == rm.counts_line(report)
    assert sender.send.called is bool(traded)


def test_a_job_exception_telegrams_once_and_does_not_push_kuma(monkeypatch, keys, core):
    """One failure, one alert. run_job Telegrams the exception; a Kuma down push on
    top of it sent a second message for the same event (the user asked for one,
    2026-09-13). Kuma keeps the case the job cannot report itself: no run at all
    for the monitor's 4-day interval."""
    monkeypatch.setenv("RECORD_MOVES_PUSH_URL", "https://kuma.internal/api/push/T")
    sender = Mock()
    core.result = RuntimeError("database\nunreachable")
    with patch("scripts.jobs.runner._claim", return_value=True), \
         patch("scripts.jobs.runner._set_status") as set_status, \
         patch(f"{MOD}.push_kuma") as push:
        rc = rm.main(["--execute"], sender=sender)

    assert rc == 1
    assert set_status.call_args.args[2] == "error"
    sender.send_error.assert_called_once()
    assert "unreachable" in sender.send_error.call_args.args[0]
    push.assert_not_called()
    sender.send.assert_not_called()


@pytest.mark.parametrize("missing", ["TWELVE_DATA_KEY", "FINNHUB_API_KEY"])
def test_execute_without_a_key_fails_inside_the_job_so_the_runner_reports_it(monkeypatch, keys,
                                                                              missing):
    """On the server a missing key is a broken deployment: it must reach Telegram as a
    job error, not exit quietly before the claim."""
    monkeypatch.delenv(missing)
    outcome = {}

    def fake_run_job(name, fn, **kw):
        with pytest.raises(RuntimeError, match=missing):
            fn()
        outcome["ran"] = True
        return 1

    with patch(f"{MOD}.run_job", side_effect=fake_run_job), patch(f"{MOD}.connect") as conn:
        assert rm.main(["--execute"], sender=Mock()) == 1
    assert outcome == {"ran": True}
    conn.assert_not_called()


def test_force_without_execute_is_refused():
    with pytest.raises(SystemExit):
        rm.main(["--force"])


def test_dry_run_is_not_an_alias():
    with pytest.raises(SystemExit):
        rm.main(["--dry-run"])


@pytest.mark.parametrize("missing", ["TWELVE_DATA_KEY", "FINNHUB_API_KEY"])
def test_a_missing_key_fails_report_only_before_any_database_work(monkeypatch, keys, capsys, missing):
    monkeypatch.delenv(missing)
    with patch(f"{MOD}.connect") as conn:
        rc = rm.main([])
    assert rc == 1
    conn.assert_not_called()
    assert missing in capsys.readouterr().err


def test_the_job_deadline_leaves_room_inside_the_systemd_start_timeout():
    """trading-record-moves.service kills the job at TimeoutStartSec=1800, and its
    ExecStartPre DNS wait can take 60s of that. A job killed by systemd sends no
    Telegram and no Kuma push; one that stops at its own deadline reports. The
    last Twelve Data fetch can start just inside the deadline and take the
    gate's whole wait budget plus an HTTP timeout per attempt."""
    from src.utils import rate_limiter

    last_fetch = (rate_limiter.TWELVE_DATA_WAIT_BUDGET_SEC
                  + rate_limiter.TWELVE_DATA_ATTEMPTS * em.TWELVE_DATA_TIMEOUT_SEC)
    assert rm.DEFAULT_MAX_MINUTES * 60 + 60 + last_fetch + 120 <= 1800
