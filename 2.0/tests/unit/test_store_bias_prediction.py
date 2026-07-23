"""Unit tests for resolve_compound_risk_context — ORATS gate (Jul 2026 audit).

When ORATS_ENABLED is false, the frozen position_limits snapshot (2026-06-24)
must NOT feed sizing_alarm / r_slp_30, and the stored fused bias must come
from the Tradier slope_atm proxy at reduced confidence — matching what
analyzer._fuse_skew_signals shows in /analyze output.
"""

import sqlite3
import sys
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.domain.enums import DirectionalBias
from scripts.store_bias_prediction import analyze_and_store, resolve_compound_risk_context


@pytest.fixture
def frozen_db(tmp_path):
    """DB with ORATS snapshot rows that would fire every alarm.

    'TEST' has a fresh last_updated; 'STALE' is 30 days old (simulates
    flipping ORATS_ENABLED=true without re-running the snapshot refresh).
    """
    from datetime import datetime, timedelta
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE position_limits ("
        "ticker TEXT PRIMARY KEY, r_slp_30 REAL, "
        "fcst_ern_iv_effect REAL, iee_earn_effect REAL, last_updated TEXT)"
    )
    fresh = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    stale = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
    conn.execute(
        "INSERT INTO position_limits VALUES ('TEST', -2.0, 2.5, 4.0, ?)", (fresh,)
    )
    conn.execute(
        "INSERT INTO position_limits VALUES ('STALE', -2.0, 2.5, 4.0, ?)", (stale,)
    )
    conn.commit()
    conn.close()
    return str(db)


def _make_skew(bias=DirectionalBias.BEARISH, conf=0.6, slope_atm=-150.0):
    skew = Mock()
    skew.directional_bias = bias
    skew.bias_confidence = conf
    skew.slope_atm = slope_atm
    return skew


class TestOratsDisabled:
    def test_ignores_frozen_position_limits(self, frozen_db):
        r_slp_30, _, sizing_alarm = resolve_compound_risk_context(
            frozen_db, 'TEST', _make_skew(), orats_enabled=False
        )
        assert r_slp_30 is None
        assert sizing_alarm is False

    def test_fused_bias_uses_tradier_proxy(self, frozen_db):
        # BEARISH (-2) at conf 0.6 + proxy(-150) WEAK_BEARISH (-1) at conf 0.3
        # -> fused = -1.67 -> round(-2) -> BEARISH; same as analyzer proxy path
        _, fused, _ = resolve_compound_risk_context(
            frozen_db, 'TEST', _make_skew(slope_atm=-150.0), orats_enabled=False
        )
        assert fused in ('bearish', 'strong_bearish')

    def test_no_slope_atm_no_fused_bias(self, frozen_db):
        _, fused, _ = resolve_compound_risk_context(
            frozen_db, 'TEST', _make_skew(slope_atm=None), orats_enabled=False
        )
        assert fused is None


class TestPredictionExpiration:
    """Expiration must come from the trading calendar + Tradier snap, not
    earnings_date + 2 days (which lands on Saturday for Thursday reporters
    and non-listed Thursday for Tuesday reporters — Jul 2026 audit finding).

    Mirrors analyzer Step 0: desired = calculate_implied_move_expiration
    (first post-earnings trading day), snapped via find_nearest_expiration.
    """

    def _drive(self, earnings_date, snap_to=None, snap_ok=True):
        container = Mock()
        snap_result = Mock()
        snap_result.is_ok = snap_ok
        snap_result.value = snap_to
        container.tradier.find_nearest_expiration.return_value = snap_result
        skew_result = Mock()
        skew_result.is_err = True
        skew_result.error = 'NODATA'
        container.skew_analyzer.analyze_skew_curve.return_value = skew_result
        analyze_and_store(container, 'TEST', earnings_date, 'unused.db')
        desired = container.tradier.find_nearest_expiration.call_args[0][1]
        used = container.skew_analyzer.analyze_skew_curve.call_args[0][1]
        return desired, used

    def test_thursday_reporter_never_targets_saturday(self):
        # GE case: Thu Jul 16 + 2d = Saturday Jul 18 (pre-fix bug)
        desired, _ = self._drive(date(2026, 7, 16), snap_to=date(2026, 7, 17))
        assert desired == date(2026, 7, 17)  # next trading day, a Friday

    def test_tuesday_reporter_targets_next_trading_day(self):
        # JPM case: Tue Jul 14 -> desired Wed Jul 15 (snap resolves listing)
        desired, _ = self._drive(date(2026, 7, 14), snap_to=date(2026, 7, 17))
        assert desired == date(2026, 7, 15)

    def test_skew_uses_snapped_expiration(self):
        _, used = self._drive(date(2026, 7, 14), snap_to=date(2026, 7, 17))
        assert used == date(2026, 7, 17)

    def test_snap_failure_falls_back_to_desired(self):
        _, used = self._drive(date(2026, 7, 14), snap_ok=False)
        assert used == date(2026, 7, 15)


class TestOratsEnabled:
    def test_reads_snapshot_and_fires_alarm(self, frozen_db):
        r_slp_30, fused, sizing_alarm = resolve_compound_risk_context(
            frozen_db, 'TEST', _make_skew(), orats_enabled=True
        )
        assert r_slp_30 == -2.0
        assert sizing_alarm is True
        assert fused in ('bearish', 'strong_bearish')

    def test_stale_snapshot_row_ignored(self, frozen_db):
        # 30-day-old row: guard treats it as absent — no alarm, no r_slp_30,
        # fused bias from proxy (resubscription-without-refresh safety)
        r_slp_30, fused, sizing_alarm = resolve_compound_risk_context(
            frozen_db, 'STALE', _make_skew(slope_atm=-150.0), orats_enabled=True
        )
        assert r_slp_30 is None
        assert sizing_alarm is False
        assert fused in ('bearish', 'strong_bearish')

    def test_missing_ticker_falls_back_to_proxy(self, frozen_db):
        # No position_limits row: no alarm, no r_slp_30, but fused bias
        # still computed from the Tradier proxy (mirrors analyzer fallback)
        r_slp_30, fused, sizing_alarm = resolve_compound_risk_context(
            frozen_db, 'NOPE', _make_skew(slope_atm=-150.0), orats_enabled=True
        )
        assert r_slp_30 is None
        assert sizing_alarm is False
        assert fused in ('bearish', 'strong_bearish')
