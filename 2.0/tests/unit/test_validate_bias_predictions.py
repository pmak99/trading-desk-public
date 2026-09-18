"""scripts/validate_bias_predictions.py against real Postgres.

Two defects, each tested here.

1. Migration 2 made `bias_predictions.prediction_correct` a boolean. The validator
   kept binding `1 if correct else 0`, which Postgres rejects, so from 2026-09-13
   every validation failed at the UPDATE and rolled back.
2. The grade measured its own yfinance bars. Before 2026-09-04 it read an AMC
   report's move one session early -- the pre-report drift -- and those grades were
   never revisited: on 2026-09-13, 18 of 120 validated rows named the opposite of
   both the gap and the close in `historical_moves` (NVDA 2026-08-26: stored DOWN,
   gap +6.3%). After the fix a prediction dated a day off its report still found no
   timing and was guessed AMC, and trade.sh graded mid-session. It now grades the
   `historical_moves` reaction, matched and screened the way the compound-risk
   recalibration matches and screens it (src/domain/reaction_session.py), and
   fetches nothing.
"""

from datetime import date, datetime

import pytest

from common.db import connect  # noqa: E402
from common.pgtesting import baseline_ddl  # noqa: E402
from src.domain.reaction_session import ET

pytestmark = pytest.mark.pg


@pytest.fixture
def db(pg_schema):
    with connect() as conn:
        conn.execute(baseline_ddl("bias_predictions", "earnings_calendar", "historical_moves"))
        conn.commit()
    return pg_schema


def _event(ticker, day, timing, prev_close=None, open_=None, close=None):
    """A calendar row, and its move row when prices are given."""
    with connect() as conn:
        conn.execute("INSERT INTO earnings_calendar (ticker, earnings_date, timing) VALUES (%s, %s, %s)",
                     (ticker, day, timing))
        if prev_close is not None:
            conn.execute(
                "INSERT INTO historical_moves (ticker, earnings_date, prev_close, earnings_open,"
                " earnings_high, earnings_low, earnings_close) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (ticker, day, prev_close, open_, max(open_, close), min(open_, close), close))
        conn.commit()


def _prediction(ticker, day, predicted_at, bias="neutral", graded=None):
    """A bias_predictions row. `graded` = (direction, move, gap, correct) stores a grade."""
    with connect() as conn:
        pid = conn.execute(
            "INSERT INTO bias_predictions (ticker, earnings_date, expiration, stock_price,"
            " predicted_at, skew_atm, skew_curvature, skew_strength, slope_atm,"
            " directional_bias, bias_strength, bias_confidence, r_squared, num_points)"
            " VALUES (%s, %s, %s, 100, %s, 0.1, 0.1, 'smirk', -1.0, %s, 2, 0.6, 0.9, 7)"
            " RETURNING id", (ticker, day, day, predicted_at, bias)).fetchone()[0]
        if graded:
            direction, move, gap, correct = graded
            conn.execute(
                "UPDATE bias_predictions SET actual_direction = %s, actual_move_pct = %s,"
                " actual_gap_pct = %s, prediction_correct = %s, validated_at = %s WHERE id = %s",
                (direction, move, gap, correct, datetime(2026, 8, 27, 7, 0, tzinfo=ET), pid))
        conn.commit()
    return pid


def _row(pid):
    with connect() as conn:
        return conn.execute(
            "SELECT actual_direction, round(actual_move_pct::numeric, 2)::float move,"
            " round(actual_gap_pct::numeric, 2)::float gap, prediction_correct,"
            " validated_at IS NOT NULL validated FROM bias_predictions WHERE id = %s",
            (pid,)).fetchone()


def _run(capsys, *argv):
    import scripts.validate_bias_predictions as vbp

    assert vbp.main(list(argv)) == 0
    return capsys.readouterr().out


def et(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


# --- 2. the reaction historical_moves recorded ---------------------------------------

def test_nothing_is_fetched():
    import scripts.validate_bias_predictions as vbp

    assert not hasattr(vbp, "yf")
    assert not hasattr(vbp, "yahoo_call")


def test_an_amc_prediction_grades_the_next_session_reaction_from_historical_moves(db, capsys):
    """NVDA 2026-08-26 AMC: the recorded reaction gapped +6.3% and closed +8.74%."""
    _event("NVDA", "2026-08-26", "AMC", prev_close=100.0, open_=106.30, close=108.74)
    bullish = _prediction("NVDA", "2026-08-26", et(2026, 8, 26, 8, 49), bias="bullish")

    _run(capsys, "--execute")
    assert _row(bullish) == ("UP", 8.74, 6.3, True, True)


@pytest.mark.parametrize("bias, correct", [("neutral", False), ("bearish", False),
                                           ("strong_bullish", True)])
def test_the_verdict_is_a_boolean_bind(db, capsys, bias, correct):
    _event("TEST", "2026-09-01", "AMC", prev_close=100.0, open_=103.0, close=104.2)
    pid = _prediction("TEST", "2026-09-01", et(2026, 9, 1, 10, 0), bias=bias)

    _run(capsys, "--execute")
    assert _row(pid).prediction_correct is correct


def test_a_close_inside_half_a_percent_is_flat_and_only_neutral_calls_it(db, capsys):
    _event("FLAT", "2026-09-01", "BMO", prev_close=100.0, open_=101.0, close=100.49)
    neutral = _prediction("FLAT", "2026-09-01", et(2026, 8, 31, 12, 0), bias="neutral")

    _run(capsys, "--execute")
    assert _row(neutral) == ("FLAT", 0.49, 1.0, True, True)


@pytest.mark.parametrize("close_pct, direction", [(0.49, "FLAT"), (-0.49, "FLAT"), (0.5, "UP"),
                                                  (-0.5, "DOWN"), (0.0, "FLAT")])
def test_direction_band(close_pct, direction):
    import scripts.validate_bias_predictions as vbp

    assert vbp.direction_of(close_pct) == direction


def test_a_prediction_dated_off_its_report_grades_the_nearest_reaction(db, capsys):
    """HPQ: predicted for 2026-08-26 at 08:41; the report was 2026-08-25 AMC, so the
    prediction still came before the 08-26 open. The old exact-date timing lookup
    found nothing and guessed AMC on 08-26 -- a session late."""
    _event("HPQ", "2026-08-25", "AMC", prev_close=29.52, open_=29.42, close=30.52)
    pid = _prediction("HPQ", "2026-08-26", et(2026, 8, 26, 8, 41), bias="bullish")

    _run(capsys, "--execute")
    assert _row(pid) == ("UP", 3.39, -0.34, True, True)


def test_a_prediction_taken_after_the_reaction_open_is_measured_but_not_graded(db, capsys):
    """DAL: the scan dated it 2026-01-21 and predicted on 01-16; the report was
    01-13 BMO. A read of the chain after the reaction is not a forecast."""
    _event("DAL", "2026-01-13", "BMO", prev_close=100.0, open_=101.47, close=102.21)
    pid = _prediction("DAL", "2026-01-21", et(2026, 1, 16, 11, 21), bias="neutral")

    out = _run(capsys, "--execute")
    assert _row(pid) == ("UP", 2.21, 1.47, None, True)
    assert "not a forecast" in out


def test_a_prediction_with_no_reaction_row_waits(db, capsys):
    _event("WAIT", "2026-09-10", "AMC")
    pid = _prediction("WAIT", "2026-09-10", et(2026, 9, 10, 9, 0), bias="bullish")

    out = _run(capsys, "--execute")
    assert _row(pid) == (None, None, None, None, False)
    assert "no historical_moves reaction" in out


def test_a_reaction_beyond_the_join_window_is_not_this_prediction_s(db, capsys):
    _event("FAR", "2026-06-01", "AMC", prev_close=100.0, open_=110.0, close=112.0)
    pid = _prediction("FAR", "2026-08-01", et(2026, 7, 31, 9, 0), bias="bullish")

    _run(capsys, "--execute")
    assert _row(pid).validated is False


# --- report-only unless --execute ------------------------------------------------------

def test_report_only_writes_nothing(db, capsys):
    _event("NVDA", "2026-08-26", "AMC", prev_close=100.0, open_=106.30, close=108.74)
    pid = _prediction("NVDA", "2026-08-26", et(2026, 8, 26, 8, 49), bias="bullish")

    out = _run(capsys)
    assert _row(pid) == (None, None, None, None, False)
    assert "REPORT ONLY" in out and "NVDA" in out


def test_already_validated_rows_are_left_alone_without_regrade(db, capsys):
    _event("NVDA", "2026-08-26", "AMC", prev_close=100.0, open_=106.30, close=108.74)
    pid = _prediction("NVDA", "2026-08-26", et(2026, 8, 26, 8, 49), bias="neutral",
                      graded=("DOWN", -1.59, -0.19, False))

    _run(capsys, "--execute")
    assert _row(pid) == ("DOWN", -1.59, -0.19, False, True)


# --- --regrade: the stored grades the old measurement wrote ------------------------------

@pytest.fixture
def stale_grades(db):
    # NVDA 2026-08-26: graded on 08-26's own session before the AMC fix.
    _event("NVDA", "2026-08-26", "AMC", prev_close=100.0, open_=106.30, close=108.74)
    wrong = _prediction("NVDA", "2026-08-26", et(2026, 8, 26, 8, 49), bias="bullish",
                        graded=("DOWN", -1.59, -0.19, False))
    # BAC 2026-01-14 BMO: the old measurement was right; regrading changes nothing.
    _event("BAC", "2026-01-14", "BMO", prev_close=100.0, open_=96.96, close=96.22)
    right = _prediction("BAC", "2026-01-14", et(2026, 1, 14, 3, 41), bias="strong_bullish",
                        graded=("DOWN", -3.78, -3.04, False))
    # SEDG 2026-02-18: graded, but no reaction was ever recorded.
    _event("SEDG", "2026-02-18", "AMC")
    orphan = _prediction("SEDG", "2026-02-18", et(2026, 2, 13, 5, 41), bias="neutral",
                         graded=("DOWN", -5.47, 8.43, False))
    return wrong, right, orphan


def test_regrade_reports_every_change_and_writes_nothing(stale_grades, capsys):
    wrong, right, orphan = stale_grades

    out = _run(capsys, "--regrade")
    assert _row(wrong) == ("DOWN", -1.59, -0.19, False, True)
    assert _row(orphan).validated is True
    assert "DOWN -> UP" in out
    assert "verdict flipped: 1" in out
    assert "cleared (no reaction row): 1" in out
    assert "unchanged: 1" in out


def test_regrade_with_execute_rewrites_and_clears(stale_grades, capsys):
    wrong, right, orphan = stale_grades

    _run(capsys, "--regrade", "--execute")
    assert _row(wrong) == ("UP", 8.74, 6.3, True, True)
    assert _row(right) == ("DOWN", -3.78, -3.04, False, True)
    assert _row(orphan) == (None, None, None, None, False)


def test_the_ticker_and_date_filters_scope_the_run(stale_grades, capsys):
    wrong, right, orphan = stale_grades

    _run(capsys, "NVDA", "--regrade", "--execute")
    assert _row(wrong).actual_direction == "UP"
    assert _row(orphan).validated is True

    _run(capsys, "--regrade", "--execute", "--start", "2026-02-01", "--end", "2026-02-28")
    assert _row(orphan).validated is False
    assert _row(right) == ("DOWN", -3.78, -3.04, False, True)


def test_grading_is_a_pure_function_of_the_prediction_and_its_reactions():
    import scripts.validate_bias_predictions as vbp

    reactions = [{"earnings_date": date(2026, 8, 25), "gap": -0.34, "cm": 3.39, "timing": "AMC"},
                 {"earnings_date": date(2026, 5, 27), "gap": 3.1, "cm": -1.88, "timing": "AMC"}]
    p = {"earnings_date": date(2026, 8, 26), "predicted_at": et(2026, 8, 26, 8, 41),
         "directional_bias": "weak_bullish"}
    g = vbp.grade(p, reactions)
    assert (g.reaction_date, g.direction, g.correct) == (date(2026, 8, 25), "UP", True)

    late = {**p, "predicted_at": et(2026, 8, 26, 9, 30)}       # the reaction open itself
    assert vbp.grade(late, reactions).correct is None
    assert vbp.grade({**p, "earnings_date": date(2026, 12, 1)}, reactions) is None
