"""ScanRun seam extracted from whisper_mode.

whisper_mode_parallel() historically returned only a CLI exit code: it built a
`results` list and handed it straight to _display_scan_results(), so no non-CLI
consumer (Telegram digest, A/B diff harness) could reach the data. This is the
one refactor the whole 5.0 migration is blocked on.

These are characterisation tests: they pin the behaviour that must NOT change
(the CLI path still returns an exit code, still passes the same results to the
display helper, still short-circuits when no earnings are found) alongside the
new data-returning entry point.

Imports use the `scripts.` prefix, matching tests/conftest.py which puts 2.0/ on
sys.path. That is not cosmetic: 2.0/scripts/scan is importable as BOTH `scan.*`
(the CLI entry point adds 2.0/scripts to sys.path) and `scripts.scan.*`, and
Python treats those as two distinct module objects — `scan.quality_scorer is
scripts.scan.quality_scorer` is False. Patching one prefix while the code under
test uses the other silently patches nothing.
"""

from datetime import date, timedelta
from unittest.mock import Mock, patch

import pytest

from scripts.scan.workflows.whisper_mode import (
    ScanRun,
    collect_whisper_results,
    whisper_mode_parallel,
)
from src.domain.errors import AppError, Err, ErrorCode

MOD = "scripts.scan.workflows.whisper_mode"

ROW = {
    "ticker": "AAPL",
    "ticker_name": "Apple Inc",
    "earnings_date": "2026-10-29",
    "expiration_date": "2026-10-30",
    "stock_price": 250.0,
    "implied_move_pct": "5.0",
    "historical_mean_pct": "3.0",
    "vrp_ratio": 1.67,
    "edge_score": 40.0,
    "recommendation": "GOOD",
    "is_tradeable": True,
    "liquidity_tier": "EXCELLENT",
    "liquidity_oi_ratio": 12.0,
    "directional_bias": "NEUTRAL",
    "status": "SUCCESS",
}


def _batch(rows, **counts):
    br = Mock()
    br.results = [Mock(data=r) for r in rows]
    br.success_count = counts.get("success", len(rows))
    br.error_count = counts.get("error", 0)
    br.skip_count = counts.get("skip", 0)
    br.filtered_count = counts.get("filtered", 0)
    br.total_duration_ms = 1.0
    br.avg_duration_ms = 1.0
    return br


@pytest.fixture
def monday():
    # Relative, never a hardcoded future date: a literal pair eventually rots
    # into the past and silently stops exercising the branch it was written for.
    today = date.today()
    return today - timedelta(days=today.weekday()) + timedelta(days=7)


def _container(batch):
    c = Mock()
    c.concurrent_scanner.scan_tickers.return_value = batch
    # The run's VIX log line reads this; unavailable is a logged warning, nothing more.
    c.market_conditions_analyzer.get_current_conditions.return_value = Err(
        AppError(ErrorCode.EXTERNAL, "no VIX in this test"))
    return c


@pytest.fixture
def container():
    return _container(_batch([ROW]))


def test_collect_returns_data_not_an_exit_code(container, monday):
    with patch(f"{MOD}.fetch_earnings_for_ticker",
               return_value=(monday + timedelta(days=2), Mock(value="AMC"))), \
         patch(f"{MOD}.validate_tradeable_earnings_dates"):
        run = collect_whisper_results(container, ["AAPL"], monday,
                                      monday + timedelta(days=6))

    assert isinstance(run, ScanRun)
    assert run.results == [ROW]
    assert run.success_count == 1
    assert run.week_range == (monday, monday + timedelta(days=6))
    assert run.no_earnings_found is False


def test_collect_validates_tradeable_earnings_dates(container, monday):
    """The validation side effect must survive the extraction."""
    with patch(f"{MOD}.fetch_earnings_for_ticker",
               return_value=(monday + timedelta(days=2), Mock(value="AMC"))), \
         patch(f"{MOD}.validate_tradeable_earnings_dates") as validate:
        collect_whisper_results(container, ["AAPL"], monday,
                                monday + timedelta(days=6))

    validate.assert_called_once()
    assert validate.call_args[0][0] == [ROW]


def test_collect_reports_no_earnings_found(container, monday):
    with patch(f"{MOD}.fetch_earnings_for_ticker", return_value=None):
        run = collect_whisper_results(container, ["AAPL"], monday,
                                      monday + timedelta(days=6))

    assert run.no_earnings_found is True
    assert run.results == []
    assert run.success_count == 0


def test_skip_count_includes_tickers_with_no_earnings(monday):
    """Pins the original arithmetic: skip_count + (len(tickers) - len(lookup))."""
    c = _container(_batch([ROW], skip=2))

    def only_aapl(_container, ticker):
        if ticker == "AAPL":
            return (monday + timedelta(days=2), Mock(value="AMC"))
        return None

    with patch(f"{MOD}.fetch_earnings_for_ticker", side_effect=only_aapl), \
         patch(f"{MOD}.validate_tradeable_earnings_dates"):
        run = collect_whisper_results(c, ["AAPL", "MSFT", "NVDA"], monday,
                                      monday + timedelta(days=6))

    # 2 from the scanner + 2 tickers that never had earnings
    assert run.skip_count == 4


def test_cli_path_still_returns_exit_code_and_same_results(container, monday):
    with patch(f"{MOD}.fetch_earnings_for_ticker",
               return_value=(monday + timedelta(days=2), Mock(value="AMC"))), \
         patch(f"{MOD}.validate_tradeable_earnings_dates"), \
         patch(f"{MOD}._display_scan_results", return_value=0) as disp:
        rc = whisper_mode_parallel(container, ["AAPL"], monday,
                                   monday + timedelta(days=6))

    assert rc == 0
    assert disp.call_args[1]["results"] == [ROW]
    assert disp.call_args[1]["mode_name"] == "WHISPER MODE"
    assert disp.call_args[1]["week_range"] == (monday, monday + timedelta(days=6))


def test_cli_path_short_circuits_to_zero_when_no_earnings(container, monday):
    """Original returned 0 WITHOUT calling the display helper."""
    with patch(f"{MOD}.fetch_earnings_for_ticker", return_value=None), \
         patch(f"{MOD}._display_scan_results", return_value=99) as disp:
        rc = whisper_mode_parallel(container, ["AAPL"], monday,
                                   monday + timedelta(days=6))

    assert rc == 0
    disp.assert_not_called()
