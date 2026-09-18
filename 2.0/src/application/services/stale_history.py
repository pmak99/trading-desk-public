"""Warn when a ticker's stored earnings history has swallowed a quarter.

CLAUDE.md's quarter-gap check (`QUARTER_GAP_DAYS`, scripts/integrity_report.py) ran
weekly and only scanned traded tickers, so it never saw a gap on a name nobody had
traded yet. LEN's 2026-03-12 and 2026-06-11 reactions sat missing from
historical_moves for six months, found only because someone ran /analyze LEN and
noticed the last-4-quarters table was all 2025 dates the day after LEN reported
(2026-09-16). VRP and TRR both window off historical_moves, so a gap like that
silently substitutes older, less relevant quarters for the ones actually missing.

This is the same check, run live on the one ticker every /analyze, scan and whisper
call already has historical_moves and earnings_date in hand for -- no extra query --
so the gap shows in the report itself instead of waiting on the weekly sweep, or a
human noticing.

A warning, never a block: VRP/TRR still compute from whatever quarters are stored.
"""

from datetime import date
from typing import Optional, Sequence

from common.constants import QUARTER_GAP_DAYS


def stale_history_warning(
    ticker: str,
    historical_moves: Sequence,
    earnings_date: date,
    gap_days: int = QUARTER_GAP_DAYS,
) -> Optional[str]:
    """None unless the most recent stored quarter is more than `gap_days` before
    `earnings_date` -- the same threshold integrity_report.py uses to flag a quarter
    missing from both `earnings_calendar` and `historical_moves`.

    `historical_moves` is `prices_repository.get_historical_moves()`'s result: DESC
    by earnings_date, most recent first. An empty list has nothing to compare --
    callers already treat zero history as NODATA before this runs.
    """
    if not historical_moves:
        return None
    most_recent = historical_moves[0].earnings_date
    gap = (earnings_date - most_recent).days
    if gap <= gap_days:
        return None
    return (
        f"{gap} days since {ticker}'s last stored quarter ({most_recent}) -- at least "
        f"one earnings event is missing from historical_moves. VRP and TRR below are "
        f"computed on a stale window. Backfill before sizing on them: "
        f"2.0/scripts/backfill_historical.py {ticker} --discover "
        f"--start-date {most_recent} --end-date {earnings_date} --execute"
    )
