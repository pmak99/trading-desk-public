"""Twelve Data's time_series: the one request behind every Twelve Data fetch.

Basic (free) plan: 8 API credits a minute, reset at the start of each minute,
800 a day. Until 2026-09-14 each caller paced itself -- record_moves 7.5 s a
call, backfill_historical 8 s a ticker, TACO's fallback not at all -- and a 429
was never retried: record_moves stopped its run at the first one, even when only
the minute had run out because another process shared the key.

Every test here runs on the conftest's fake gate clock: nothing sleeps.
"""

import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.infrastructure.api import twelve_data as td  # noqa: E402
from src.utils import rate_limiter  # noqa: E402
from src.utils.rate_limiter import TWELVE_DATA, vendor_gate  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
KEY = "k-not-in-urls"
OK = {"status": "ok", "values": []}
MINUTE = {"code": 429, "status": "error",
          "message": "You have run out of API credits for the current minute. 9 API credits "
                     "were used, with the current limit being 8."}
DAY = {"code": 429, "status": "error",
       "message": "You have run out of API credits for the day. 801 API credits were used, "
                  "with the current limit being 800."}


def _response(payload, status=200, headers=None):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = dict(headers or {})
    resp.json.return_value = payload
    if status >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(f"{status} error", response=resp)
    else:
        resp.raise_for_status = MagicMock()
    return resp


def _serve(clock, *responses):
    """A fake requests.get answering in order (the last repeats), stamping each call."""
    stamps = []

    def get(url, params=None, headers=None, timeout=None, **_):
        stamps.append(clock.now)
        return responses[min(len(stamps), len(responses)) - 1]

    return get, stamps


def _max_in_any_window(stamps, width):
    worst, j = 0, 0
    for i, t in enumerate(stamps):
        while stamps[j] <= t - width:
            j += 1
        worst = max(worst, i - j + 1)
    return worst


def test_never_more_than_8_requests_in_any_60_seconds(vendor_clock):
    get, stamps = _serve(vendor_clock, _response(OK))
    with patch.object(td.requests, "get", get):
        for _ in range(30):
            td.time_series({"symbol": "RH"}, KEY, timeout=30)
            vendor_clock.sleep(0.5)

    assert len(stamps) == 30
    assert _max_in_any_window(stamps, 60.0) <= 8


def test_an_http_429_waits_for_retry_after_then_retries(vendor_clock):
    get, stamps = _serve(vendor_clock, _response(MINUTE, 429, {"Retry-After": "7"}), _response(OK))
    with patch.object(td.requests, "get", get):
        assert td.time_series({"symbol": "RH"}, KEY, timeout=30) == OK

    assert len(stamps) == 2
    assert stamps[1] - stamps[0] == pytest.approx(7.0)


def test_a_minute_limit_without_retry_after_waits_into_the_next_minute(vendor_clock):
    get, stamps = _serve(vendor_clock, _response(MINUTE), _response(OK))
    with patch.object(td.requests, "get", get), patch.object(td.time, "time", return_value=165.0):
        assert td.time_series({"symbol": "RH"}, KEY, timeout=30) == OK

    # 165 s is 45 s into a minute; the credits reset at 180 s.
    assert stamps[1] - stamps[0] == pytest.approx(16.0)


@pytest.mark.parametrize("now,wait", [(120.0, 61.0), (165.0, 16.0), (179.5, 1.5)])
def test_seconds_to_next_minute(now, wait):
    assert td.seconds_to_next_minute(now) == pytest.approx(wait)


@pytest.mark.parametrize("response", [_response(DAY), _response(DAY, 429),
                                      _response({**DAY, "code": 403})])
def test_the_days_credits_running_out_is_not_retried(vendor_clock, response):
    get, stamps = _serve(vendor_clock, response)
    with patch.object(td.requests, "get", get):
        with pytest.raises(td.TwelveDataRateLimited, match="for the day") as err:
            td.time_series({"symbol": "RH"}, KEY, timeout=30)

    assert len(stamps) == 1
    assert err.value.daily


def test_a_limit_that_never_clears_raises_rate_limited_within_the_budget(vendor_clock):
    get, stamps = _serve(vendor_clock, _response({}, 429))
    start = vendor_clock.now
    with patch.object(td.requests, "get", get), patch.object(td.time, "time", return_value=120.0):
        with pytest.raises(td.TwelveDataRateLimited):
            td.time_series({"symbol": "RH"}, KEY, timeout=30)

    gate = vendor_gate(TWELVE_DATA)
    assert len(stamps) == gate.attempts
    assert vendor_clock.now - start <= gate.budget


def test_a_5xx_is_retried_and_a_lasting_one_raises_the_http_error(vendor_clock):
    get, stamps = _serve(vendor_clock, _response({}, 503), _response(OK))
    with patch.object(td.requests, "get", get):
        assert td.time_series({"symbol": "RH"}, KEY, timeout=30) == OK
    assert len(stamps) == 2

    get, stamps = _serve(vendor_clock, _response({}, 502))
    with patch.object(td.requests, "get", get):
        with pytest.raises(requests.HTTPError):
            td.time_series({"symbol": "RH"}, KEY, timeout=30)
    assert len(stamps) == vendor_gate(TWELVE_DATA).attempts


def test_other_errors_are_not_retried(vendor_clock):
    body = {"code": 400, "status": "error", "message": "**symbol** not found: ZZZZ"}
    get, stamps = _serve(vendor_clock, _response(body))
    with patch.object(td.requests, "get", get):
        with pytest.raises(td.TwelveDataError, match="not found") as err:
            td.time_series({"symbol": "ZZZZ"}, KEY, timeout=30)
    assert not isinstance(err.value, td.TwelveDataRateLimited)
    assert len(stamps) == 1


def test_no_slot_in_time_is_a_rate_limit_so_a_batch_caller_stops(vendor_clock):
    gate = vendor_gate(TWELVE_DATA)
    gate._paused_until = vendor_clock.now + 10 * gate.budget
    get, stamps = _serve(vendor_clock, _response(OK))
    with patch.object(td.requests, "get", get):
        with pytest.raises(td.TwelveDataRateLimited, match="no call slot"):
            td.time_series({"symbol": "RH"}, KEY, timeout=30)
    assert stamps == []


def test_this_hosts_daily_ceiling_is_the_days_limit_and_sends_nothing(vendor_clock, monkeypatch, tmp_path):
    """Refused like Twelve Data's own "run out of API credits for the day": daily,
    so record_moves stops its run and keeps the rest pending."""
    counter = rate_limiter.DailyCallCounter(tmp_path / "calls", ceiling=1)
    monkeypatch.setattr(rate_limiter, "_gates", {TWELVE_DATA: rate_limiter.create_twelve_data_gate(
        clock=vendor_clock.time, sleep=vendor_clock.sleep, daily=counter)})
    get, stamps = _serve(vendor_clock, _response(OK))
    with patch.object(td.requests, "get", get):
        assert td.time_series({"symbol": "RH"}, KEY, timeout=30) == OK
        with pytest.raises(td.TwelveDataRateLimited, match="1 calls") as err:
            td.time_series({"symbol": "RH"}, KEY, timeout=30)
    assert err.value.daily
    assert len(stamps) == 1


# --- every Twelve Data request goes through the gate ----------------------------

class SpyGate:
    def __init__(self):
        self.calls = 0

    def call(self, fn, retry_after):
        self.calls += 1
        return fn()


def test_no_other_module_calls_twelve_data_directly():
    offenders = []
    for root in ("2.0/src", "2.0/scripts", "scripts", "common"):
        for path in (REPO / root).rglob("*.py"):
            if {"venv", "tests"} & set(path.parts) or path.name == "twelve_data.py":
                continue
            for i, line in enumerate(path.read_text().splitlines(), 1):
                if re.search(r"api\.twelvedata\.com", line):
                    offenders.append(f"{path.relative_to(REPO)}:{i}: {line.strip()}")
    assert offenders == []


def test_earnings_moves_and_taco_fetch_through_the_gate(monkeypatch):
    from scripts import earnings_moves as em
    from scripts.taco import cross_asset

    spy = SpyGate()
    monkeypatch.setattr(rate_limiter, "_gates", {TWELVE_DATA: spy})
    with patch.object(td.requests, "get", return_value=_response(OK)) as get:
        em.fetch_twelve_data_prices("RH", api_key=KEY)
        em.get_twelve_data_prices("RH", api_key=KEY)
        cross_asset.fetch_twelvedata("TLT", KEY)

    assert get.call_count == 3
    assert spy.calls == 3
