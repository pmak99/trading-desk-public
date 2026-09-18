"""
Unit tests for TickerAnalyzer risk-classification logic that previously had
zero direct test coverage: _compute_tail_risk_level (gap-based TRR feeding
sizing_ctx.trr_level), and that the VIX regime no longer overrides the
VRP recommendation (removed 2026-09-14).
"""

from datetime import date
from unittest.mock import Mock

from src.application.services.analyzer import TickerAnalyzer
from src.domain.enums import Recommendation
from src.domain.types import Percentage, VRPResult


def _make_analyzer():
    return TickerAnalyzer(Mock())


def _make_move(gap_pct):
    """Mock historical move exposing only the gap_move_pct field used by
    _compute_tail_risk_level."""
    move = Mock()
    if gap_pct is None:
        move.gap_move_pct = None
    else:
        move.gap_move_pct = Percentage(gap_pct)
    return move


def _make_vrp(vrp_ratio, recommendation):
    return VRPResult(
        ticker="TEST",
        expiration=date(2026, 7, 1),
        implied_move_pct=Percentage(vrp_ratio * 5.0),
        historical_mean_move_pct=Percentage(5.0),
        vrp_ratio=vrp_ratio,
        edge_score=1.0,
        recommendation=recommendation,
    )


class TestComputeTailRiskLevel:
    """Gap-based TRR classification (drives sizing_ctx.trr_level)."""

    def test_high_when_max_to_avg_gap_exceeds_2_5(self):
        analyzer = _make_analyzer()
        # gaps: 2,2,2,12 -> avg=4.5, max=12 -> TRR=2.67 -> HIGH
        moves = [_make_move(g) for g in [2.0, 2.0, 2.0, 12.0]]
        assert analyzer._compute_tail_risk_level(moves) == "HIGH"

    def test_normal_when_ratio_between_1_5_and_2_5(self):
        analyzer = _make_analyzer()
        # gaps: 3,4,5,10 -> avg=5.5, max=10 -> TRR=1.82 -> NORMAL
        moves = [_make_move(g) for g in [3.0, 4.0, 5.0, 10.0]]
        assert analyzer._compute_tail_risk_level(moves) == "NORMAL"

    def test_low_when_ratio_below_1_5(self):
        analyzer = _make_analyzer()
        # gaps: 4,5,4.5,5.5 -> avg=4.75, max=5.5 -> TRR=1.16 -> LOW
        moves = [_make_move(g) for g in [4.0, 5.0, 4.5, 5.5]]
        assert analyzer._compute_tail_risk_level(moves) == "LOW"

    def test_uses_absolute_value_of_gap(self):
        analyzer = _make_analyzer()
        # Signed gaps should be treated as magnitudes: -2,-2,-2,12 same as
        # the HIGH case above.
        moves = [_make_move(g) for g in [-2.0, -2.0, -2.0, -12.0]]
        assert analyzer._compute_tail_risk_level(moves) == "HIGH"

    def test_none_gap_entries_are_filtered_out(self):
        analyzer = _make_analyzer()
        # Four valid entries (TRR=1.0 -> LOW) plus None noise that must not
        # crash, skew the ratio, or count toward the 4-quarter minimum.
        moves = [_make_move(3.0), _make_move(None), _make_move(3.0), _make_move(None),
                 _make_move(3.0), _make_move(3.0)]
        assert analyzer._compute_tail_risk_level(moves) == "LOW"

    def test_fewer_than_two_valid_moves_returns_none(self):
        analyzer = _make_analyzer()
        assert analyzer._compute_tail_risk_level([_make_move(5.0)]) is None
        assert analyzer._compute_tail_risk_level([]) is None
        assert analyzer._compute_tail_risk_level([_make_move(None), _make_move(None)]) is None

    def test_all_zero_gaps_returns_low_not_divide_by_zero(self):
        analyzer = _make_analyzer()
        moves = [_make_move(0.0)] * 4
        # avg_gap == 0 guards the division; trr falls back to 0 -> LOW
        assert analyzer._compute_tail_risk_level(moves) == "LOW"


class TestVixDoesNotMoveTheTier:
    """Until 2026-09-14 /analyze alone raised the VRP thresholds 1.1-1.5x by VIX
    regime, refused every trade at VIX 40+, and built no strategies when the VIX
    fetch failed. The pre-registered test (scripts/research/vix_adaptive_thresholds.py)
    came back INSUFFICIENT, so the adjustment was removed: every path applies the
    VRP calculator's tier. VIX is still logged."""

    def _run(self, conditions_result):
        from src.config.config import APIConfig, StrategyConfig
        from src.domain.enums import EarningsTiming
        from src.domain.errors import Ok

        container = Mock()
        # Real sections: a Mock's orats_enabled is truthy and would read ORATS.
        container.config.api = APIConfig(tradier_api_key="")
        container.config.thresholds.min_historical_quarters = 4
        container.config.strategy = StrategyConfig()
        exp = date(2026, 10, 16)
        container.cached_options_provider.find_nearest_expiration.return_value = Ok(exp)
        container.cached_options_provider.get_option_chain.return_value = Ok(Mock())
        container.implied_move_calculator.calculate.return_value = Ok(
            Mock(implied_move_pct=Percentage(12.5))
        )
        moves = [Mock(close_move_pct=Percentage(4.0), gap_move_pct=Percentage(3.0),
                      intraday_move_pct=Percentage(3.5), earnings_date=date(2026, 7, 20))
                 for _ in range(6)]
        container.prices_repository.get_historical_moves.return_value = Ok(moves)
        container.vrp_calculator.calculate.return_value = Ok(
            _make_vrp(2.5, Recommendation.EXCELLENT)
        )
        container.skew_analyzer = None
        container.consistency_analyzer = None
        container.strategy_generator.generate_strategies.return_value = Mock(strategies=[Mock()])
        container.market_conditions_analyzer.get_current_conditions.return_value = conditions_result
        container.db_pool.connection.side_effect = RuntimeError("no db in this test")

        result = TickerAnalyzer(container).analyze(
            "TEST", date(2026, 10, 14), exp,
            earnings_timing=EarningsTiming.AMC, generate_strategies=True,
        )
        assert result.is_ok, result
        return result.value, container

    def test_extreme_vix_keeps_the_calculator_tier_and_builds_strategies(self):
        from src.domain.errors import Ok

        extreme = Mock(regime="extreme", vix_level=Percentage(45.0))
        analysis, container = self._run(Ok(extreme))

        assert analysis.recommendation == Recommendation.EXCELLENT
        assert analysis.vrp.recommendation == Recommendation.EXCELLENT
        assert analysis.strategies is not None
        # still logged: analysis_log.vix_level / vix_regime
        assert container.analysis_repository.log_analysis.call_args.args[1] is extreme

    def test_failed_vix_fetch_no_longer_withholds_strategies(self):
        from src.domain.errors import AppError, Err, ErrorCode

        analysis, container = self._run(Err(AppError(ErrorCode.EXTERNAL, "VIX down")))

        assert analysis.strategies is not None
        assert container.analysis_repository.log_analysis.call_args.args[1] is None
