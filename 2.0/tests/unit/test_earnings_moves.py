"""scripts/earnings_moves.py -- the one Twelve Data fetch, move calculator and
insert-only move writer that backfill_historical.py and jobs/record_moves.py share.

The writer is the part a scheduled job leans on: it must add a quarter that is
missing and do nothing else. backfill_historical's save_to_database upserts the
calendar (writing its timing inference as confirmed) and overwrites prices; the
insert-only path touches neither.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import requests

from common.db import connect
from common.pgtesting import baseline_ddl
from scripts import earnings_moves as em

KEY = "k-not-in-urls"


def _response(payload, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status} error")
    else:
        resp.raise_for_status = MagicMock()
    return resp


BARS = {"status": "ok", "values": [
    {"datetime": "2026-09-11", "open": "101", "high": "104", "low": "99", "close": "103", "volume": "900"},
    {"datetime": "2026-09-10", "open": "98", "high": "100", "low": "97", "close": "100", "volume": "500"},
]}


def test_backfill_historical_uses_the_shared_core():
    """One implementation: backfill_historical re-exports the core's objects
    rather than carrying a second copy that could drift."""
    import scripts.backfill_historical as bh

    assert bh.get_twelve_data_prices is em.get_twelve_data_prices
    assert bh.calculate_earnings_move is em.calculate_earnings_move
    assert bh.EarningsEvent is em.EarningsEvent
    assert bh.EarningsTiming is em.EarningsTiming


def test_fetch_parses_bars_newest_first_and_sends_the_key_as_a_header():
    with patch.object(em.requests, "get", return_value=_response(BARS)) as get:
        prices = em.fetch_twelve_data_prices("RH", date(2026, 8, 31), date(2026, 9, 20), api_key=KEY)

    assert [p.date for p in prices] == [date(2026, 9, 11), date(2026, 9, 10)]
    assert prices[1].close == 100.0 and prices[0].volume == 900
    kwargs = get.call_args.kwargs
    assert kwargs["headers"] == {"Authorization": f"apikey {KEY}"}
    assert KEY not in str(get.call_args.args) + str(kwargs["params"])
    assert kwargs["params"]["start_date"] == "2026-08-31"
    assert kwargs["params"]["end_date"] == "2026-09-20"


@pytest.mark.parametrize("response", [
    _response({}, status=429),
    _response({"code": 429, "status": "error",
               "message": "You have run out of API credits for the current minute."}),
    _response({"code": 403, "status": "error",
               "message": "You have run out of API credits for the day."}),
])
def test_rate_limit_and_exhausted_credits_raise_rate_limited(response):
    with patch.object(em.requests, "get", return_value=response):
        with pytest.raises(em.TwelveDataRateLimited):
            em.fetch_twelve_data_prices("RH", api_key=KEY)


def test_an_http_429_carries_the_body_message_so_minute_and_day_limits_can_be_told_apart():
    body = {"code": 429, "status": "error",
            "message": "You have run out of API credits for the day. 801 API credits were used."}
    with patch.object(em.requests, "get", return_value=_response(body, status=429)):
        with pytest.raises(em.TwelveDataRateLimited, match="for the day"):
            em.fetch_twelve_data_prices("RH", api_key=KEY)


def test_other_api_errors_raise_twelve_data_error_not_rate_limited():
    body = {"code": 400, "status": "error", "message": "**symbol** not found: ZZZZ"}
    with patch.object(em.requests, "get", return_value=_response(body)):
        with pytest.raises(em.TwelveDataError) as err:
            em.fetch_twelve_data_prices("ZZZZ", api_key=KEY)
    assert not isinstance(err.value, em.TwelveDataRateLimited)
    assert "not found" in str(err.value)


def test_fetch_refuses_to_run_without_a_key():
    with patch.object(em.requests, "get") as get:
        with pytest.raises(em.TwelveDataError, match="TWELVE_DATA_KEY"):
            em.fetch_twelve_data_prices("RH", api_key="")
    get.assert_not_called()


def _always(answer):
    """requests.get that gives the same answer however often it is retried."""
    def get(*args, **kwargs):
        if isinstance(answer, Exception):
            raise answer
        return answer
    return get


@pytest.mark.parametrize("answer", [
    _response({}, status=429),
    _response({"status": "error", "message": "boom"}),
    requests.ConnectionError("down"),
])
def test_lenient_wrapper_keeps_backfill_behaviour_empty_list_on_any_failure(answer):
    with patch.object(em.requests, "get", side_effect=_always(answer)):
        assert em.get_twelve_data_prices("RH", api_key=KEY) == []


# --- insert-only writer -------------------------------------------------------

pg = pytest.mark.pg


@pytest.fixture
def db(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("historical_moves", "earnings_calendar"))
        conn.commit()
    return pg_schema


def test_a_report_dated_today_in_new_york_waits_for_its_reaction_on_a_utc_host(monkeypatch):
    """At 21:00 ET a UTC host's date is already tomorrow. Read from the host clock
    (until 2026-09-14), the same-day guard let today's UNKNOWN report through, and
    with no next session to infer from it took the BMO path: the report day's own
    session was returned as the reaction of a report that may not have happened."""
    from datetime import datetime, timezone

    import common.timezone as tz

    class UtcHost(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc)
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    monkeypatch.setattr(tz, "datetime", UtcHost)
    bars = [em.DailyPrice(date(2026, 9, 11), 100, 101, 99, 100, 1000),
            em.DailyPrice(date(2026, 9, 14), 100, 104, 99, 103, 1000)]
    for timing in (em.EarningsTiming.UNKNOWN, em.EarningsTiming.AMC):
        event = em.EarningsEvent("RH", date(2026, 9, 14), timing)
        assert em.calculate_earnings_move(event, bars) is None


def _move(ticker="RH", day=date(2026, 9, 10), prev=100.0, open_=103.0, close=106.0):
    return em.EarningsMove(
        ticker=ticker, earnings_date=day, timing=em.EarningsTiming.AMC,
        prev_close=prev, reaction_open=open_, reaction_high=107.0, reaction_low=102.0,
        reaction_close=close, gap_move_pct=0.0, intraday_move_pct=0.0, close_move_pct=0.0,
        volume_before=500, volume_reaction=900)


@pg
def test_insert_move_if_absent_writes_prices_and_the_generated_moves_follow(db):
    with connect() as conn:
        assert em.insert_move_if_absent(conn, _move()) is True
        conn.commit()
        row = conn.execute(
            "SELECT prev_close, earnings_open, earnings_close, volume_before, volume_earnings,"
            " round(gap_move_pct::numeric, 2) AS gap, round(close_move_pct::numeric, 2) AS close"
            " FROM historical_moves").fetchone()
    assert (row.prev_close, row.earnings_open, row.earnings_close) == (100.0, 103.0, 106.0)
    assert (row.volume_before, row.volume_earnings) == (500, 900)
    assert (row.gap, row.close) == (3.0, 6.0)


@pg
def test_insert_move_if_absent_never_rewrites_an_existing_row(db):
    with connect() as conn:
        conn.execute(
            "INSERT INTO historical_moves (ticker, earnings_date, prev_close, earnings_open,"
            " earnings_high, earnings_low, earnings_close, pre_earnings_straddle_pct, ern_iv_effect)"
            " VALUES ('RH', '2026-09-10', 50, 51, 52, 49, 50.5, 7.5, 1.2)")
        conn.commit()
        before = conn.execute("SELECT * FROM historical_moves").fetchall()

        assert em.insert_move_if_absent(conn, _move()) is False
        conn.commit()
        assert conn.execute("SELECT * FROM historical_moves").fetchall() == before


@pg
def test_insert_move_if_absent_never_touches_the_calendar(db):
    with connect() as conn:
        conn.execute("INSERT INTO earnings_calendar (ticker, earnings_date, timing, confirmed)"
                     " VALUES ('RH', '2026-09-10', 'UNKNOWN', false)")
        conn.commit()
        before = conn.execute("SELECT * FROM earnings_calendar").fetchall()

        em.insert_move_if_absent(conn, _move())
        em.insert_move_if_absent(conn, _move(ticker="NEW"))
        conn.commit()
        assert conn.execute("SELECT * FROM earnings_calendar").fetchall() == before


# --- the reaction session must have closed before its bar is a move -------------

def _bar(day, o, c):
    return em.DailyPrice(date=date.fromisoformat(day), open=o, high=max(o, c) + 1,
                         low=min(o, c) - 1, close=c, volume=1000)


def _at(monkeypatch, day, hh, mm):
    from datetime import datetime

    from common.timezone import MARKET_TZ
    now = datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}").replace(tzinfo=MARKET_TZ)
    monkeypatch.setattr(em, "now_et", lambda: now)


# Thu 2026-09-10 is the reaction day in each case; its bar is what a daytime
# Twelve Data fetch returns while the session is still trading.
SEPT = [_bar("2026-09-08", 100, 100), _bar("2026-09-09", 100, 100), _bar("2026-09-10", 104, 107)]


@pytest.mark.parametrize("timing, report", [
    (em.EarningsTiming.BMO, "2026-09-10"),        # reacts on the report date
    (em.EarningsTiming.AMC, "2026-09-09"),        # reacts the next session
    (em.EarningsTiming.UNKNOWN, "2026-09-09"),    # waits for the later of the two
])
def test_a_reaction_session_still_trading_at_1559_et_is_not_a_move(monkeypatch, timing, report):
    """The only guard was `earnings_date >= today`: a BMO report was measured on
    its own still-open session, and an AMC report on the next day's."""
    event = em.EarningsEvent("RH", date.fromisoformat(report), timing)

    _at(monkeypatch, "2026-09-10", 15, 59)
    assert em.calculate_earnings_move(event, SEPT) is None

    _at(monkeypatch, "2026-09-10", 16, 1)
    move = em.calculate_earnings_move(event, SEPT)
    assert move is not None and move.reaction_close == 107


def test_an_early_close_ends_the_reaction_session_at_1300_et(monkeypatch):
    """AMC Wed 2026-11-25 reacts Fri 11-27 (Thanksgiving closed), a 13:00 close."""
    prices = [_bar("2026-11-24", 50, 50), _bar("2026-11-25", 50, 50), _bar("2026-11-27", 53, 52)]
    event = em.EarningsEvent("RH", date(2026, 11, 25), em.EarningsTiming.AMC)

    _at(monkeypatch, "2026-11-27", 12, 59)
    assert em.calculate_earnings_move(event, prices) is None

    _at(monkeypatch, "2026-11-27", 13, 1)
    move = em.calculate_earnings_move(event, prices)
    assert move is not None and move.reaction_close == 52


def test_the_holiday_before_an_amc_reaction_is_not_its_session(monkeypatch):
    """At 16:01 on Thanksgiving no session has closed since the Wednesday report."""
    prices = [_bar("2026-11-24", 50, 50), _bar("2026-11-25", 50, 50)]
    event = em.EarningsEvent("RH", date(2026, 11, 25), em.EarningsTiming.AMC)

    _at(monkeypatch, "2026-11-26", 16, 1)
    assert em.calculate_earnings_move(event, prices) is None
