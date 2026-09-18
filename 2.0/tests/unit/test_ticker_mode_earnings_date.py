"""`trade.sh list TICKERS DATE [offset_days]` analyses every ticker for earnings on DATE.

Until 2026-09-14 the wrapper validated DATE and printed `Earnings Date: DATE`, then ran
`scan.py --tickers ... --expiration-offset 1`. scan.py had no way to receive the date, so
each ticker was analysed for its next calendar date, and the default offset of 1 (raised
to the 3-day floor) replaced the weekday expiration rule on every run. `--earnings-date`
now carries DATE, each ticker takes its stored BMO/AMC tag for it as `./trade.sh TICKER
DATE` does (UNKNOWN when the calendar has none), and the wrapper passes an offset only
when one was given (scripts/tests/test_shell_wrappers.py).
"""

import importlib
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.domain.enums import EarningsTiming
from src.domain.errors import AppError, Err, ErrorCode, Ok
from src.utils.concurrent_scanner import ConcurrentScanner

WEDNESDAY = date(2026, 10, 14)
NEXT_FRIDAY = date(2026, 10, 23)      # the weekday rule: the same-week Friday is 2 DTE


@pytest.fixture
def ticker_mode(monkeypatch):
    """scripts.scan.workflows.ticker_mode with its calendar lookup refused, filters and
    display stubbed, and the analysis replaced by a recorder of what it was handed."""
    module = importlib.import_module("scripts.scan.workflows.ticker_mode")
    seen = []

    def record(container, ticker, earnings_date, expiration_date, **kwargs):
        seen.append((ticker, earnings_date, expiration_date, kwargs["earnings_timing"]))
        return None

    def no_calendar_lookup(*args, **kwargs):
        raise AssertionError("a given earnings date must not be replaced by the calendar's")

    monkeypatch.setattr(module, "_log_vix_context", lambda container: None)
    monkeypatch.setattr(module, "should_filter_ticker", lambda *a, **kw: (False, None, None, None))
    monkeypatch.setattr(module, "filter_ticker_concurrent", lambda *a, **kw: (False, None))
    monkeypatch.setattr(module, "_display_scan_results", lambda *a, **kw: 0)
    monkeypatch.setattr(module, "analyze_ticker", record)
    monkeypatch.setattr(module, "analyze_ticker_concurrent", record)
    monkeypatch.setattr(module, "fetch_earnings_for_ticker", no_calendar_lookup)
    return module, seen


def _container(stored):
    """Stored timings by ticker; a ticker absent from `stored` has no calendar row."""
    container = MagicMock()
    container.config.thresholds.min_dte = 3
    container.concurrent_scanner = ConcurrentScanner(container, max_workers=1)

    def get_timing(ticker, earnings_date):
        if ticker in stored:
            return Ok(stored[ticker])
        return Err(AppError(ErrorCode.NODATA, f"No earnings_calendar row for {ticker}"))

    container.earnings_repository.get_timing.side_effect = get_timing
    return container


@pytest.mark.parametrize("tickers", [["AAA"], ["AAA", "BBB"]], ids=["sequential", "parallel"])
def test_every_ticker_is_analysed_for_the_date_given(ticker_mode, tickers):
    module, seen = ticker_mode
    container = _container({"AAA": EarningsTiming.BMO})

    module.ticker_mode(container, tickers, None, parallel=True, earnings_date=WEDNESDAY)

    expected = {("AAA", WEDNESDAY, NEXT_FRIDAY, EarningsTiming.BMO),
                ("BBB", WEDNESDAY, NEXT_FRIDAY, EarningsTiming.UNKNOWN)}
    assert set(seen) == {e for e in expected if e[0] in tickers}


@pytest.mark.parametrize("tickers", [["AAA"], ["AAA", "BBB"]], ids=["sequential", "parallel"])
def test_an_offset_given_with_the_date_replaces_the_weekday_rule(ticker_mode, tickers):
    module, seen = ticker_mode
    module.ticker_mode(_container({}), tickers, 1, parallel=True, earnings_date=WEDNESDAY)
    # +1 raised to the 3-day minimum: Saturday, moved to the next trading day.
    assert {expiration for _, _, expiration, _ in seen} == {date(2026, 10, 19)}


def test_without_a_date_each_ticker_keeps_its_next_calendar_date(ticker_mode, monkeypatch):
    """`scan.py --tickers` on its own still means each ticker's next event."""
    module, seen = ticker_mode
    monkeypatch.setattr(module, "fetch_earnings_for_ticker",
                        lambda container, ticker: (WEDNESDAY, EarningsTiming.AMC))
    module.ticker_mode(_container({}), ["AAA"], None)
    assert seen == [("AAA", WEDNESDAY, NEXT_FRIDAY, EarningsTiming.AMC)]


def test_scan_main_hands_the_date_to_ticker_mode():
    from tests.unit.test_container_startup import _load_script

    scan = _load_script("scan")          # scan.py as trade.sh runs it, by path
    args = scan.parse_args(["--tickers", "aapl, msft", "--earnings-date", "2026-10-14"])
    with patch.object(scan, "parse_args", return_value=args), \
         patch.object(scan, "setup_logging"), \
         patch.object(scan, "register_shutdown_callback"), \
         patch.object(scan, "apply_migrations"), \
         patch.object(scan, "Container") as container, \
         patch.object(scan, "ticker_mode", return_value=0) as ticker_mode:
        assert scan.main() == 0
    ticker_mode.assert_called_once_with(
        container.return_value, ["AAPL", "MSFT"], None,
        parallel=True, skip_weekly_filter=False, earnings_date=WEDNESDAY,
    )


def test_scan_cli_takes_the_date_only_with_tickers():
    from scripts.scan.cli import parse_args

    assert parse_args(["--tickers", "AAPL", "--earnings-date", "2026-10-14"]).earnings_date == WEDNESDAY
    assert parse_args(["--tickers", "AAPL"]).earnings_date is None
    for argv in (["--scan-date", "2026-10-14", "--earnings-date", "2026-10-14"],
                 ["--whisper-week", "--earnings-date", "2026-10-14"],
                 ["--tickers", "AAPL", "--earnings-date", "10/14/2026"]):
        with pytest.raises(SystemExit) as exc:
            parse_args(argv)
        assert exc.value.code == 2
