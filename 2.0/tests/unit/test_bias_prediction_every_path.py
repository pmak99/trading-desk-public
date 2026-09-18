"""A bias prediction for every analysed earnings event, not only `./trade.sh TICKER`.

The compound-risk recalibration (scripts/research/compound_risk_recalibration.py)
needs 68 graded bias_predictions rows per group. Until 2026-09-14 only
`trade.sh TICKER` wrote one -- 3.4 gradable rows a week, FULL not before 2028 --
while scan, whisper, the Telegram bot and the morning digest analysed the same
names and threw the skew reading away. They all run
ticker_analysis.analyze_ticker, which now hands its reading to the one writer,
store_bias_prediction.analyze_and_store.

What a row must be for the recalibration to grade it, and so what the writer
enforces:
  - one per (ticker, earnings_date), the FIRST, like the A/B evaluator's first
    pre-open analysis -- no later look can have chosen it;
  - taken before the reaction session opens, by the stored timing tag and the
    grader's own rule (src/domain/reaction_session.py);
  - for an event in earnings_calendar, the only events record_moves grades.

Real Postgres (pg_pool, tables from the baseline schema).
"""

import importlib
import logging
from collections import Counter
from datetime import date, timedelta
from unittest.mock import MagicMock, Mock

import pytest

import scripts.store_bias_prediction as sbp  # noqa: E402
from common.pgtesting import baseline_ddl  # noqa: E402
from src.application.metrics.skew_enhanced import SkewAnalysis  # noqa: E402
from src.domain.enums import (  # noqa: E402
    DirectionalBias,
    EarningsTiming,
    Recommendation,
)
from src.domain.errors import AppError, Err, ErrorCode, Ok  # noqa: E402
from src.domain.reaction_session import reaction_open_cutoff  # noqa: E402
from src.domain.types import Money, Percentage, VRPResult  # noqa: E402
from src.infrastructure.cache.memory_cache import (  # noqa: E402
    CachedOptionsDataProvider,
    MemoryCache,
)

pytestmark = pytest.mark.pg


def _monday(weeks_ahead=2):
    today = date.today()
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7) + timedelta(weeks=weeks_ahead)


MONDAY = _monday()
EVENT = MONDAY + timedelta(days=1)             # a Tuesday, in the future
FRIDAY = MONDAY + timedelta(days=4)            # calculate_expiration_date(EVENT)
FRIDAYS = [MONDAY + timedelta(days=4, weeks=w) for w in range(6)]


class _Tradier:
    """Stands in for TradierAPI under CachedOptionsDataProvider and counts every call."""

    def __init__(self):
        self.calls = Counter()

    def get_expirations(self, ticker):
        self.calls["expirations"] += 1
        return Ok(list(FRIDAYS))

    def get_option_chain(self, ticker, expiration, stock_price=None):
        self.calls["chain"] += 1
        return Ok(Mock(stock_price=Money(100.0)))


class _NoCache(MemoryCache):
    """Every read misses: a reuse that only worked through a warm cache fails here."""

    def get(self, key):
        return None


class _Skew:
    """SkewAnalyzerEnhanced's contract: one chain read per call, then a reading."""

    def __init__(self, provider, ok=True):
        self.provider = provider
        self.ok = ok
        self.calls = []

    def analyze_skew_curve(self, ticker, expiration):
        self.calls.append((ticker, expiration))
        self.provider.get_option_chain(ticker, expiration)
        if not self.ok:
            return Err(AppError(ErrorCode.NODATA, "Insufficient data points for skew fit"))
        return Ok(SkewAnalysis(
            ticker=ticker, expiration=expiration, stock_price=Money(100.0),
            skew_atm=Percentage(12.0), curvature=0.01, strength="smirk",
            directional_bias=DirectionalBias.BEARISH, confidence=0.8, num_points=9,
            slope_atm=-0.24, bias_confidence=0.8,
        ))


def _vrp(ticker):
    return VRPResult(
        ticker=ticker, expiration=FRIDAY,
        implied_move_pct=Percentage(10.0), historical_mean_move_pct=Percentage(4.0),
        vrp_ratio=2.5, edge_score=1.0, recommendation=Recommendation.EXCELLENT,
        historical_quarters=8,
    )


def _container(db_pool, skew_ok=True):
    tradier = _Tradier()
    container = MagicMock()
    container.db_pool = db_pool
    container.cached_options_provider = CachedOptionsDataProvider(provider=tradier, cache=_NoCache())
    container.tradier_calls = tradier.calls
    container.config.thresholds.require_weekly_options = False
    container.config.thresholds.min_dte = 3   # the parallel path reads MIN_DTE too
    container.implied_move_calculator.calculate.return_value = Ok(Mock(
        implied_move_pct=Percentage(10.0), stock_price=Money(100.0),
    ))
    moves = [Mock(close_move_pct=Percentage(4.0), gap_move_pct=Percentage(3.0),
                  intraday_move_pct=Percentage(3.5), earnings_date=EVENT - timedelta(days=60)) for _ in range(8)]
    container.prices_repository.get_historical_moves.return_value = Ok(moves)
    container.vrp_calculator.calculate.side_effect = lambda ticker, **kw: Ok(_vrp(ticker))
    container.skew_analyzer = _Skew(container.cached_options_provider, ok=skew_ok)
    return container


@pytest.fixture
def pool(pg_pool):
    with pg_pool.connection() as conn:
        conn.execute(baseline_ddl("bias_predictions", "earnings_calendar", "strategies"))
    return pg_pool


def _calendar(pool, ticker="MU", earnings_date=EVENT, timing="AMC"):
    with pool.connection() as conn:
        conn.execute("INSERT INTO earnings_calendar (ticker, earnings_date, timing, confirmed)"
                     " VALUES (%s, %s, %s, true)", (ticker, earnings_date, timing))


@pytest.fixture
def event(pool):
    """MU reports after the close on EVENT, a stored calendar event."""
    _calendar(pool)
    return pool


def _rows(pool, ticker="MU"):
    with pool.connection() as conn:
        return conn.execute(
            "SELECT ticker, earnings_date, expiration, predicted_at, directional_bias, fused_bias,"
            " trr_level, compound_risk_active FROM bias_predictions WHERE ticker = %s ORDER BY id",
            (ticker,)).fetchall()


@pytest.fixture
def ta(monkeypatch):
    """analyze_ticker with its outside calls cut: company name, liquidity chain, analysis_log."""
    from scripts.scan.workflows import ticker_analysis

    monkeypatch.setattr(ticker_analysis, "get_ticker_name", lambda ticker: "Micron Technology")
    monkeypatch.setattr(ticker_analysis, "check_liquidity_hybrid",
                        lambda **kw: (True, "GOOD", {"method": "NO_CHAIN"}))
    monkeypatch.setattr(ticker_analysis, "_log_analysis_row", lambda *a, **kw: None)
    return ticker_analysis


def _scan(ta, container, ticker="MU", expiration=FRIDAY):
    return ta.analyze_ticker(container, ticker, EVENT, expiration,
                             earnings_timing=EarningsTiming.AMC)


# --- the scan path writes -------------------------------------------------------

def test_a_scan_writes_one_prediction_for_the_event(event, ta):
    result = _scan(ta, _container(event))

    assert result["status"] == "SUCCESS"
    rows = _rows(event)
    assert len(rows) == 1
    row = rows[0]
    assert (row.earnings_date, row.expiration) == (EVENT, FRIDAY)
    assert row.directional_bias == "bearish" and row.fused_bias == "bearish"
    assert row.trr_level == "LOW"                  # eight flat 3% gaps
    assert row.compound_risk_active is False


def test_the_prediction_reuses_the_scans_skew_reading(event, ta):
    container = _container(event)
    _scan(ta, container)
    # one skew read, the scan's own, on the trading expiration
    assert container.skew_analyzer.calls == [("MU", FRIDAY)]


def test_a_second_analysis_of_the_event_keeps_the_first_prediction(event, ta):
    _scan(ta, _container(event))
    first = _rows(event)[0].predicted_at

    _scan(ta, _container(event))

    rows = _rows(event)
    assert len(rows) == 1
    assert rows[0].predicted_at == first


def test_a_repeat_prediction_reads_no_chain(event):
    container = _container(event)
    assert sbp.analyze_and_store(container, "MU", EVENT) == sbp.STORED
    reads = dict(container.tradier_calls)

    assert sbp.analyze_and_store(container, "MU", EVENT) == sbp.SKIPPED
    assert dict(container.tradier_calls) == reads


def test_a_custom_expiration_offset_reads_the_prediction_chain(event, ta):
    """`trade.sh list TICKERS DATE 1` passes --expiration-offset 1: the scan's chain
    is not the trading Friday a prediction is fit on (see TestPredictionExpiration)."""
    container = _container(event)
    _scan(ta, container, expiration=EVENT + timedelta(days=3))

    assert container.skew_analyzer.calls[-1] == ("MU", FRIDAY)
    assert _rows(event)[0].expiration == FRIDAY


# --- ...only what the recalibration can grade ------------------------------------

def test_no_prediction_once_the_reaction_session_has_opened(event, ta, monkeypatch):
    cutoff = reaction_open_cutoff(EVENT, "AMC")          # 09:30 ET the next session
    monkeypatch.setattr(sbp, "now_et", lambda: cutoff)

    assert _scan(ta, _container(event))["status"] == "SUCCESS"
    assert _rows(event) == []


def test_the_last_moment_before_the_open_still_writes(event, ta, monkeypatch):
    monkeypatch.setattr(sbp, "now_et", lambda: reaction_open_cutoff(EVENT, "AMC") - timedelta(seconds=1))
    _scan(ta, _container(event))
    assert len(_rows(event)) == 1


@pytest.mark.parametrize("stored_timing,rows", [
    ("AMC", 1),        # reaction tomorrow: still a forecast
    ("BMO", 0),        # reaction today: the session just opened
    ("DMH", 0),        # uncertain tags take the earliest open that could be the reaction
    ("UNKNOWN", 0),
])
def test_the_stored_timing_tag_sets_the_cutoff_not_the_callers(pool, ta, monkeypatch,
                                                              stored_timing, rows):
    """09:30 ET on the report date; the scan passes AMC every time. The grader reads
    earnings_calendar.timing, so the writer does too."""
    _calendar(pool, timing=stored_timing)
    monkeypatch.setattr(sbp, "now_et", lambda: reaction_open_cutoff(EVENT, "BMO"))
    _scan(ta, _container(pool))
    assert len(_rows(pool)) == rows


def test_no_prediction_for_an_event_missing_from_the_calendar(pool, ta):
    """record_moves only records a reaction for a stored event, so an uncalendared
    prediction can never be graded -- and the calendar holds the tag the cutoff needs."""
    assert _scan(ta, _container(pool))["status"] == "SUCCESS"
    assert _rows(pool) == []


def test_no_prediction_without_a_skew_reading(event, ta, caplog):
    """A chain too thin for a 25Δ fit is a data condition the scan already shows as
    NEUTRAL: said once at INFO, and the event gets no row (every skew column is NOT NULL)."""
    with caplog.at_level(logging.INFO):
        assert _scan(ta, _container(event, skew_ok=False))["status"] == "SUCCESS"
    assert _rows(event) == []
    assert any("MU" in r.getMessage() and "no skew reading" in r.getMessage()
               for r in caplog.records)


def test_a_concurrent_writer_keeps_the_first_row(event):
    """The existence check and the insert are separate round trips (the chain read
    sits between them); ON CONFLICT DO NOTHING is what holds one row per event."""
    skew = _Skew(Mock()).analyze_skew_curve("MU", FRIDAY).value
    cutoff = reaction_open_cutoff(EVENT, "AMC")
    first, second = cutoff - timedelta(hours=2), cutoff - timedelta(hours=1)
    with event.connection() as conn:
        assert sbp.store_bias_prediction(conn, "MU", EVENT, FRIDAY, 100.0, skew, first) is True
        assert sbp.store_bias_prediction(conn, "MU", EVENT, FRIDAY, 100.0, skew, second) is False
    rows = _rows(event)
    assert len(rows) == 1 and rows[0].predicted_at == first


# --- a failure never costs the scan --------------------------------------------------

def test_a_raising_prediction_step_still_returns_the_scan_result(event, ta, monkeypatch, caplog):
    def boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(sbp, "analyze_and_store", boom)
    with caplog.at_level(logging.INFO):
        result = _scan(ta, _container(event))

    assert result["status"] == "SUCCESS" and result["ticker"] == "MU"
    hits = [r for r in caplog.records if r.levelno >= logging.WARNING and "MU" in r.getMessage()
            and "prediction" in r.getMessage()]
    assert hits and hits[0].exc_info is not None


def test_a_failing_write_is_a_warning_with_its_traceback(pool, caplog):
    """No earnings_calendar or bias_predictions table: the check itself raises."""
    with pool.connection() as conn:
        conn.execute("DROP TABLE earnings_calendar")
    with caplog.at_level(logging.INFO):
        assert sbp.analyze_and_store(_container(pool), "MU", EVENT) == sbp.FAILED
    hits = [r for r in caplog.records if r.levelno >= logging.WARNING and "MU" in r.getMessage()]
    assert hits and hits[0].exc_info is not None


# --- every path goes through it --------------------------------------------------------

def test_the_bot_analyze_command_writes_the_prediction(event, ta, monkeypatch):
    from scripts.jobs import telegram_commands

    monkeypatch.setattr(telegram_commands, "fetch_earnings_for_ticker",
                        lambda container, t: (EVENT, EarningsTiming.AMC))
    telegram_commands._handle_analyze(_container(event), "MU")
    assert len(_rows(event)) == 1


def test_whisper_collection_writes_the_prediction(event, ta, monkeypatch):
    """collect_whisper_results is /whisper, the bot's /whisper and the morning digest."""
    whisper_mode = importlib.import_module("scripts.scan.workflows.whisper_mode")
    from src.utils.concurrent_scanner import ConcurrentScanner

    monkeypatch.setattr(whisper_mode, "fetch_earnings_for_ticker",
                        lambda container, t: (EVENT, EarningsTiming.AMC))
    monkeypatch.setattr(whisper_mode, "filter_ticker_concurrent", lambda *a: (False, None))
    monkeypatch.setattr(whisper_mode, "validate_tradeable_earnings_dates", lambda *a: None)
    monkeypatch.setattr(whisper_mode, "_log_vix_context", lambda container: None)
    container = _container(event)
    container.concurrent_scanner = ConcurrentScanner(container, max_workers=1)

    whisper_mode.collect_whisper_results(container, ["MU"], MONDAY, MONDAY + timedelta(days=6))

    assert len(_rows(event)) == 1
    assert container.skew_analyzer.calls == [("MU", FRIDAY)]


# --- cost ------------------------------------------------------------------------------

TWENTY = [f"T{i:02d}" for i in range(20)]


def _twenty(pool, ta):
    container = _container(pool)
    for ticker in TWENTY:
        _scan(ta, container, ticker=ticker)
    return container


def test_a_twenty_ticker_scan_pays_no_extra_tradier_call(pool, ta, monkeypatch):
    for ticker in TWENTY:
        _calendar(pool, ticker=ticker)

    with monkeypatch.context() as m:
        m.setattr(ta, "_record_bias_prediction", lambda *a, **kw: None)
        without = _twenty(pool, ta).tradier_calls
    with pool.connection() as conn:
        assert conn.execute("SELECT count(*) FROM bias_predictions").fetchone()[0] == 0

    with_predictions = _twenty(pool, ta).tradier_calls

    assert with_predictions == without
    with pool.connection() as conn:
        assert conn.execute("SELECT count(*) FROM bias_predictions").fetchone()[0] == 20


def test_the_writer_refuses_exactly_what_the_grader_drops():
    from scripts import ab_evaluate_live as ev
    from src.domain import reaction_session

    assert ev.reaction_open_cutoff is reaction_session.reaction_open_cutoff
    assert ev.is_pre_open is reaction_session.is_pre_open
    assert sbp.reaction_open_cutoff is reaction_session.reaction_open_cutoff
