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
    """Expiration must match the TRADING expiration trade.sh's live /analyze
    path actually uses — calculate_expiration_date's min-3-DTE weekly Friday
    — not calculate_implied_move_expiration's near-dated (earnings+1)
    expiration.

    These two functions are for different purposes (see date_utils.py
    docstrings): calculate_implied_move_expiration is for VRP/implied-move
    math; calculate_expiration_date is documented "for TRADING purposes
    (liquidity, strategy)" and is what trade.sh's calculate_expiration()
    shell function calls before invoking analyze.py --expiration. Using the
    wrong one here meant every ticker's bias_predictions row was fit against
    a different, much shorter-dated option chain than the one /analyze
    actually shows and trades against (e.g. 1 DTE vs 8 DTE for a Thursday
    reporter) — enough to flip both the sign and curve shape of the skew fit.
    A prior version of this test encoded the bug's behavior directly (see
    git history), which is why it went uncaught. Found Aug 27 2026 via a
    live MRVL/S divergence between bias_predictions and console output.
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

    def test_thursday_reporter_uses_next_week_friday(self):
        # Thu Jul 16 2026: Thu/Fri reporters route to next week's Friday
        # under the min-3-DTE floor, matching trade.sh's calculate_expiration
        # exactly (ground-truthed via calculate_expiration_date directly).
        desired, _ = self._drive(date(2026, 7, 16), snap_to=date(2026, 7, 24))
        assert desired == date(2026, 7, 24)

    def test_tuesday_reporter_uses_same_week_friday(self):
        # Tue Jul 14 2026: Mon/Tue reporters route to the same week's Friday.
        desired, _ = self._drive(date(2026, 7, 14), snap_to=date(2026, 7, 17))
        assert desired == date(2026, 7, 17)

    def test_skew_uses_snapped_expiration(self):
        _, used = self._drive(date(2026, 7, 14), snap_to=date(2026, 7, 17))
        assert used == date(2026, 7, 17)

    def test_snap_failure_falls_back_to_desired(self):
        _, used = self._drive(date(2026, 7, 14), snap_ok=False)
        assert used == date(2026, 7, 17)


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
