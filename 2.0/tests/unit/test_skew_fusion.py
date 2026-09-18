"""Unit tests for _fuse_skew_signals and analyzer position limits loading."""

from collections import namedtuple
from unittest.mock import Mock

import pytest

from src.domain.enums import DirectionalBias
from src.domain.types import PositionLimitsSnapshot


def _make_analyzer(strategy_config=None):
    """Container mock carrying a REAL StrategyConfig.

    _build_sizing_context reads sizing tiers off container.config.strategy
    (tunable since Sep 2026). A bare Mock returns Mock attributes there, which
    silently poisons every cap assertion instead of failing loudly.
    """
    from src.application.services.analyzer import TickerAnalyzer
    from src.config.config import StrategyConfig
    container = Mock()
    container.config.strategy = strategy_config or StrategyConfig()
    return TickerAnalyzer(container)


def _make_skew(bias: DirectionalBias, bias_confidence: float, slope_atm: float = 0.0):
    skew = Mock()
    skew.directional_bias = bias
    skew.bias_confidence = bias_confidence
    skew.slope_atm = slope_atm
    return skew


class TestFuseSkewSignals:
    """Confidence-weighted signal fusion."""

    # Without ORATS there is no second source. slope_atm comes from the same
    # fit that produced the Tradier bias, so it cannot vote again: the Tradier
    # bias passes through whatever the slope says.

    def test_no_orats_returns_tradier_bias(self):
        analyzer = _make_analyzer()
        skew = _make_skew(DirectionalBias.BEARISH, 0.6, slope_atm=-150.0)
        result = analyzer._fuse_skew_signals(skew, r_slp_30=None)
        assert result == DirectionalBias.BEARISH

    def test_no_orats_contrary_slope_does_not_moderate_tradier(self):
        # A slope from the same fit cannot disagree with that fit's own
        # reading; it is not evidence against it.
        analyzer = _make_analyzer()
        skew = _make_skew(DirectionalBias.BEARISH, 0.6, slope_atm=150.0)
        result = analyzer._fuse_skew_signals(skew, r_slp_30=None)
        assert result == DirectionalBias.BEARISH

    def test_no_orats_flat_slope_does_not_dilute_tradier(self):
        analyzer = _make_analyzer()
        skew = _make_skew(DirectionalBias.BEARISH, 0.6, slope_atm=0.0)
        result = analyzer._fuse_skew_signals(skew, r_slp_30=None)
        assert result == DirectionalBias.BEARISH

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


class TestSameFitIsNotASecondSource:
    """Defect 3: the post-ORATS path fused the Tradier bias with a linear map
    of slope_atm (compute_tradier_r_slp30_proxy) — the slope of the very fit
    that produced the bias. One measurement counted twice cannot add
    confidence, and must not be able to move the reading.
    """

    @staticmethod
    def _fuse(bias, conf, slope_atm, r_slp_30=None):
        from src.application.metrics.skew_enhanced import fuse_skew_bias
        return fuse_skew_bias(
            tradier_bias=bias, tradier_conf=conf,
            slope_atm=slope_atm, r_slp_30=r_slp_30,
        )

    def test_same_fit_slope_does_not_flip_a_neutral(self):
        # A steep slope used to drag a NEUTRAL reading to WEAK_BEARISH.
        assert self._fuse(DirectionalBias.NEUTRAL, 0.6, -600.0) == DirectionalBias.NEUTRAL

    def test_same_fit_slope_does_not_promote_into_the_compound_risk_leg(self):
        # WEAK_BEARISH at the noise gate's floor plus the same fit's slope
        # used to fuse to exactly -1.5 → BEARISH, arming the compound-risk
        # cap on one source.
        assert self._fuse(DirectionalBias.WEAK_BEARISH, 0.3, -600.0) == DirectionalBias.WEAK_BEARISH

    @pytest.mark.parametrize("conf", [0.0, 0.3, 0.6, 1.0])
    @pytest.mark.parametrize("slope", [-5000.0, -600.0, -150.0, -1.0, 0.0, 1.0, 150.0, 600.0, 5000.0])
    def test_without_orats_every_reading_passes_through(self, conf, slope):
        for bias in DirectionalBias:
            assert self._fuse(bias, conf, slope) == bias

    def test_no_fit_and_no_orats_is_still_none(self):
        assert self._fuse(DirectionalBias.BEARISH, 0.6, None) is None

    def test_orats_still_fuses(self):
        # ORATS r_slp_30 is its own surface — an independent source.
        # NEUTRAL at 0.5 + STRONG_BEARISH (-3) at 0.5 → -1.5 → BEARISH.
        assert self._fuse(DirectionalBias.NEUTRAL, 0.5, 0.0, r_slp_30=-2.0) == DirectionalBias.BEARISH


class TestExplicitRounding:
    """Defect 4: round() is banker's rounding — round(0.5) = 0, round(2.5) = 2.

    The fused level rounds half away from zero: symmetric under a sign flip
    and monotone in magnitude.
    """

    @pytest.mark.parametrize("value, expected", [
        (0.5, 1), (1.5, 2), (2.5, 3),
        (-0.5, -1), (-1.5, -2), (-2.5, -3),
        (0.0, 0), (0.49, 0), (-0.49, 0), (0.51, 1), (1.49, 1), (2.51, 3),
        (1.4999999999999998, 2),   # a tie that floating point lands short
        (-1.4999999999999998, -2),
    ])
    def test_half_away_from_zero(self, value, expected):
        from src.application.metrics.skew_enhanced import round_half_away_from_zero
        result = round_half_away_from_zero(value)
        assert result == expected
        assert isinstance(result, int)

    def test_fused_half_rounds_up_in_magnitude(self):
        # NEUTRAL (0) at 0.5 + ORATS WEAK_BEARISH (-1, r_slp_30=0.5) at 0.5
        # = -0.5 → WEAK_BEARISH. Banker's rounding gave NEUTRAL.
        from src.application.metrics.skew_enhanced import fuse_skew_bias
        fused = fuse_skew_bias(DirectionalBias.NEUTRAL, 0.5, 0.0, r_slp_30=0.5)
        assert fused == DirectionalBias.WEAK_BEARISH

    def test_fused_two_and_a_half_reaches_strong(self):
        # STRONG_BEARISH (-3) at 0.5 + ORATS BEARISH (-2, r_slp_30=0.0) at 0.5
        # = -2.5 → STRONG_BEARISH. Banker's rounding gave BEARISH.
        from src.application.metrics.skew_enhanced import fuse_skew_bias
        fused = fuse_skew_bias(DirectionalBias.STRONG_BEARISH, 0.5, 0.0, r_slp_30=0.0)
        assert fused == DirectionalBias.STRONG_BEARISH

    def test_rounding_is_symmetric(self):
        from src.application.metrics.skew_enhanced import fuse_skew_bias
        bullish = fuse_skew_bias(DirectionalBias.STRONG_BULLISH, 0.5, 0.0, r_slp_30=2.5)
        assert bullish == DirectionalBias.STRONG_BULLISH  # +3 and +2 → +2.5 → +3


def _pg_row(fields: dict):
    """Build a row shaped like the pool actually returns one.

    psycopg's namedtuple_row yields namedtuples, so the code under test uses
    attribute access. A dict here would pass while the real thing raised
    TypeError -- the mock has to have the same shape as the driver, not the
    shape the old sqlite3.Row had.
    """
    if fields is None:
        return None
    return namedtuple("Row", fields.keys())(**fields)


def _make_db_pool_mock(row):
    """Mock db_pool whose connection() context manager yields a conn returning row."""
    cursor_mock = Mock()
    cursor_mock.fetchone = Mock(return_value=_pg_row(row))
    conn_mock = Mock()
    conn_mock.execute = Mock(return_value=cursor_mock)
    from contextlib import contextmanager

    @contextmanager
    def _connection():
        yield conn_mock

    pool_mock = Mock()
    pool_mock.connection = _connection
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
        assert ctx.effective_max_contracts == 50  # CLAUDE.md rule 4: no-flag default 50 (was 100 until 2026-09-13)

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
