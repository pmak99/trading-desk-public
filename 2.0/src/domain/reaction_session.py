"""Which reaction a logged earnings date belongs to, when an analysis of it stops
being a forecast (the reaction session's open), and when its move can be measured
(the reaction session's close).

One rule for every caller. The live A/B evaluator, the compound-risk recalibration
and the bias-prediction validator match a logged date to its `historical_moves`
reaction with `nearest_reaction` and grade only rows taken before that reaction's
open (scripts/ab_evaluate_live.py, scripts/research/compound_risk_recalibration.py,
scripts/validate_bias_predictions.py), and the bias-prediction writer refuses to
write at or after it (scripts/store_bias_prediction.py). Written once so the writer
can never keep a row a grader would throw away, and no two graders pair a row with
different reactions.

The move writers measure a reaction from its session's daily bar, so they wait for
that session to close: the shared calculator (scripts/earnings_moves.py) returns no
move before `is_reaction_complete`, and jobs/record_moves.py takes its reaction
session from `reaction_session`.
"""

from datetime import datetime, time

from common.timezone import MARKET_TZ as ET  # noqa: E402
from src.domain.market_calendar import (
    adjust_to_trading_day,
    next_trading_day,
    session_close,
)

MARKET_OPEN = time(9, 30)

# Date-join tolerance: a logged earnings_date (analysis_log, bias_predictions) is a
# snapshot at analysis time and earnings_calendar can be corrected afterward.
# Widened 3->30 days Sep 4 2026, after real date corrections up to 20 days off were
# found still being dropped by the previous 3-day window. Adjacent quarters are ~90
# days apart, so 30 cannot reach the next report.
JOIN_DAYS = 30


def reaction_open_cutoff(report_date, timing):
    """The instant an analysis stops being a forecast of this report.

    BMO: the report date's 09:30 ET open. AMC: 09:30 ET at the next NYSE session
    (src/domain/market_calendar.py). DMH, UNKNOWN or no calendar row: the report
    date's open -- the earliest open that could be the reaction, so an uncertain
    tag errs toward exclusion.

    ⚠️ Until 2026-09-14 AMC took the next weekday, so an AMC report before a
    market holiday was cut off at the closed day's 09:30 and a log taken on the
    holiday, before the real reaction, was dropped as post-open.

    The tag is `earnings_calendar.timing` for the `historical_moves` row being
    graded, which the backfill writes with the timing it measured the move under.
    A wrong tag moves the cutoff by one session.
    """
    day = next_trading_day(report_date) if timing == "AMC" else report_date
    return datetime.combine(day, MARKET_OPEN, tzinfo=ET)


def is_pre_open(ts, report_date, timing):
    return ts < reaction_open_cutoff(report_date, timing)


def reaction_session(report_date, timing):
    """The latest session that can hold this report's reaction.

    BMO: the report date (the next session, if the date is not one). AMC: the
    next session. DMH, UNKNOWN or no tag: the next session -- an uncertain tag
    waits until even its latest possible reaction is done.
    """
    if timing == "BMO":
        return adjust_to_trading_day(report_date)
    return next_trading_day(report_date)


def reaction_close(report_date, timing):
    """The instant the reaction session closes: 16:00 ET, 13:00 on an early close."""
    day = reaction_session(report_date, timing)
    return datetime.combine(day, session_close(day), tzinfo=ET)


def is_reaction_complete(now, report_date, timing):
    """Whether the reaction session's daily bar is final at `now` (tz-aware).

    ⚠️ Until 2026-09-14 the calculator's only guard was `report_date >= today`:
    a daytime run measured a BMO report on its own still-trading session, and an
    AMC report on the next day's, and stored the partial bar as the move.
    """
    return now >= reaction_close(report_date, timing)


def nearest_reaction(logged_date, reaction_dates, join_days=JOIN_DAYS):
    """(nearest reaction date within join_days, whether it was a tie).

    An equidistant pair goes to the earlier reaction, deterministically; the old
    SQL join returned both and graded the event twice.
    """
    near = sorted((abs((d - logged_date).days), d) for d in reaction_dates
                  if abs((d - logged_date).days) <= join_days)
    if not near:
        return None, False
    return near[0][1], len(near) > 1 and near[1][0] == near[0][0]
