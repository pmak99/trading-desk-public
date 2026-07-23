"""Unit tests for _fuse_skew_signals and analyzer position limits loading."""

import pytest
import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.domain.enums import DirectionalBias
from src.domain.types import SizingContext, PositionLimitsSnapshot


def _make_analyzer():
    from src.application.services.analyzer import TickerAnalyzer
    return TickerAnalyzer(Mock())


def _make_skew(bias: DirectionalBias, bias_confidence: float, slope_atm: float = 0.0):
    skew = Mock()
    skew.directional_bias = bias
    skew.bias_confidence = bias_confidence
    skew.slope_atm = slope_atm
    return skew


class TestFuseSkewSignals:
    """Confidence-weighted signal fusion."""

    def test_no_orats_uses_tradier_proxy(self):
        # When r_slp_30 is None, the Tradier slope_atm proxy is used at 0.3
        # confidence. Negative slope_atm = put skew = bearish (same convention
        # as the polynomial classifier; empirically corr(slope_atm, r_slp_30)
        # = +0.45 on 42 ORATS-era tickers).
        # tradier: BEARISH (-2), conf=0.6; proxy(-150) ≈ WEAK_BEARISH (-1)
        # fused = (-2*0.6 + -1*0.3) / 0.9 = -1.67 → round(-2) → BEARISH
        analyzer = _make_analyzer()
        skew = _make_skew(DirectionalBias.BEARISH, 0.6, slope_atm=-150.0)
        result = analyzer._fuse_skew_signals(skew, r_slp_30=None)
        assert result in (DirectionalBias.BEARISH, DirectionalBias.STRONG_BEARISH)

    def test_no_orats_bullish_slope_moderates_bearish_tradier(self):
        # Bullish slope (+150) pulls a bearish Tradier bias toward neutral —
        # proxy(+150) ≈ WEAK_BULLISH (+1).
        # fused = (-2*0.6 + 1*0.3) / 0.9 = -1.0 → WEAK_BEARISH
        analyzer = _make_analyzer()
        skew = _make_skew(DirectionalBias.BEARISH, 0.6, slope_atm=150.0)
        result = analyzer._fuse_skew_signals(skew, r_slp_30=None)
        assert result == DirectionalBias.WEAK_BEARISH

    def test_no_orats_neutral_slope_stays_bearish(self):
        # slope_atm=0 → proxy=RSLP30_MEAN → NEUTRAL (0).
        # tradier: BEARISH (-2), conf=0.6; proxy: NEUTRAL (0), conf=0.3
        # fused = -1.2/0.9 ≈ -1.33 → WEAK_BEARISH
        analyzer = _make_analyzer()
        skew = _make_skew(DirectionalBias.BEARISH, 0.6, slope_atm=0.0)
        result = analyzer._fuse_skew_signals(skew, r_slp_30=None)
        assert result in (DirectionalBias.WEAK_BEARISH, DirectionalBias.BEARISH)

    def test_orats_strong_bearish_pulls_neutral_tradier(self):
        analyzer = _make_analyzer()
        # Tradier: NEUTRAL (0), conf=0.226; ORATS: STRONG_BEARISH (-3), conf=0.5
        # fused = (0*0.226 + -3*0.5) / (0.226+0.5) = -1.5/0.726 ≈ -2.07 → round(-2) → BEARISH
        skew = _make_skew(DirectionalBias.NEUTRAL, 0.226)
        result = analyzer._fuse_skew_signals(skew, r_slp_30=-2.0)  # < -0.905 → -3
        assert result in (DirectionalBias.BEARISH, DirectionalBias.STRONG_BEARISH)

    def test_matching_signals_preserve_level(self):
        analyzer = _make_analyzer()
        # Both BEARISH (-2): fused = (-2*0.5 + -2*0.5) / (0.5+0.5) = -2 → BEARISH
        skew = _make_skew(DirectionalBias.BEARISH, 0.5)
        result = analyzer._fuse_skew_signals(skew, r_slp_30=0.0)  # < 0.065 → -2
        assert result == DirectionalBias.BEARISH

    def test_opposite_signals_tend_toward_neutral(self):
        analyzer = _make_analyzer()
        # Tradier: STRONG_BULLISH (+3), conf=0.5; ORATS: STRONG_BEARISH (-3), conf=0.5
        # fused = (3*0.5 + -3*0.5) / 1.0 = 0 → NEUTRAL
        skew = _make_skew(DirectionalBias.STRONG_BULLISH, 0.5)
        result = analyzer._fuse_skew_signals(skew, r_slp_30=-2.0)  # STRONG_BEARISH
        assert result == DirectionalBias.NEUTRAL

    def test_zero_tradier_confidence_orats_dominates(self):
        analyzer = _make_analyzer()
        # Tradier: BULLISH (+2), conf=0.0; ORATS: STRONG_BEARISH (-3), conf=0.5
        # fused = (2*0.0 + -3*0.5) / (0.0+0.5) = -3 → STRONG_BEARISH
        skew = _make_skew(DirectionalBias.BULLISH, 0.0)
        result = analyzer._fuse_skew_signals(skew, r_slp_30=-2.0)
        assert result == DirectionalBias.STRONG_BEARISH


class TestTradierRslp30Proxy:
    """compute_tradier_r_slp30_proxy sign and calibration.

    Convention (matches the polynomial classifier and 5.0/src/domain/skew.py):
    negative slope_atm = put skew = bearish → LOW r_slp_30.
    Calibration: OLS of ORATS-era bias_predictions (n=42 tickers,
    corr +0.45): Δr_slp_30 ≈ 0.0027 per slope_atm unit.
    """

    def _proxy(self, slope):
        from src.application.metrics.skew_enhanced import (
            compute_tradier_r_slp30_proxy,
        )
        return compute_tradier_r_slp30_proxy(slope)

    def test_zero_slope_maps_to_mean(self):
        from common.constants import RSLP30_MEAN
        assert self._proxy(0.0) == pytest.approx(RSLP30_MEAN)

    def test_positive_slope_is_bullish_side(self):
        from common.constants import RSLP30_MEAN
        assert self._proxy(150.0) > RSLP30_MEAN

    def test_negative_slope_is_bearish_side(self):
        from common.constants import RSLP30_MEAN
        assert self._proxy(-150.0) < RSLP30_MEAN

    def test_calibrated_scale_not_overdriven(self):
        # ±150 slope is ~0.3σ of r_slp_30, NOT ±2σ — a strong Tradier slope
        # alone must not map to a STRONG_* r_slp_30 bucket.
        from src.application.metrics.skew_enhanced import r_slp_30_to_numeric
        assert r_slp_30_to_numeric(self._proxy(150.0)) == +1   # WEAK_BULLISH
        assert r_slp_30_to_numeric(self._proxy(-150.0)) == -1  # WEAK_BEARISH

    def test_extreme_slope_clamps_at_two_sigma(self):
        from common.constants import RSLP30_MEAN, RSLP30_STD
        assert self._proxy(5000.0) == pytest.approx(RSLP30_MEAN + 2 * RSLP30_STD)
        assert self._proxy(-5000.0) == pytest.approx(RSLP30_MEAN - 2 * RSLP30_STD)

    def test_monotonic_increasing(self):
        vals = [self._proxy(s) for s in (-600, -150, 0, 150, 600)]
        assert vals == sorted(vals)


def _make_db_pool_mock(row):
    """Return a mock db_pool whose get_connection() context manager yields a conn returning row."""
    cursor_mock = Mock()
    cursor_mock.fetchone = Mock(return_value=row)
    conn_mock = Mock()
    conn_mock.execute = Mock(return_value=cursor_mock)
    from contextlib import contextmanager
    @contextmanager
    def _get_connection():
        yield conn_mock
    pool_mock = Mock()
    pool_mock.get_connection = _get_connection
    return pool_mock


def _fresh_ts():
    from datetime import datetime
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _stale_ts(days_old=30):
    from datetime import datetime, timedelta
    return (datetime.now() - timedelta(days=days_old)).strftime('%Y-%m-%d %H:%M:%S')


class TestLoadPositionLimits:
    """_load_position_limits returns correct snapshot."""

    def test_no_row_returns_empty_snapshot(self):
        analyzer = _make_analyzer()
        analyzer.container.db_pool = _make_db_pool_mock(None)
        result = analyzer._load_position_limits("FAKE")
        assert result == PositionLimitsSnapshot()

    def test_row_maps_to_snapshot(self):
        analyzer = _make_analyzer()
        fake_row = {
            'fcst_ern_iv_effect': 2.3,
            'iee_earn_effect': 3.5,
            'r_slp_30': -0.5,
            'last_updated': _fresh_ts(),
        }
        analyzer.container.db_pool = _make_db_pool_mock(fake_row)
        result = analyzer._load_position_limits("AAPL")
        assert result.fcst_ern_iv_effect == 2.3
        assert result.iee_earn_effect == 3.5
        assert result.r_slp_30 == -0.5

    def test_null_fields_become_none(self):
        analyzer = _make_analyzer()
        fake_row = {
            'fcst_ern_iv_effect': None,
            'iee_earn_effect': None,
            'r_slp_30': None,
            'last_updated': _fresh_ts(),
        }
        analyzer.container.db_pool = _make_db_pool_mock(fake_row)
        result = analyzer._load_position_limits("AAPL")
        assert result == PositionLimitsSnapshot()

    def test_stale_snapshot_returns_empty(self):
        # Resubscription safety: flipping ORATS_ENABLED=true without
        # re-running the snapshot refresh must NOT feed year-old fcst/iee
        # into sizing. Rows older than ORATS_SNAPSHOT_MAX_AGE_DAYS are
        # treated as absent.
        analyzer = _make_analyzer()
        fake_row = {
            'fcst_ern_iv_effect': 2.3,
            'iee_earn_effect': 3.5,
            'r_slp_30': -0.5,
            'last_updated': _stale_ts(days_old=30),
        }
        analyzer.container.db_pool = _make_db_pool_mock(fake_row)
        result = analyzer._load_position_limits("AAPL")
        assert result == PositionLimitsSnapshot()

    def test_missing_last_updated_treated_as_stale(self):
        # Unknown age fails closed — the guard exists for safety
        analyzer = _make_analyzer()
        fake_row = {
            'fcst_ern_iv_effect': 2.3,
            'iee_earn_effect': 3.5,
            'r_slp_30': -0.5,
            'last_updated': None,
        }
        analyzer.container.db_pool = _make_db_pool_mock(fake_row)
        result = analyzer._load_position_limits("AAPL")
        assert result == PositionLimitsSnapshot()

    def test_within_max_age_passes(self):
        analyzer = _make_analyzer()
        fake_row = {
            'fcst_ern_iv_effect': 2.3,
            'iee_earn_effect': None,
            'r_slp_30': None,
            'last_updated': _stale_ts(days_old=7),
        }
        analyzer.container.db_pool = _make_db_pool_mock(fake_row)
        result = analyzer._load_position_limits("AAPL")
        assert result.fcst_ern_iv_effect == 2.3


class TestBuildSizingContext:
    """_build_sizing_context correctly delegates to SizingContext."""

    def test_passes_trr_level(self):
        analyzer = _make_analyzer()
        snapshot = PositionLimitsSnapshot(fcst_ern_iv_effect=1.5, iee_earn_effect=2.0)
        ctx = analyzer._build_sizing_context(snapshot, 'HIGH')
        assert ctx.trr_level == 'HIGH'
        assert ctx.trr_cap == 50

    def test_passes_fcst_and_iee(self):
        analyzer = _make_analyzer()
        snapshot = PositionLimitsSnapshot(fcst_ern_iv_effect=2.5, iee_earn_effect=4.0)
        ctx = analyzer._build_sizing_context(snapshot, None)
        assert ctx.fcst_ern_iv_effect == 2.5
        assert ctx.iee_earn_effect == 4.0
        assert ctx.iv_effect_reduction is True

    def test_none_snapshot_produces_empty_context(self):
        analyzer = _make_analyzer()
        snapshot = PositionLimitsSnapshot()
        ctx = analyzer._build_sizing_context(snapshot, None)
        assert ctx.trr_level is None
        assert ctx.fcst_ern_iv_effect is None
        assert ctx.effective_max_contracts == 100

    def test_passes_fused_bias_for_compound_risk_cap(self):
        # Fused bearish skew + TRR HIGH -> compound risk -> engine cap 25
        analyzer = _make_analyzer()
        snapshot = PositionLimitsSnapshot()
        ctx = analyzer._build_sizing_context(
            snapshot, 'HIGH', fused_bias=DirectionalBias.BEARISH
        )
        assert ctx.fused_bias == DirectionalBias.BEARISH
        assert ctx.effective_max_contracts == 25

    def test_fused_bias_defaults_to_none(self):
        analyzer = _make_analyzer()
        ctx = analyzer._build_sizing_context(PositionLimitsSnapshot(), 'HIGH')
        assert ctx.fused_bias is None
        assert ctx.effective_max_contracts == 50
