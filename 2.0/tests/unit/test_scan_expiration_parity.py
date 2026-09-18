"""The trading expiration is the same whether a scan runs in parallel or not.

scan, whisper and ticker mode each have a sequential loop and a parallel path
through ConcurrentScanner (the default; `--sequential` turns it off). Until
2026-09-14 they disagreed on two things. The parallel path turned the offset into
`expiration_offset or 0` and then `offset if offset else None`, so
`--expiration-offset 0` meant the weekday rule there and the 3-day floor
sequentially. And ConcurrentScanner never passed `min_dte`, so `MIN_DTE` bound
only `--sequential` runs.

Each mode is run both ways, for every weekday of an ordinary week and a holiday
week (Thanksgiving, Good Friday), no offset and offsets 0-7, at MIN_DTE 3 and 5.
The analysis is replaced by a recorder of the expiration it was handed.
"""

import importlib
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.domain.enums import EarningsTiming
from src.domain.errors import Ok
from src.utils.concurrent_scanner import ConcurrentScanner

WEEKS = [date(2026, 10, 12), date(2026, 11, 23), date(2027, 3, 22)]   # Mondays
EVENTS = [monday + timedelta(days=d) for monday in WEEKS for d in range(5)]
OFFSETS = [None, *range(8)]
MIN_DTES = [3, 5]
CASES = [(e, o, m) for e in EVENTS for o in OFFSETS for m in MIN_DTES]


def _container(min_dte):
    container = MagicMock()
    container.config.thresholds.min_dte = min_dte
    container.concurrent_scanner = ConcurrentScanner(container, max_workers=1)
    return container


def _recorder(seen):
    def analyze(container, ticker, earnings_date, expiration_date, **kwargs):
        seen.append(expiration_date)
        return None
    return analyze


@pytest.fixture
def modes(monkeypatch):
    """Patch each mode's calendar, filters, analysis and display; return a runner
    `run(mode, parallel, event, offset, min_dte) -> the expiration analysed`."""
    workflows = {name: importlib.import_module(f"scripts.scan.workflows.{name}")
                 for name in ("scan_mode", "ticker_mode", "whisper_mode")}
    state = {}

    for module in workflows.values():
        monkeypatch.setattr(module, "_log_vix_context", lambda container: None)
        monkeypatch.setattr(module, "should_filter_ticker",
                            lambda *a, **kw: (False, None, None, None))
        monkeypatch.setattr(module, "filter_ticker_concurrent", lambda *a, **kw: (False, None))
        monkeypatch.setattr(module, "_display_scan_results", lambda *a, **kw: 0)
        seen = state.setdefault("seen", [])
        monkeypatch.setattr(module, "analyze_ticker", _recorder(seen))
        monkeypatch.setattr(module, "analyze_ticker_concurrent", _recorder(seen))
        if hasattr(module, "fetch_earnings_for_ticker"):
            monkeypatch.setattr(module, "fetch_earnings_for_ticker",
                                lambda container, t: (state["event"], EarningsTiming.AMC))
    monkeypatch.setattr(workflows["scan_mode"], "fetch_earnings_for_date",
                        lambda container, d: [("AAA", state["event"], EarningsTiming.AMC)])

    whisper = workflows["whisper_mode"]
    monkeypatch.setattr(whisper, "ensure_tickers_in_db", lambda *a: None)
    monkeypatch.setattr(whisper, "get_shared_cache", lambda container: None)
    scraper = MagicMock()
    scraper.get_most_anticipated_earnings.side_effect = lambda week_monday=None: Ok(
        (["AAA"], datetime.combine(state["event"] - timedelta(days=state["event"].weekday()),
                                   datetime.min.time())))
    monkeypatch.setattr(whisper, "EarningsWhisperScraper", lambda cache=None: scraper)

    def run(mode, parallel, event, offset, min_dte):
        state["event"] = event
        state["seen"].clear()
        container = _container(min_dte)
        if mode == "scan":
            workflows["scan_mode"].scanning_mode(container, event, offset, parallel=parallel)
        elif mode == "ticker":
            # One ticker never goes parallel; two do, on the same event.
            tickers = ["AAA", "BBB"] if parallel else ["AAA"]
            workflows["ticker_mode"].ticker_mode(container, tickers, offset, parallel=parallel)
        else:
            whisper.whisper_mode(container, None, offset, parallel=parallel)
        assert state["seen"], f"{mode} parallel={parallel} analysed nothing"
        assert len(set(state["seen"])) == 1
        return state["seen"][0]

    return run


@pytest.mark.parametrize("mode", ["scan", "ticker", "whisper"])
def test_parallel_and_sequential_analyse_the_same_expiration(modes, mode):
    mismatches = []
    for event, offset, min_dte in CASES:
        sequential = modes(mode, False, event, offset, min_dte)
        parallel = modes(mode, True, event, offset, min_dte)
        if parallel != sequential:
            mismatches.append(f"{event:%a %Y-%m-%d} offset={offset} MIN_DTE={min_dte}: "
                              f"sequential {sequential}, parallel {parallel}")
    assert mismatches == []


@pytest.mark.parametrize("mode", ["scan", "ticker", "whisper"])
def test_both_paths_hold_the_configured_floor(modes, mode):
    """Wednesday, offset 0, MIN_DTE 5: the floor raises the offset to 5 days,
    Monday. The parallel path used to take the weekday rule and a 3-day floor."""
    wednesday = date(2026, 10, 14)
    for parallel in (False, True):
        assert modes(mode, parallel, wednesday, 0, 5) == date(2026, 10, 19)
