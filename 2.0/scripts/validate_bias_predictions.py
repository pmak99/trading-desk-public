#!/usr/bin/env python3
"""
Grade directional bias predictions against the reaction historical_moves recorded.

Report-only unless --execute is passed.

WHY IT READS historical_moves (2026-09-14)
------------------------------------------
This script used to measure each reaction itself, from yfinance bars, and that
measurement is where the bad grades came from:

  - Until 2026-09-04 it read close-to-close ending ON the report date: right for
    BMO, one session early for AMC -- the pre-report drift, not the reaction. The
    fix only reached rows validated after it, because a graded row is never
    selected again. On 2026-09-13, 18 of 120 validated rows named the opposite of
    both the gap and the close in historical_moves (NVDA 2026-08-26 AMC: stored
    DOWN, gap +6.3%, close +8.7%).
  - After it, the timing came from an exact-date calendar lookup, so a prediction
    dated a day off its report found none and was guessed AMC; UNKNOWN and DMH were
    guessed AMC too; trade.sh runs this at any hour, so an AMC reaction could be
    graded mid-session; and closes were dividend-adjusted.

Now nothing is fetched. A prediction is graded against the historical_moves row
nearest its earnings_date within JOIN_DAYS (src/domain/reaction_session.py -- the
match the compound-risk recalibration makes), using that row's close_move_pct and
gap_move_pct: the reference and reaction sessions record_moves or the backfill
measured under the event's timing. A prediction with no such row waits; record_moves
appends the row once the reaction session is complete and Yahoo corroborates the
timing.

A prediction taken at or after its reaction session's open (reaction_session.
is_pre_open, by the reaction's stored timing) read a chain that had already
reacted. It is not a forecast: the move is recorded and prediction_correct stays
NULL. store_bias_prediction refuses such writes since 2026-09-14; older rows hold
them -- mostly scan predictions dated a week late.

  direction  close_move_pct: FLAT inside +/-0.5%, else UP or DOWN
  correct    bullish -> UP, bearish -> DOWN, neutral -> FLAT

--regrade grades every past prediction again, including those already validated,
and prints each change. A validated row with no reaction row is cleared back to
ungraded, so it waits like any other.

Usage (from 2.0/):
    python scripts/validate_bias_predictions.py                  # report unvalidated
    python scripts/validate_bias_predictions.py --execute        # grade them (trade.sh)
    python scripts/validate_bias_predictions.py AAPL --execute
    python scripts/validate_bias_predictions.py --start 2025-11-01 --end 2025-11-30
    python scripts/validate_bias_predictions.py --regrade        # report every change
"""

import argparse
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Optional

from common.db import connect  # noqa: E402
from common.timezone import now_et
from src.domain.reaction_session import JOIN_DAYS, is_pre_open, nearest_reaction

FLAT_BAND_PCT = 0.5


def direction_of(close_move_pct: float) -> str:
    if abs(close_move_pct) < FLAT_BAND_PCT:
        return "FLAT"
    return "UP" if close_move_pct > 0 else "DOWN"


def is_prediction_correct(bias: str, direction: str) -> bool:
    """Bullish biases call UP, bearish DOWN, neutral FLAT (strict)."""
    if "bullish" in bias.lower():
        return direction == "UP"
    if "bearish" in bias.lower():
        return direction == "DOWN"
    if "neutral" in bias.lower():
        return direction == "FLAT"
    return False


@dataclass(frozen=True)
class Grade:
    reaction_date: date
    timing: Optional[str]
    move_pct: float                  # historical_moves.close_move_pct
    gap_pct: float                   # historical_moves.gap_move_pct
    direction: str
    correct: Optional[bool]          # None: taken at or after the reaction open


def grade(prediction: dict, reactions: List[dict]) -> Optional[Grade]:
    """The prediction's grade, or None when it has no reaction row yet. Pure.

    prediction: earnings_date, predicted_at (tz-aware), directional_bias.
    reactions:  the ticker's historical_moves rows as dicts of earnings_date,
                gap, cm and timing (earnings_calendar's tag for that date).
    """
    day, _ = nearest_reaction(prediction["earnings_date"], [r["earnings_date"] for r in reactions])
    if day is None:
        return None
    r = next(r for r in reactions if r["earnings_date"] == day)
    direction = direction_of(r["cm"])
    correct = (is_prediction_correct(prediction["directional_bias"], direction)
               if is_pre_open(prediction["predicted_at"], day, r["timing"]) else None)
    return Grade(day, r["timing"], r["cm"], r["gap"], direction, correct)


PREDICTION_QUERY = """
SELECT id, ticker, earnings_date, predicted_at, directional_bias,
       actual_move_pct, actual_gap_pct, actual_direction, prediction_correct,
       validated_at IS NOT NULL AS validated
FROM bias_predictions
WHERE earnings_date < CURRENT_DATE
  AND (%(regrade)s OR validated_at IS NULL)
  AND (%(ticker)s::text IS NULL OR ticker = %(ticker)s)
  AND (%(start)s::date IS NULL OR earnings_date >= %(start)s)
  AND (%(end)s::date IS NULL OR earnings_date <= %(end)s)
ORDER BY earnings_date, ticker, id
"""

REACTION_QUERY = """
SELECT h.ticker, h.earnings_date, h.gap_move_pct gap, h.close_move_pct cm, ec.timing
FROM historical_moves h
LEFT JOIN earnings_calendar ec ON ec.ticker = h.ticker AND ec.earnings_date = h.earnings_date
WHERE h.ticker = ANY(%s)
ORDER BY h.ticker, h.earnings_date
"""

GRADE_SQL = """
UPDATE bias_predictions
SET actual_move_pct = %s, actual_gap_pct = %s, actual_direction = %s,
    prediction_correct = %s, validated_at = %s
WHERE id = %s
"""

CLEAR_SQL = """
UPDATE bias_predictions
SET actual_move_pct = NULL, actual_gap_pct = NULL, actual_direction = NULL,
    prediction_correct = NULL, validated_at = NULL
WHERE id = %s
"""


def _verdict(correct: Optional[bool]) -> str:
    return {True: "correct", False: "wrong", None: "not a forecast"}[correct]


def _stored(p: dict) -> str:
    if not p["validated"]:
        return "ungraded"
    move = "n/a" if p["actual_move_pct"] is None else f"{p['actual_move_pct']:+.2f}%"
    return f"{p['actual_direction']} {_verdict(p['prediction_correct'])} (close {move})"


def _change(p: dict, g: Grade) -> str:
    """How a validated row's stored grade differs from its grade now."""
    if (p["actual_direction"], p["prediction_correct"]) == (g.direction, g.correct):
        return "unchanged"
    if g.correct is None:
        return "now not a forecast"
    if p["prediction_correct"] != g.correct:
        return "verdict flipped"
    return "direction changed"


CHANGES = ("unchanged", "verdict flipped", "direction changed", "now not a forecast",
           "cleared (no reaction row)")


def run(conn, predictions: List[dict], reactions: dict, *, execute: bool, regrade: bool,
        now: datetime) -> List[str]:
    """Grade each prediction; with execute, write every row whose stored values differ."""
    lines, counts, changes = [], Counter(), Counter()
    for p in predictions:
        g = grade(p, reactions.get(p["ticker"], []))
        head = f"{p['ticker']:6} {p['earnings_date']}  {p['directional_bias']:15}"
        if g is None:
            if p["validated"]:
                changes["cleared (no reaction row)"] += 1
                counts["written"] += 1
                lines.append(f"  {head} no historical_moves reaction within {JOIN_DAYS} days"
                             f" -- stored {_stored(p)}: cleared")
                if execute:
                    conn.execute(CLEAR_SQL, (p["id"],))
            else:
                counts["waiting"] += 1
                lines.append(f"  {head} no historical_moves reaction within {JOIN_DAYS} days yet -- waits")
            continue

        counts["graded"] += 1
        counts[_verdict(g.correct)] += 1
        line = (f"  {head} reaction {g.reaction_date} {g.timing or 'no tag':7} close {g.move_pct:+7.2f}%"
                f" gap {g.gap_pct:+7.2f}% -> {g.direction:4} {_verdict(g.correct)}")
        if p["validated"]:
            change = _change(p, g)
            changes[change] += 1
            if change != "unchanged":
                line += f"   [{change}: {p['actual_direction']} -> {g.direction}; was {_stored(p)}]"
        lines.append(line)
        stored = (p["actual_move_pct"], p["actual_gap_pct"], p["actual_direction"], p["prediction_correct"])
        if not p["validated"] or stored != (g.move_pct, g.gap_pct, g.direction, g.correct):
            counts["written"] += 1
            if execute:
                # A bool or None, never 1/0: prediction_correct is boolean (migration 2).
                # now_et() is tz-aware: validated_at is timestamptz.
                conn.execute(GRADE_SQL, (g.move_pct, g.gap_pct, g.direction, g.correct, now, p["id"]))

    forecasts = counts["correct"] + counts["wrong"]
    lines.append("")
    lines.append(f"  Graded {counts['graded']}: correct {counts['correct']}, wrong {counts['wrong']}, "
                 f"not a forecast {counts['not a forecast']}; waiting for a reaction row "
                 f"{counts['waiting']}")
    if forecasts:
        lines.append(f"  Accuracy of these forecasts: {counts['correct'] / forecasts:.1%} "
                     f"({counts['correct']}/{forecasts})")
    if regrade:
        lines.append("  Against the stored grades: " + ", ".join(f"{k}: {changes[k]}" for k in CHANGES))
    lines.append(f"  Rows {'written' if execute else 'to write'}: {counts['written']}")
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Grade bias predictions against their historical_moves reaction. "
                    "Report only unless --execute.")
    parser.add_argument("ticker", nargs="?", help="only this ticker")
    parser.add_argument("--start", type=date.fromisoformat, help="earliest earnings_date (YYYY-MM-DD)")
    parser.add_argument("--end", type=date.fromisoformat, help="latest earnings_date (YYYY-MM-DD)")
    parser.add_argument("--regrade", action="store_true",
                        help="grade already-validated predictions again and show each change; "
                             "a validated row with no reaction row is cleared")
    parser.add_argument("--execute", action="store_true",
                        help="write the grades (default: report only)")
    args = parser.parse_args(argv)

    mode = ("REGRADE" if args.regrade else "VALIDATION") + (
        "" if args.execute else "  [REPORT ONLY - pass --execute to write]")
    print("=" * 78)
    print(f"BIAS PREDICTION {mode}")
    print("=" * 78)

    with connect() as conn:
        if not args.execute:
            conn.read_only = True
        predictions = [r._asdict() for r in conn.execute(PREDICTION_QUERY, {
            "regrade": args.regrade, "ticker": args.ticker.upper() if args.ticker else None,
            "start": args.start, "end": args.end})]
        if not predictions:
            print("\nNo predictions to grade")
            return 0
        reactions = defaultdict(list)
        for r in conn.execute(REACTION_QUERY, (sorted({p["ticker"] for p in predictions}),)):
            reactions[r.ticker].append(r._asdict())

        print(f"\n{len(predictions)} prediction(s)\n")
        for line in run(conn, predictions, reactions, execute=args.execute,
                        regrade=args.regrade, now=now_et()):
            print(line)
        if args.execute:
            conn.commit()
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
