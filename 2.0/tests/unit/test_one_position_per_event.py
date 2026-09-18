"""One position per ticker per earnings event, warned on every path.

CLAUDE.md: "One position per ticker per earnings event. No second bet, no
repair." A repair campaign on record was two positions on one event. The check lived
in TickerAnalyzer._check_existing_position and ran only for /analyze with
--strategies, so scan, whisper and both Telegram commands never showed it. One
function (application/services/existing_position.py) now serves all of them.

It reads the `positions` view over `strategies`, which /journal fills after positions
close: a closed, journaled trade on the event is all it can see, and the warning says
so. A trade placed in both accounts is one prior position, not two (migration 5).

Real Postgres (pg_pool, tables from the baseline schema): a row-access or SQL
mistake here must fail a test, not quietly skip the guard in production.
"""

import importlib
import logging
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from common.pgtesting import baseline_ddl  # noqa: E402
from src.application.services.existing_position import (  # noqa: E402
    existing_position_warning,
)
from src.config.config import APIConfig  # noqa: E402
from src.domain.enums import EarningsTiming, Recommendation  # noqa: E402
from src.domain.errors import AppError, Err, ErrorCode, Ok  # noqa: E402
from src.domain.types import Money, Percentage, VRPResult  # noqa: E402
from src.infrastructure.cache.memory_cache import (  # noqa: E402
    CachedOptionsDataProvider,
    MemoryCache,
)

pytestmark = pytest.mark.pg


def _monday(weeks_ahead=2):
    today = date.today()
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7) + timedelta(weeks=weeks_ahead)


EVENT = _monday() + timedelta(days=1)          # a Tuesday, in the future
OTHER_EVENT = EVENT - timedelta(days=91)


def _insert(pool, symbol, earnings_date, acquired, quantity, campaign_id=None,
            strategy_type="SPREAD", gain_loss=-39617.0, account="TAXABLE"):
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO strategies (symbol, strategy_type, acquired_date, sale_date,"
            " quantity, gain_loss, is_winner, earnings_date, campaign_id, trade_type,"
            " account_type)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'NEW', %s)",
            (symbol, strategy_type, acquired, acquired + timedelta(days=1), quantity,
             gain_loss, gain_loss > 0, earnings_date, campaign_id, account),
        )
        conn.commit()


@pytest.fixture
def pool(pg_pool):
    with pg_pool.connection() as conn:
        conn.execute(baseline_ddl("strategies", "trade_journal", "positions"))
        conn.commit()
    return pg_pool


@pytest.fixture
def traded(pool):
    """MU already carries a journaled position on EVENT (the Dec 2025 shape)."""
    _insert(pool, "MU", EVENT, EVENT - timedelta(days=1), 128, campaign_id="MU-EVENT")
    return pool


# --- the shared check -------------------------------------------------------

def test_no_prior_position_is_silent(pool):
    assert existing_position_warning(pool, "MU", EVENT) is None


def test_prior_position_on_the_event_is_flagged_with_its_limit(traded):
    warning = existing_position_warning(traded, "MU", EVENT)
    assert warning is not None
    assert "MU" in warning and "128" in warning and "MU-EVENT" in warning
    assert EVENT.isoformat() in warning
    # the check sees closed, journaled trades only, and must say so
    assert "closed" in warning and "journaled" in warning


def test_another_event_for_the_ticker_is_not_this_one(pool):
    _insert(pool, "MU", OTHER_EVENT, OTHER_EVENT - timedelta(days=1), 800)
    assert existing_position_warning(pool, "MU", EVENT) is None


def test_another_ticker_on_the_date_is_not_flagged(pool):
    _insert(pool, "AAPL", EVENT, EVENT - timedelta(days=1), 50)
    assert existing_position_warning(pool, "MU", EVENT) is None


def test_counts_every_position_and_describes_the_latest(pool):
    for i, qty in enumerate((128, 200, 75, 40, 10, 5)):
        _insert(pool, "MU", EVENT, EVENT - timedelta(days=10 - i), qty)
    warning = existing_position_warning(pool, "MU", EVENT)
    assert warning.startswith("6 prior")
    assert "x5" in warning                         # the most recently acquired


def test_one_trade_in_both_accounts_is_one_prior_position(pool):
    # PLTR 2026-05-04: the same put spread in TAXABLE and the IRA, strategies 297/322.
    _insert(pool, "PLTR", EVENT, EVENT - timedelta(days=1), 200, account="TAXABLE")
    _insert(pool, "PLTR", EVENT, EVENT - timedelta(days=1), 200, account="IRA")
    warning = existing_position_warning(pool, "PLTR", EVENT)
    assert warning.startswith("1 prior")
    assert "x400" in warning


def test_a_broken_check_warns_with_its_traceback_and_does_not_block(pg_pool, caplog):
    # no strategies table: the query fails
    with caplog.at_level(logging.INFO):
        assert existing_position_warning(pg_pool, "MU", EVENT) is None
    hits = [r for r in caplog.records if r.levelno >= logging.WARNING and "MU" in r.getMessage()]
    assert hits and hits[0].exc_info is not None


# --- every path shows it ------------------------------------------------------

def _fridays(monday, weeks=6):
    return [monday + timedelta(days=4, weeks=w) for w in range(weeks)]


class _Listing:
    def __init__(self, expirations):
        self.expirations = expirations

    def get_expirations(self, ticker):
        return Ok(list(self.expirations))


def _vrp(ticker="MU"):
    return VRPResult(
        ticker=ticker, expiration=EVENT + timedelta(days=3),
        implied_move_pct=Percentage(10.0), historical_mean_move_pct=Percentage(4.0),
        vrp_ratio=2.5, edge_score=1.0, recommendation=Recommendation.EXCELLENT,
        historical_quarters=8,
    )


def _container(db_pool):
    container = MagicMock()
    container.db_pool = db_pool
    container.cached_options_provider = CachedOptionsDataProvider(
        provider=_Listing(_fridays(_monday())), cache=MemoryCache()
    )
    # A real APIConfig: a MagicMock's orats_enabled is truthy, and the analyzer
    # would read the ORATS snapshot table this fixture does not build.
    container.config.api = APIConfig(tradier_api_key="")
    container.config.thresholds.require_weekly_options = False
    container.config.thresholds.min_dte = 3
    container.config.thresholds.min_historical_quarters = 4
    container.implied_move_calculator.calculate.return_value = Ok(Mock(
        implied_move_pct=Percentage(10.0), stock_price=Money(100.0),
    ))
    moves = [Mock(close_move_pct=Percentage(4.0), gap_move_pct=Percentage(3.0),
                  intraday_move_pct=Percentage(3.5), earnings_date=EVENT - timedelta(days=60))
             for _ in range(8)]
    container.prices_repository.get_historical_moves.return_value = Ok(moves)
    container.vrp_calculator.calculate.side_effect = lambda ticker, **kw: Ok(_vrp(ticker))
    # The scan's VIX log line reads this; unavailable is a logged warning, nothing more.
    container.market_conditions_analyzer.get_current_conditions.return_value = Err(
        AppError(ErrorCode.EXTERNAL, "no VIX in this test"))
    container.skew_analyzer = None
    container.consistency_analyzer = None
    return container


@pytest.fixture
def scan_deps(monkeypatch):
    """Cut analyze_ticker's outside calls: company name and liquidity chain. Its
    analysis_log row goes to the container's mock repository."""
    from scripts.scan.workflows import ticker_analysis as ta

    monkeypatch.setattr(ta, "get_ticker_name", lambda ticker: "Micron Technology")
    monkeypatch.setattr(ta, "check_liquidity_hybrid",
                        lambda **kw: (True, "GOOD", {"method": "NO_CHAIN"}))
    return ta


def _warned(caplog):
    """The table's warning block, not just the check's own log line (which the
    shared function writes whether or not any output shows it)."""
    from scripts.scan.formatters import EXISTING_POSITION_HEADER

    messages = [r.getMessage() for r in caplog.records]
    at = [i for i, m in enumerate(messages) if EXISTING_POSITION_HEADER in m]
    return bool(at) and any("MU" in m and "already exist" in m for m in messages[at[0]:])


def test_analyze_warns_without_strategy_generation(traded):
    from src.application.services.analyzer import TickerAnalyzer

    container = _container(traded)
    result = TickerAnalyzer(container).analyze(
        "MU", EVENT, EVENT + timedelta(days=3), earnings_timing=EarningsTiming.AMC,
        generate_strategies=False,
    )
    assert result.is_ok, result
    assert "MU-EVENT" in result.value.existing_position_warning


def test_scan_result_carries_the_warning(traded, scan_deps):
    result = scan_deps.analyze_ticker(
        _container(traded), "MU", EVENT, EVENT + timedelta(days=3),
        earnings_timing=EarningsTiming.AMC,
    )
    assert result["status"] == "SUCCESS"
    assert "MU-EVENT" in result["existing_position_warning"]


def test_scan_result_without_a_prior_position_has_none(pool, scan_deps):
    result = scan_deps.analyze_ticker(
        _container(pool), "MU", EVENT, EVENT + timedelta(days=3),
        earnings_timing=EarningsTiming.AMC,
    )
    assert result["existing_position_warning"] is None


def test_sequential_scan_output_shows_the_warning(traded, scan_deps, monkeypatch, caplog):
    scan_mode = importlib.import_module("scripts.scan.workflows.scan_mode")

    monkeypatch.setattr(scan_mode, "fetch_earnings_for_date",
                        lambda container, d: [("MU", EVENT, EarningsTiming.AMC)])
    monkeypatch.setattr(scan_mode, "should_filter_ticker",
                        lambda *a, **kw: (False, None, None, None))
    with caplog.at_level(logging.INFO):
        assert scan_mode.scanning_mode(_container(traded), EVENT, parallel=False) == 0
    assert _warned(caplog)


def test_sequential_ticker_output_shows_the_warning(traded, scan_deps, monkeypatch, caplog):
    ticker_mode = importlib.import_module("scripts.scan.workflows.ticker_mode")

    monkeypatch.setattr(ticker_mode, "fetch_earnings_for_ticker",
                        lambda container, t: (EVENT, EarningsTiming.AMC))
    monkeypatch.setattr(ticker_mode, "should_filter_ticker",
                        lambda *a, **kw: (False, None, None, None))
    with caplog.at_level(logging.INFO):
        assert ticker_mode.ticker_mode(_container(traded), ["MU"], parallel=False) == 0
    assert _warned(caplog)


def _whisper_run(db_pool, monkeypatch):
    whisper_mode = importlib.import_module("scripts.scan.workflows.whisper_mode")
    from src.utils.concurrent_scanner import ConcurrentScanner

    monkeypatch.setattr(whisper_mode, "fetch_earnings_for_ticker",
                        lambda container, t: (EVENT, EarningsTiming.AMC))
    monkeypatch.setattr(whisper_mode, "filter_ticker_concurrent", lambda *a: (False, None))
    monkeypatch.setattr(whisper_mode, "validate_tradeable_earnings_dates", lambda *a: None)
    container = _container(db_pool)
    container.concurrent_scanner = ConcurrentScanner(container, max_workers=1)
    monday = _monday()
    return whisper_mode.collect_whisper_results(container, ["MU"], monday, monday + timedelta(days=6))


def test_whisper_results_and_table_show_the_warning(traded, scan_deps, monkeypatch, caplog):
    from scripts.scan.formatters import _display_scan_results

    run = _whisper_run(traded, monkeypatch)
    assert "MU-EVENT" in run.results[0]["existing_position_warning"]
    with caplog.at_level(logging.INFO):
        _display_scan_results(run.results, run.success_count, run.error_count, run.skip_count,
                              run.filtered_count, "WHISPER MODE", week_range=run.week_range)
    assert _warned(caplog)


def test_telegram_whisper_digest_shows_the_warning(traded, scan_deps, monkeypatch):
    from src.application.formatters.telegram import format_digest

    run = _whisper_run(traded, monkeypatch)
    assert "already exist for MU" in format_digest(run.results, title="Whisper")


def test_telegram_analyze_reply_shows_the_warning(traded, scan_deps, monkeypatch):
    from scripts.jobs import telegram_commands

    monkeypatch.setattr(telegram_commands, "fetch_earnings_for_ticker",
                        lambda container, t: (EVENT, EarningsTiming.AMC))
    reply = telegram_commands._handle_analyze(_container(traded), "MU")
    assert "already exist for MU" in reply
    assert "closed" in reply


def test_every_tradeable_table_prints_the_warnings():
    """Four renderers print the tradeable table (the parallel helper and the
    sequential scan, ticker and whisper modes); each must call the shared one."""
    root = Path(__file__).resolve().parents[2] / "scripts" / "scan"
    for rel in ("formatters.py", "workflows/scan_mode.py",
                "workflows/ticker_mode.py", "workflows/whisper_mode.py"):
        src = (root / rel).read_text()
        tables = src.count("TRADEABLE OPPORTUNITIES FOUND")
        assert tables >= 1, rel
        assert src.count("log_existing_position_warnings(tradeable)") == tables, rel
