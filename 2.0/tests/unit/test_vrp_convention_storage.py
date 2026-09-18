"""analysis_log and sentiment_history name the VRP convention that graded each row
(migration 6), and the writer fills the graded convention from the VRPResult itself."""
from datetime import date

import psycopg
import pytest

from common.db import connect
from common.pgtesting import baseline_ddl
from src.application.metrics.vrp import compute_baseline_vrp, logged_vrp_columns
from src.domain.enums import EarningsTiming, Recommendation, VRPConvention
from src.domain.types import (
    HistoricalMove,
    ImpliedMove,
    Money,
    Percentage,
    Strike,
    TickerAnalysis,
    VRPResult,
)
from src.infrastructure.database.repositories.analysis_repository import (
    AnalysisRepository,
)

pytestmark = pytest.mark.pg


@pytest.fixture
def conn(pg_schema):
    c = connect()
    try:
        c.execute(baseline_ddl("analysis_log", "sentiment_history"))
        yield c
    finally:
        c.rollback()
        c.close()


def _insert(conn, convention, intraday, close):
    conn.execute(
        "INSERT INTO analysis_log (timestamp, ticker, earnings_date, expiration,"
        " implied_move_pct, historical_intraday_mean_pct, vrp_intraday_ratio,"
        " historical_close_mean_pct, vrp_close_ratio, recommendation, vrp_convention)"
        " VALUES (now(), 'T', '2026-10-15', '2026-10-16', 5.0, 2.0, %s, 3.0, %s, 'good', %s)",
        (intraday, close, convention))


def test_a_close_graded_row_needs_its_close_ratio(conn):
    _insert(conn, "close", 2.5, 1.6)
    _insert(conn, "close", None, 1.6)
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, "close", 2.5, None)


def test_an_intraday_graded_row_needs_its_intraday_ratio(conn):
    _insert(conn, "intraday", 2.5, None)
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, "intraday", None, 1.6)


def test_an_unknown_convention_is_refused(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert(conn, "gap", 2.5, 1.6)


def test_sentiment_history_convention_travels_with_its_ratio(conn):
    conn.execute("INSERT INTO sentiment_history (ticker, earnings_date, collected_at, source,"
                 " sentiment_text, vrp_ratio, vrp_convention)"
                 " VALUES ('T', '2026-10-15', now(), 'test', 'x', 1.1, 'close')")
    conn.execute("INSERT INTO sentiment_history (ticker, earnings_date, collected_at, source,"
                 " sentiment_text) VALUES ('V', '2026-10-15', now(), 'test', 'x')")
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("INSERT INTO sentiment_history (ticker, earnings_date, collected_at, source,"
                     " sentiment_text, vrp_ratio) VALUES ('U', '2026-10-15', now(), 'test', 'x', 1.1)")


def _moves(intraday, close):
    return [HistoricalMove(
        ticker="T", earnings_date=date(2025, m, 1), prev_close=Money(100.0),
        earnings_open=Money(100.0), earnings_high=Money(101.0), earnings_low=Money(99.0),
        earnings_close=Money(100.0), intraday_move_pct=Percentage(intraday),
        gap_move_pct=Percentage(close - intraday), close_move_pct=Percentage(close),
    ) for m in (1, 4, 7, 10)]


def _vrp(metric, ratio, mean):
    return VRPResult(ticker="T", expiration=date(2026, 10, 16), implied_move_pct=Percentage(6.0),
                     historical_mean_move_pct=Percentage(mean), vrp_ratio=ratio, edge_score=1.0,
                     recommendation=Recommendation.GOOD, move_metric=metric)


def test_logged_columns_take_the_graded_convention_from_the_result_itself():
    cols = logged_vrp_columns(_vrp("close", 1.5, 4.0), _moves(intraday=2.0, close=4.0))
    assert (cols["vrp_close_ratio"], cols["historical_close_mean_pct"]) == (1.5, 4.0)
    assert cols["vrp_intraday_ratio"] == pytest.approx(3.0)
    assert cols["historical_intraday_mean_pct"] == pytest.approx(2.0)


def test_compute_baseline_vrp_reads_the_named_metric():
    moves = _moves(intraday=2.0, close=4.0)
    assert compute_baseline_vrp(8.0, moves, "intraday") == (pytest.approx(2.0), pytest.approx(4.0))
    assert compute_baseline_vrp(8.0, moves, "close") == (pytest.approx(4.0), pytest.approx(2.0))
    assert compute_baseline_vrp(8.0, moves[:3], "close") == (None, None)
    with pytest.raises(ValueError):
        compute_baseline_vrp(8.0, moves, "gap")


def test_the_enum_is_the_check_vocabulary():
    assert {c.value for c in VRPConvention} == {"intraday", "close"}


def _analysis(vrp, **columns):
    implied = ImpliedMove(ticker="T", expiration=date(2026, 10, 16), stock_price=Money(100.0),
                          atm_strike=Strike(100.0), straddle_cost=Money(6.0),
                          implied_move_pct=Percentage(6.0), upper_bound=Money(106.0),
                          lower_bound=Money(94.0))
    return TickerAnalysis(ticker="T", earnings_date=date(2026, 10, 15),
                          earnings_timing=EarningsTiming.AMC, entry_time=None,
                          expiration=date(2026, 10, 16), implied_move=implied, vrp=vrp,
                          recommendation=vrp.recommendation, **columns)


def test_the_repository_writes_the_graded_convention_from_the_result(pg_pool):
    with pg_pool.connection() as c:
        c.execute(baseline_ddl("analysis_log"))
        c.commit()
    repo = AnalysisRepository(pool=pg_pool)
    # The TickerAnalysis's own close columns are absent: the writer must not need them.
    repo.log_analysis(_analysis(_vrp("close", 1.5, 4.0), vrp_intraday_ratio=3.0,
                                historical_intraday_mean_pct=2.0), source="analyze")
    with pg_pool.connection() as c:
        row = c.execute("SELECT vrp_convention, vrp_close_ratio, historical_close_mean_pct,"
                        " vrp_intraday_ratio, historical_intraday_mean_pct, recommendation"
                        " FROM analysis_log").fetchone()
    assert tuple(row) == ("close", 1.5, 4.0, 3.0, 2.0, "good")


def test_the_repository_writes_the_graded_intraday_convention_from_the_result(pg_pool):
    """R1 insurance: a rollback to intraday grading (Rollback R1) keeps schema v6 and
    this same writer -- it must fill the graded convention from the VRPResult when
    move_metric is 'intraday' too, not only 'close'."""
    with pg_pool.connection() as c:
        c.execute(baseline_ddl("analysis_log"))
        c.commit()
    repo = AnalysisRepository(pool=pg_pool)
    # The TickerAnalysis's own intraday columns are absent: the writer must not need them.
    repo.log_analysis(_analysis(_vrp("intraday", 3.0, 2.0), vrp_close_ratio=1.5,
                                historical_close_mean_pct=4.0), source="analyze")
    with pg_pool.connection() as c:
        row = c.execute("SELECT vrp_convention, vrp_close_ratio, historical_close_mean_pct,"
                        " vrp_intraday_ratio, historical_intraday_mean_pct, recommendation"
                        " FROM analysis_log").fetchone()
    assert tuple(row) == ("intraday", 1.5, 4.0, 3.0, 2.0, "good")
