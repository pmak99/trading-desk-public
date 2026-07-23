"""Unit tests for scripts/taco/exits.py — frozen-ref exit engine.

Includes the audit-finding-1 regression: a sequence of ever-lower closes
after entry MUST trigger STOPPED. With refs recomputed daily (the bug this
spec was audited for), the stop would chase the market and never fire.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.taco.exits import ExitStatus, evaluate_exit
from scripts.taco.market_data import EntryRefs


def series(start: date, closes):
    return [(start + timedelta(days=i), float(c)) for i, c in enumerate(closes)]


CALL_REFS = EntryRefs("CALL", date(2026, 6, 26), 100.0, 90.0, None, None, 15.0)
PUT_REFS = EntryRefs("PUT", date(2026, 6, 26), 111.0, None, 111.0, 100.0, 15.0)
D0 = date(2026, 7, 1)
CALM_VIX = series(D0, [30.0, 29.0, 28.0])   # above pre_event_vix, no signal


class TestCallExits:
    def test_regression_lower_closes_trigger_stop(self):
        # THE audit regression: market keeps falling after entry -> STOPPED.
        spx = series(D0, [89.0, 87.0, 85.0])
        ev = evaluate_exit(CALL_REFS, spx, series(D0, [36.0, 38.0, 40.0]))
        assert ev.status == ExitStatus.STOPPED

    def test_retrace_target_takes_profit(self):
        # dip = 10; calibrated 50% target = 95; close at 97 -> TAKE_PROFIT
        ev = evaluate_exit(CALL_REFS, series(D0, [92.0, 95.0, 97.0]),
                           series(D0, [30.0, 26.0, 22.0]))
        assert ev.status == ExitStatus.TAKE_PROFIT
        assert ev.retrace == pytest.approx(0.7)

    def test_vix_normalized_takes_profit(self):
        # retrace short of target but VIX closed under pre_event 15.0
        ev = evaluate_exit(CALL_REFS, series(D0, [92.0, 93.0]),
                           series(D0, [20.0, 14.0]))
        assert ev.status == ExitStatus.TAKE_PROFIT
        assert "VIX" in ev.reason

    def test_vix_stalled_five_sessions_is_review(self):
        spx = series(D0, [91.0] * 6)                     # retrace ~0.1, stuck
        vix = series(D0, [16.0, 14.0, 14.5, 14.0, 13.8, 13.5])  # 5 under 15
        ev = evaluate_exit(CALL_REFS, spx, vix)
        assert ev.status == ExitStatus.REVIEW
        assert "stall" in ev.reason.lower()

    def test_giveback_review(self):
        # rebounds to 95 (armed: 5 >= 0.25*10), falls back to 92 ->
        # giveback (95-92)/(95-90) = 0.6 > 0.5 -> REVIEW
        ev = evaluate_exit(CALL_REFS, series(D0, [93.0, 95.0, 92.0]), CALM_VIX)
        assert ev.status == ExitStatus.REVIEW
        assert "giveback" in ev.reason.lower()

    def test_hold_when_nothing_fires(self):
        ev = evaluate_exit(CALL_REFS, series(D0, [90.5, 91.0, 91.5]), CALM_VIX)
        assert ev.status == ExitStatus.HOLD

    def test_invalid_refs_review(self):
        bad = EntryRefs("CALL", date(2026, 6, 26), 90.0, 95.0, None, None, 15.0)
        ev = evaluate_exit(bad, series(D0, [96.0]), CALM_VIX)
        assert ev.status == ExitStatus.REVIEW


class TestPutExits:
    def test_new_closing_high_stops(self):
        ev = evaluate_exit(PUT_REFS, series(D0, [110.0, 112.0]), CALM_VIX)
        assert ev.status == ExitStatus.STOPPED

    def test_rip_retrace_takes_profit(self):
        # rip = 111-100 = 11; calibrated 50% given back = close <= 105.5
        ev = evaluate_exit(PUT_REFS, series(D0, [108.0, 104.0]), CALM_VIX)
        assert ev.status == ExitStatus.TAKE_PROFIT

    def test_vix_normalization_is_not_a_put_signal(self):
        ev = evaluate_exit(PUT_REFS, series(D0, [110.0, 109.5]),
                           series(D0, [14.0, 13.0]))   # under pre_event_vix
        assert ev.status == ExitStatus.HOLD
