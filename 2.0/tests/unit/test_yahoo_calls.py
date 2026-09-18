"""Every yfinance request goes through `yahoo_call` -- the process's Yahoo gate.

Until 2026-09-14 no yfinance call had a limit or a backoff. The scan's ticker-info
lookup slept 0.2 s under a lock, record_moves and resolve_ambiguous_moves spaced
their Yahoo calls 1.0 s apart, and everything else -- the calendar sync's Yahoo
fallback, the bias validator, harvest, TACO, the Sunday IV tracker -- looped with
no spacing. A YFRateLimitError was logged as that ticker's failure and the loop
ran straight into the same limit.

yfinance is lazy: `yf.Ticker(symbol)` asks Yahoo nothing; `.history()`, `.info`,
`.calendar`, `.earnings_dates`, `.fast_info.last_price` and `yf.download()` do.
The fake below records each of those and whether it happened inside the gate.
"""

import re
import sys
import types
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from yfinance.exceptions import YFRateLimitError

from src.utils import rate_limiter  # noqa: E402
from src.utils.rate_limiter import YAHOO, vendor_gate  # noqa: E402

REPO = Path(__file__).resolve().parents[3]


class SpyGate:
    def __init__(self):
        self.calls = 0
        self.inside = False

    def call(self, fn, retry_after):
        self.calls += 1
        self.inside = True
        try:
            return fn()
        finally:
            self.inside = False


def _history_frame():
    idx = pd.DatetimeIndex([pd.Timestamp("2026-09-10", tz="America/New_York"),
                            pd.Timestamp("2026-09-11", tz="America/New_York")])
    return pd.DataFrame({"Open": [99.0, 100.0], "Close": [100.0, 101.0]}, index=idx)


def _earnings_frame():
    idx = pd.DatetimeIndex([pd.Timestamp(date.today() + timedelta(days=10), tz="America/New_York")
                            + pd.Timedelta(hours=16)])
    return pd.DataFrame({"Reported EPS": [float("nan")]}, index=idx)


class FakeYfinance(types.ModuleType):
    """Stands in for the yfinance module. Records every request, and every request
    made outside the gate -- recorded, not raised, because the call sites catch
    every exception."""

    def __init__(self, gate, answers=None):
        super().__init__("yfinance")
        self.gate, self.requests, self.outside = gate, [], []
        self.answers = answers or {}
        fake = self

        def ask(what, default):
            fake.requests.append(what)
            if not getattr(fake.gate, "inside", True):
                fake.outside.append(what)
            answer = fake.answers.get(what, default)
            if callable(answer):
                answer = answer()
            if isinstance(answer, Exception):
                raise answer
            return answer

        class Ticker:
            def __init__(self, symbol, *args, **kwargs):
                self.symbol = symbol

            @property
            def calendar(self):
                return ask("calendar", {"Earnings Date": [date.today() + timedelta(days=10)]})

            @property
            def earnings_dates(self):
                return ask("earnings_dates", _earnings_frame())

            @property
            def info(self):
                return ask("info", {"marketCap": 5e9, "shortName": "Fake Inc."})

            @property
            def fast_info(self):
                return ask("fast_info", types.SimpleNamespace(last_price=101.0))

            def history(self, *args, **kwargs):
                return ask("history", _history_frame())

        def download(*args, **kwargs):
            return ask("download", pd.DataFrame(
                {("Close", "^VIX"): [15.0], ("Close", "^VIX3M"): [17.0]}))

        self.Ticker = Ticker
        self.download = download


def _yahoo_earnings(monkeypatch, fake):
    from src.infrastructure.data_sources import yahoo_finance_earnings as yfe
    monkeypatch.setattr(yfe, "yf", fake)
    return yfe.YahooFinanceEarnings()


def _market_data(monkeypatch, fake):
    from scripts.scan import market_data
    monkeypatch.setattr(market_data, "yf", fake)
    monkeypatch.setattr(market_data, "YFINANCE_AVAILABLE", True)
    monkeypatch.setattr(market_data, "_ticker_info_cache", {})
    return market_data


def _lazy(monkeypatch, fake):
    monkeypatch.setitem(sys.modules, "yfinance", fake)


SITES = {
    "YahooFinanceEarnings.get_next_earnings_date": (
        lambda mp, fake: _yahoo_earnings(mp, fake).get_next_earnings_date("AAA"),
        {"calendar", "earnings_dates"}),
    "YahooFinanceEarnings.get_earnings_date_near": (
        lambda mp, fake: _yahoo_earnings(mp, fake).get_earnings_date_near("AAA", [date.today()]),
        {"earnings_dates"}),
    "scan.market_data.get_ticker_info": (
        lambda mp, fake: _market_data(mp, fake).get_ticker_info("AAA"),
        {"info"}),
    "scan.workflows.vix._check_vix_term_structure": (
        lambda mp, fake: (_lazy(mp, fake),
                          __import__("scripts.scan.workflows.vix", fromlist=["x"])
                          ._check_vix_term_structure()),
        {"download"}),
    "scan.harvest_live._fetch_closes_yf": (
        lambda mp, fake: (_lazy(mp, fake),
                          __import__("scripts.scan.harvest_live", fromlist=["x"])
                          ._fetch_closes_yf("SPY", "3mo")),
        {"history"}),
    "track_iv_weekly._dated_closes": (
        lambda mp, fake: (_lazy(mp, fake),
                          __import__("scripts.track_iv_weekly", fromlist=["x"])
                          ._dated_closes("SPY", date(2026, 9, 1), date(2026, 9, 11))),
        {"history"}),
    "backfill_historical.discover_earnings_from_yahoo": (
        lambda mp, fake: (_lazy(mp, fake), fake.answers.setdefault("earnings_dates", pd.DataFrame()),
                          __import__("scripts.backfill_historical", fromlist=["x"])
                          .discover_earnings_from_yahoo("AAA")),
        {"earnings_dates"}),
    "taco.market_data.fetch_series": (
        lambda mp, fake: (_lazy(mp, fake),
                          __import__("scripts.taco.market_data", fromlist=["x"])
                          .fetch_series("^GSPC")),
        {"history"}),
    "research.vix_adaptive_thresholds.load_vix_closes": (
        lambda mp, fake: (_lazy(mp, fake),
                          __import__("scripts.research.vix_adaptive_thresholds", fromlist=["x"])
                          .load_vix_closes(date(2026, 9, 1), date(2026, 9, 11))),
        {"history"}),
    "taco.market_data.fetch_intraday_quote": (
        lambda mp, fake: (_lazy(mp, fake),
                          __import__("scripts.taco.market_data", fromlist=["x"])
                          .fetch_intraday_quote("^GSPC")),
        {"fast_info"}),
}


@pytest.mark.parametrize("site", sorted(SITES))
def test_every_yahoo_request_is_made_inside_the_gate(monkeypatch, site):
    run, expected = SITES[site]
    spy = SpyGate()
    monkeypatch.setattr(rate_limiter, "_gates", {YAHOO: spy})
    fake = FakeYfinance(spy)

    run(monkeypatch, fake)

    assert set(fake.requests) == expected
    assert fake.outside == []
    assert spy.calls == len(fake.requests)


# The modules that import yfinance, and so must be in SITES above. A new one
# fails here until its requests go through yahoo_call and a SITES entry says so.
YFINANCE_IMPORTERS = {
    "2.0/src/infrastructure/data_sources/yahoo_finance_earnings.py",
    "2.0/scripts/scan/market_data.py",
    "2.0/scripts/scan/workflows/vix.py",
    "2.0/scripts/scan/harvest_live.py",
    "2.0/scripts/track_iv_weekly.py",
    "2.0/scripts/backfill_historical.py",
    "2.0/scripts/taco/market_data.py",
    "2.0/scripts/research/vix_adaptive_thresholds.py",
}


def test_no_module_imports_yfinance_without_going_through_the_gate():
    importers = set()
    for root in ("2.0/src", "2.0/scripts", "scripts", "common"):
        for path in (REPO / root).rglob("*.py"):
            if {"venv", "tests"} & set(path.parts):
                continue
            text = path.read_text()
            if re.search(r"^\s*(import yfinance|from yfinance)", text, re.M):
                importers.add(str(path.relative_to(REPO)))
                assert "yahoo_call(" in text, f"{path.relative_to(REPO)} calls yfinance outside yahoo_call"
    assert importers == YFINANCE_IMPORTERS


# --- the backoff, end to end through one call site -------------------------------

def test_a_yahoo_rate_limit_is_retried_after_a_backoff(monkeypatch, vendor_clock):
    from scripts.scan import harvest_live

    answers = iter([YFRateLimitError(), _history_frame()])
    fake = FakeYfinance(object(), {"history": lambda: next(answers)})
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    start = vendor_clock.now

    assert harvest_live._fetch_closes_yf("SPY", "3mo") == [100.0, 101.0]
    assert fake.requests == ["history", "history"]
    assert vendor_clock.now - start == pytest.approx(vendor_gate(YAHOO).base_delay)


def test_a_yahoo_rate_limit_that_never_clears_fails_the_call_within_the_budget(monkeypatch, vendor_clock):
    from scripts.scan import harvest_live

    fake = FakeYfinance(object(), {"history": YFRateLimitError})
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    start = vendor_clock.now

    assert harvest_live._fetch_closes_yf("SPY", "3mo") == []
    gate = vendor_gate(YAHOO)
    assert len(fake.requests) <= gate.attempts
    assert vendor_clock.now - start <= gate.budget


def test_other_yahoo_errors_are_not_retried(monkeypatch, vendor_clock):
    from scripts.scan import harvest_live

    fake = FakeYfinance(object(), {"history": ValueError("no data")})
    monkeypatch.setitem(sys.modules, "yfinance", fake)

    assert harvest_live._fetch_closes_yf("SPY", "3mo") == []
    assert fake.requests == ["history"]
