"""Unit tests for scripts/taco/guard.py — IPO wind-down gate."""

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.taco.guard import WinddownPhase, entries_allowed, winddown_phase

IPO = date(2026, 10, 1)


class TestPhases:
    @pytest.mark.parametrize("today,phase", [
        (date(2026, 7, 14), WinddownPhase.CLEAR),      # T-79
        (date(2026, 8, 1), WinddownPhase.CLEAR),       # T-61
        (date(2026, 8, 2), WinddownPhase.BANNER),      # T-60 exact
        (date(2026, 8, 31), WinddownPhase.BANNER),     # T-31
        (date(2026, 9, 1), WinddownPhase.LOCKED),      # T-30 exact
        (date(2026, 9, 16), WinddownPhase.LOCKED),     # T-15
        (date(2026, 9, 17), WinddownPhase.LIQUIDATE),  # T-14 exact
        (date(2026, 10, 1), WinddownPhase.LIQUIDATE),  # IPO day
        (date(2026, 11, 1), WinddownPhase.LIQUIDATE),  # past, not released
    ])
    def test_phase_boundaries(self, today, phase):
        assert winddown_phase(today, ipo_date=IPO, released=False) == phase

    def test_released_overrides_everything(self):
        assert (winddown_phase(date(2026, 9, 20), ipo_date=IPO, released=True)
                == WinddownPhase.RELEASED)


class TestEntriesAllowed:
    def test_allowed_in_clear_and_banner(self):
        assert entries_allowed(date(2026, 7, 14), ipo_date=IPO,
                               released=False)[0]
        assert entries_allowed(date(2026, 8, 15), ipo_date=IPO,
                               released=False)[0]

    def test_refused_when_locked_with_reason(self):
        ok, reason = entries_allowed(date(2026, 9, 2), ipo_date=IPO,
                                     released=False)
        assert not ok
        assert "IPO" in reason

    def test_allowed_after_release(self):
        assert entries_allowed(date(2026, 9, 2), ipo_date=IPO,
                               released=True)[0]
