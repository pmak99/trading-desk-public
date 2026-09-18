"""Tests for ORATS_ENABLED feature flag branching."""

from datetime import date, datetime, timedelta
from unittest.mock import Mock

import pytest

# ---------------------------------------------------------------------------
# Parsing ORATS_ENABLED (off unless "true", one reader) is tested in
# test_config_env_readers.py: the flag is APIConfig.orats_enabled since
# 2026-09-14, and the common/constants.py copy that defaulted ON is gone.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# analyzer.py — Steps 5.5 and 5.6 branch on config.api.orats_enabled
# ---------------------------------------------------------------------------

@pytest.mark.pg
class TestAnalyzerOratsFlag:
    """Steps 5.5-5.7 of TickerAnalyzer.analyze(), run for real.

    config.api.orats_enabled gates whether the position_limits snapshot is read; the
    snapshot's r_slp_30 is the only second source skew fusion has, and its
    fcst/iee feed the sizing context. These tests used to patch
    _fuse_skew_signals, re-type the analyzer's if-conditions inline, call the
    mock themselves and assert it was called -- so they passed whatever
    analyze() did. Now analyze() runs against a real schema holding a fresh
    snapshot whose r_slp_30 (-2.0, STRONG_BEARISH) would move a NEUTRAL Tradier
    reading to BEARISH, and the flag is judged by what reaches the analysis.
    """

    FRESH_SNAPSHOT = {"r_slp_30": -2.0, "fcst_ern_iv_effect": 1.8, "iee_earn_effect": 2.1}

    @pytest.fixture
    def limits_db(self, pg_pool):
        from common.pgtesting import baseline_ddl

        with pg_pool.connection() as conn:
            conn.execute(baseline_ddl("position_limits", "strategies"))
            conn.commit()
        return pg_pool

    def _snapshot(self, pool, age_days=1, **fields):
        with pool.connection() as conn:
            conn.execute(
                "INSERT INTO position_limits (ticker, r_slp_30, fcst_ern_iv_effect,"
                " iee_earn_effect, last_updated) VALUES ('TEST', %s, %s, %s, %s)",
                (fields["r_slp_30"], fields["fcst_ern_iv_effect"], fields["iee_earn_effect"],
                 datetime.now() - timedelta(days=age_days)))
            conn.commit()

    def _analyze(self, pool, orats_enabled, skew_bias="NEUTRAL"):
        from src.application.metrics.skew_enhanced import SkewAnalysis
        from src.application.services.analyzer import TickerAnalyzer
        from src.config.config import APIConfig, StrategyConfig
        from src.domain.enums import DirectionalBias, EarningsTiming, Recommendation
        from src.domain.errors import Ok
        from src.domain.types import Money, Percentage, VRPResult

        exp = date(2026, 10, 16)
        container = Mock()
        container.db_pool = pool
        container.config.api = APIConfig(tradier_api_key="", orats_enabled=orats_enabled)
        container.config.thresholds.min_historical_quarters = 4
        container.config.strategy = StrategyConfig()
        container.cached_options_provider.find_nearest_expiration.return_value = Ok(exp)
        container.implied_move_calculator.calculate.return_value = Ok(
            Mock(implied_move_pct=Percentage(10.0)))
        container.prices_repository.get_historical_moves.return_value = Ok(
            [Mock(close_move_pct=Percentage(4.0), gap_move_pct=Percentage(3.0),
                  intraday_move_pct=Percentage(3.5), earnings_date=date(2026, 7, 20)) for _ in range(8)])
        container.vrp_calculator.calculate.return_value = Ok(VRPResult(
            ticker="TEST", expiration=exp, implied_move_pct=Percentage(10.0),
            historical_mean_move_pct=Percentage(4.0), vrp_ratio=2.5, edge_score=1.0,
            recommendation=Recommendation.EXCELLENT))
        container.consistency_analyzer = None
        if skew_bias is None:
            container.skew_analyzer = None
        else:
            container.skew_analyzer.analyze_skew_curve.return_value = Ok(SkewAnalysis(
                ticker="TEST", expiration=exp, stock_price=Money(100.0),
                skew_atm=Percentage(0.0), curvature=0.0, strength="smirk",
                directional_bias=DirectionalBias[skew_bias], confidence=0.5, num_points=9,
                slope_atm=0.0, bias_confidence=0.5))

        result = TickerAnalyzer(container).analyze(
            "TEST", date(2026, 10, 14), exp, earnings_timing=EarningsTiming.AMC)
        assert result.is_ok, result
        return result.value

    def test_orats_disabled_never_reads_the_snapshot(self, limits_db):
        from src.domain.enums import DirectionalBias

        self._snapshot(limits_db, **self.FRESH_SNAPSHOT)
        analysis = self._analyze(limits_db, orats_enabled=False)

        assert analysis.skew.directional_bias == DirectionalBias.NEUTRAL   # Tradier passes through
        assert analysis.sizing_context.fcst_ern_iv_effect is None
        assert analysis.sizing_context.iee_earn_effect is None
        assert analysis.sizing_context.fused_bias == DirectionalBias.NEUTRAL

    def test_orats_enabled_fuses_a_fresh_snapshot_into_skew_and_sizing(self, limits_db):
        from src.domain.enums import DirectionalBias

        self._snapshot(limits_db, **self.FRESH_SNAPSHOT)
        analysis = self._analyze(limits_db, orats_enabled=True)

        assert analysis.skew.directional_bias == DirectionalBias.BEARISH
        assert analysis.sizing_context.fused_bias == DirectionalBias.BEARISH
        assert (analysis.sizing_context.fcst_ern_iv_effect,
                analysis.sizing_context.iee_earn_effect) == (1.8, 2.1)

    def test_orats_enabled_with_a_stale_snapshot_is_tradier_only(self, limits_db):
        from src.domain.enums import DirectionalBias

        self._snapshot(limits_db, age_days=400, **self.FRESH_SNAPSHOT)
        analysis = self._analyze(limits_db, orats_enabled=True)

        assert analysis.skew.directional_bias == DirectionalBias.NEUTRAL
        assert analysis.sizing_context.fcst_ern_iv_effect is None

    def test_orats_enabled_without_a_row_is_tradier_only(self, limits_db):
        from src.domain.enums import DirectionalBias

        analysis = self._analyze(limits_db, orats_enabled=True, skew_bias="WEAK_BEARISH")
        assert analysis.skew.directional_bias == DirectionalBias.WEAK_BEARISH

    def test_no_skew_reading_means_no_fusion(self, limits_db):
        self._snapshot(limits_db, **self.FRESH_SNAPSHOT)
        analysis = self._analyze(limits_db, orats_enabled=True, skew_bias=None)

        assert analysis.skew is None
        assert analysis.sizing_context.fused_bias is None
        assert analysis.sizing_context.fcst_ern_iv_effect == 1.8   # sizing still reads it


# ---------------------------------------------------------------------------
# TestFetchOratsTickerFlag and TestRefreshOratsSnapshotsFlag were deleted with
# their subjects at the Postgres port (Task 4). Both scripts were kept solely
# "for resubscription" against an API whose key died 2026-07-02; carrying two
# dead clients into a new database was the bloat this migration set out not to
# inherit. Recoverable at tag pre-postgres-script-removal.
#
# The ORATS switch itself is still covered above — it gates the live
# analyzer path, which is the part that can still affect a trade.
# ---------------------------------------------------------------------------
