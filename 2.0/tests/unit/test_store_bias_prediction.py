"""Unit tests for resolve_compound_risk_context — ORATS gate (Jul 2026 audit).

When ORATS_ENABLED is false, the frozen position_limits snapshot (2026-06-24)
must NOT feed sizing_alarm / r_slp_30, and the stored fused bias must be the
Tradier bias unchanged — there is no second skew source post-ORATS, and the
slope_atm proxy that used to be blended in was the same fit counted twice
(deleted Sep 2026, skew_enhanced.fuse_skew_bias). Matches what
analyzer._fuse_skew_signals shows in /analyze output. Assertions are exact: a
loose `in ('bearish', 'strong_bearish')` passed with and without the blending.
"""

from datetime import date
from unittest.mock import MagicMock, Mock

import pytest

from common.db import connect
from scripts.store_bias_prediction import (
    analyze_and_store,
    resolve_compound_risk_context,
)
from src.domain.enums import DirectionalBias

pytestmark = pytest.mark.pg


@pytest.fixture
def frozen_db(pg_schema):
    """ORATS snapshot rows that would fire every alarm.

    'TEST' has a fresh last_updated; 'STALE' is 30 days old (simulates
    flipping ORATS_ENABLED=true against the permanently frozen snapshot).
    """
    from datetime import datetime, timedelta
    with connect() as conn:
        conn.execute(
            "CREATE TABLE position_limits ("
            "ticker text PRIMARY KEY, r_slp_30 double precision, "
            "fcst_ern_iv_effect double precision, iee_earn_effect double precision,"
            " last_updated timestamptz)"
        )
        conn.execute(
            "INSERT INTO position_limits VALUES ('TEST', -2.0, 2.5, 4.0, %s)",
            (datetime.now(),))
        conn.execute(
            "INSERT INTO position_limits VALUES ('STALE', -2.0, 2.5, 4.0, %s)",
            (datetime.now() - timedelta(days=30),))
        conn.commit()
    return pg_schema


def _make_skew(bias=DirectionalBias.BEARISH, conf=0.6, slope_atm=-150.0):
    skew = Mock()
    skew.directional_bias = bias
    skew.bias_confidence = conf
    skew.slope_atm = slope_atm
    return skew


class TestOratsDisabled:
    def test_ignores_frozen_position_limits(self, frozen_db):
        r_slp_30, _, sizing_alarm = resolve_compound_risk_context(
            'TEST', _make_skew(), orats_enabled=False
        )
        assert r_slp_30 is None
        assert sizing_alarm is False

    def test_fused_bias_is_the_tradier_bias_unchanged(self, frozen_db):
        _, fused, _ = resolve_compound_risk_context(
            'TEST', _make_skew(slope_atm=-150.0), orats_enabled=False
        )
        assert fused == 'bearish'

    def test_steep_slope_cannot_move_a_neutral_reading(self, frozen_db):
        # The deleted proxy mapped slope_atm=-150 to WEAK_BEARISH (-1) at conf 0.3
        # and blended it in: (0 x 0.1 + -1 x 0.3) / 0.4 = -0.75 -> weak_bearish,
        # one measurement moving a low-confidence NEUTRAL.
        _, fused, _ = resolve_compound_risk_context(
            'TEST', _make_skew(bias=DirectionalBias.NEUTRAL, conf=0.1, slope_atm=-150.0),
            orats_enabled=False
        )
        assert fused == 'neutral'

    def test_no_slope_atm_no_fused_bias(self, frozen_db):
        _, fused, _ = resolve_compound_risk_context(
            'TEST', _make_skew(slope_atm=None), orats_enabled=False
        )
        assert fused is None


class TestPredictionExpiration:
    """Expiration must match the TRADING expiration trade.sh's live /analyze
    path actually uses — calculate_expiration_date's min-3-DTE weekly Friday
    — not the near-dated implied-move expiration (the first one containing
    the reaction, metrics/implied_move_expiration.py).

    The two are for different purposes: implied_move_expiration is for
    VRP/implied-move math; calculate_expiration_date is documented "for TRADING purposes
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

    @pytest.fixture(autouse=True)
    def _open_window(self, monkeypatch):
        """The event may take its prediction (prediction_cutoff is tested against
        Postgres in test_bias_prediction_every_path.py)."""
        from datetime import datetime, timezone

        import scripts.store_bias_prediction as sbp
        monkeypatch.setattr(sbp, "prediction_cutoff", lambda *a: (
            datetime(2100, 1, 1, tzinfo=timezone.utc), None))

    def _drive(self, earnings_date, snap_to=None, snap_ok=True, min_dte=3):
        container = Mock()
        container.config.thresholds.min_dte = min_dte
        snap_result = Mock()
        snap_result.is_ok = snap_ok
        snap_result.value = snap_to
        container.cached_options_provider.find_nearest_expiration.return_value = snap_result
        skew_result = Mock()
        skew_result.is_err = True
        skew_result.error = 'NODATA'
        container.skew_analyzer.analyze_skew_curve.return_value = skew_result
        analyze_and_store(container, 'TEST', earnings_date)
        desired = container.cached_options_provider.find_nearest_expiration.call_args[0][1]
        used = container.skew_analyzer.analyze_skew_curve.call_args[0][1]
        # CLAUDE.md: chains and expirations go through the cached provider, never raw Tradier.
        container.tradier.find_nearest_expiration.assert_not_called()
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

    def test_the_configured_min_dte_floors_it_as_on_the_scan(self):
        # Mon Oct 12 2026: the same week's Friday is 4 DTE, under MIN_DTE=5, so the
        # scan asks for the next Friday; a prediction on the 4-DTE chain would not
        # be the scan's chain (scanned.requested) nor the one /analyze trades.
        desired, _ = self._drive(date(2026, 10, 12), snap_to=date(2026, 10, 23), min_dte=5)
        assert desired == date(2026, 10, 23)

    def test_skew_uses_snapped_expiration(self):
        _, used = self._drive(date(2026, 7, 14), snap_to=date(2026, 7, 17))
        assert used == date(2026, 7, 17)

    def test_snap_failure_falls_back_to_desired(self):
        _, used = self._drive(date(2026, 7, 14), snap_ok=False)
        assert used == date(2026, 7, 17)


class TestTailRiskIsRecorded:
    """trr_level was computed only inside `if vrp_result is not None`, and the VRP
    lookup before it called `VRPCalculator.calculate_vrp`, which does not exist --
    so every prediction since June 2026 stored trr_level NULL and
    compound_risk_active could never be true. TRR needs only the stored moves."""

    def _drive(self, monkeypatch, gaps):
        from datetime import datetime, timezone

        import scripts.store_bias_prediction as sbp

        stored = {}
        monkeypatch.setattr(sbp, "store_bias_prediction",
                            lambda *a, **kw: stored.update(args=a, kwargs=kw) or True)
        monkeypatch.setattr(sbp, "resolve_compound_risk_context",
                            lambda ticker, skew, orats_enabled: (None, "bearish", False))
        monkeypatch.setattr(sbp, "prediction_cutoff", lambda *a: (
            datetime(2100, 1, 1, tzinfo=timezone.utc), None))
        from src.application.metrics.vrp import VRPCalculator
        container = Mock()
        container.config.thresholds.min_dte = 3
        container.db_pool = MagicMock()
        # spec'd: a bare Mock invents calculate_vrp and hid this bug from tests
        container.vrp_calculator = Mock(spec=VRPCalculator)
        snap = Mock(is_ok=True, value=date(2026, 10, 23))
        container.cached_options_provider.find_nearest_expiration.return_value = snap
        skew = _make_skew()
        skew.stock_price.amount = 100.0
        container.skew_analyzer.analyze_skew_curve.return_value = Mock(is_err=False, value=skew)
        moves = [Mock(gap_move_pct=Mock(value=g)) for g in gaps]
        container.prices_repository.get_historical_moves.return_value = Mock(is_ok=True, value=moves)
        assert analyze_and_store(container, "TEST", date(2026, 10, 20)) == sbp.STORED
        return stored["kwargs"]

    def test_trr_level_is_stored_without_any_vrp(self, monkeypatch):
        # 11 flat 2% gaps and one 20%: 20 / 3.5 = 5.7 -> HIGH
        kwargs = self._drive(monkeypatch, [2.0] * 11 + [20.0])
        assert kwargs["trr_level"] == "HIGH"

    def test_short_history_is_unknown(self, monkeypatch):
        assert self._drive(monkeypatch, [2.0, 20.0, 2.0])["trr_level"] is None


@pytest.fixture
def predictions_db(pg_schema):
    from common.pgtesting import baseline_ddl
    with connect() as conn:
        conn.execute(baseline_ddl("bias_predictions"))
        conn.commit()
    return pg_schema


class TestCompoundRiskIsTheEngines:
    """compound_risk_active is SizingContext.compound_risk_active on the stored
    inputs -- the engine's >=2 of [TRR HIGH, sizing alarm, bearish fused skew]."""

    @pytest.mark.parametrize("trr,alarm,fused,expected", [
        ("HIGH", False, "bearish", True),
        ("HIGH", False, "strong_bearish", True),
        (None, True, "bearish", True),
        ("HIGH", True, "neutral", True),
        ("HIGH", False, "weak_bearish", False),
        ("NORMAL", False, "strong_bearish", False),
        (None, False, None, False),
    ])
    def test_stored_flag(self, predictions_db, trr, alarm, fused, expected):
        from scripts.store_bias_prediction import store_bias_prediction
        skew = _make_skew()
        skew.skew_atm.value = 0.12
        skew.curvature = 0.01
        skew.strength = "smirk"
        skew.confidence = 0.8
        skew.num_points = 9
        from common.timezone import now_et
        with connect() as conn:
            store_bias_prediction(conn, "TEST", date(2026, 10, 20), date(2026, 10, 23), 100.0,
                                  skew, now_et(),
                                  fused_bias_value=fused, trr_level=trr, sizing_alarm=alarm)
        with connect() as conn:
            row = conn.execute(
                "SELECT compound_risk_active, trr_level, sizing_alarm "
                "FROM bias_predictions WHERE ticker = 'TEST'").fetchone()
        assert row.compound_risk_active is expected
        assert row.trr_level == trr
        assert row.sizing_alarm is alarm


class TestOratsEnabled:
    def test_reads_snapshot_and_fires_alarm(self, frozen_db):
        r_slp_30, fused, sizing_alarm = resolve_compound_risk_context(
            'TEST', _make_skew(), orats_enabled=True
        )
        assert r_slp_30 == -2.0
        assert sizing_alarm is True
        assert fused in ('bearish', 'strong_bearish')

    def test_stale_snapshot_row_ignored(self, frozen_db):
        # 30-day-old row: guard treats it as absent — no alarm, no r_slp_30,
        # fused bias is the Tradier bias (resubscription-without-refresh safety)
        r_slp_30, fused, sizing_alarm = resolve_compound_risk_context(
            'STALE', _make_skew(slope_atm=-150.0), orats_enabled=True
        )
        assert r_slp_30 is None
        assert sizing_alarm is False
        assert fused == 'bearish'

    def test_missing_ticker_uses_tradier_bias(self, frozen_db):
        # No position_limits row: no alarm, no r_slp_30; fused bias is the
        # Tradier bias unchanged (mirrors analyzer fallback)
        r_slp_30, fused, sizing_alarm = resolve_compound_risk_context(
            'NOPE', _make_skew(slope_atm=-150.0), orats_enabled=True
        )
        assert r_slp_30 is None
        assert sizing_alarm is False
        assert fused == 'bearish'
