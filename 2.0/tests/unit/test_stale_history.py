"""stale_history_warning -- the live, per-ticker version of integrity_report.py's
quarter_missing_from_calendar_and_moves check.

LEN had 12 quarters on file and cleared min_historical_quarters comfortably, but
its two most recent (2026-03-12, 2026-06-11) were missing entirely, so VRP/TRR
ran on a six-month-stale window with nobody the wiser until someone read the
last-4-quarters table by eye. This pins the boundary against
common.constants.QUARTER_GAP_DAYS, the same threshold the weekly sweep uses, so
the two can't drift apart.
"""

from dataclasses import dataclass
from datetime import date

from common.constants import QUARTER_GAP_DAYS
from src.application.services.stale_history import stale_history_warning


@dataclass
class _Move:
    earnings_date: date


def test_no_warning_with_no_history():
    assert stale_history_warning("LEN", [], date(2026, 9, 16)) is None


def test_no_warning_exactly_at_the_threshold():
    most_recent = date(2026, 9, 16) - date.resolution * QUARTER_GAP_DAYS
    assert stale_history_warning("LEN", [_Move(most_recent)], date(2026, 9, 16)) is None


def test_warning_one_day_past_the_threshold():
    most_recent = date(2026, 9, 16) - date.resolution * (QUARTER_GAP_DAYS + 1)
    warning = stale_history_warning("LEN", [_Move(most_recent)], date(2026, 9, 16))
    assert warning is not None
    assert "LEN" in warning
    assert str(most_recent) in warning


def test_only_the_most_recent_quarter_is_compared():
    # DESC by earnings_date (prices_repository.get_historical_moves's order) --
    # the first element is what matters, not the oldest in the window. A very
    # old second entry must not drag the result stale on its own.
    moves = [_Move(date(2026, 8, 20)), _Move(date(2020, 3, 19))]
    assert stale_history_warning("LEN", moves, date(2026, 9, 16)) is None


def test_a_sixteen_week_fourth_quarter_is_not_stale():
    # Costco's fiscal Q4 runs 16 weeks: 2026-05-28 -> 2026-09-24 is 119 days with
    # no report in between (Yahoo, SEC 8-K). The 115-day line warned on it.
    assert stale_history_warning("COST", [_Move(date(2026, 5, 28))], date(2026, 9, 24)) is None


def test_one_lost_quarter_is_stale():
    # A single missing quarter spans two report intervals, ~180 days.
    warning = stale_history_warning("LEN", [_Move(date(2026, 3, 12))], date(2026, 9, 8))
    assert warning is not None
    assert "180" in warning


def test_lens_actual_gap_before_the_2026_09_16_backfill():
    moves = [_Move(date(2025, 12, 16))]  # before Mar/Jun 2026 were backfilled
    warning = stale_history_warning("LEN", moves, date(2026, 9, 16))
    assert warning is not None
    assert "274" in warning  # (2026-09-16 - 2025-12-16).days


def test_message_names_a_working_backfill_command():
    moves = [_Move(date(2025, 12, 16))]
    warning = stale_history_warning("LEN", moves, date(2026, 9, 16))
    assert "backfill_historical.py LEN --discover" in warning
    assert "--execute" in warning
