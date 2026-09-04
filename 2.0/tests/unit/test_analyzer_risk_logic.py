"""
Unit tests for TickerAnalyzer risk-classification logic that previously had
zero direct test coverage: _compute_tail_risk_level (gap-based TRR feeding
sizing_ctx.trr_level) and _apply_adaptive_thresholds (VIX-regime override of
the base VRP recommendation, including the hard SKIP override).
"""

import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.application.services.analyzer import TickerAnalyzer
from src.domain.types import Percentage, VRPResult
from src.domain.enums import Recommendation
from datetime import date


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


def _make_adapted(
    trade_recommended=True,
    regime="NORMAL",
    vix_level=18.0,
    vrp_excellent=1.8,
    vrp_good=1.4,
    vrp_marginal=1.2,
    adjustment_factor=1.0,
    is_adjusted=False,
):
    adapted = Mock()
    adapted.trade_recommended = trade_recommended
    adapted.regime = regime
    adapted.vix_level = vix_level
    adapted.vrp_excellent = vrp_excellent
    adapted.vrp_good = vrp_good
    adapted.vrp_marginal = vrp_marginal
    adapted.adjustment_factor = adjustment_factor
    adapted.is_adjusted = is_adjusted
    return adapted


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
        # Two valid entries (3,3 -> TRR=1.0 -> LOW) plus None noise that
        # must not crash or skew the ratio.
        moves = [_make_move(3.0), _make_move(None), _make_move(3.0), _make_move(None)]
        assert analyzer._compute_tail_risk_level(moves) == "LOW"

    def test_fewer_than_two_valid_moves_returns_none(self):
        analyzer = _make_analyzer()
        assert analyzer._compute_tail_risk_level([_make_move(5.0)]) is None
        assert analyzer._compute_tail_risk_level([]) is None
        assert analyzer._compute_tail_risk_level([_make_move(None), _make_move(None)]) is None

    def test_all_zero_gaps_returns_low_not_divide_by_zero(self):
        analyzer = _make_analyzer()
        moves = [_make_move(0.0), _make_move(0.0)]
        # avg_gap == 0 guards the division; trr falls back to 0 -> LOW
        assert analyzer._compute_tail_risk_level(moves) == "LOW"


class TestApplyAdaptiveThresholds:
    """VIX-regime adjustment of the base VRP recommendation."""

    def test_vix_unavailable_keeps_base_recommendation(self):
        analyzer = _make_analyzer()
        analyzer.container.market_conditions_analyzer.get_current_conditions.return_value = Mock(
            is_err=True
        )
        vrp = _make_vrp(1.6, Recommendation.GOOD)

        result_vrp, market_conditions = analyzer._apply_adaptive_thresholds("TEST", vrp)

        assert result_vrp is vrp
        assert market_conditions is None

    def test_regime_not_recommended_overrides_to_skip(self):
        analyzer = _make_analyzer()
        analyzer.container.market_conditions_analyzer.get_current_conditions.return_value = Mock(
            is_err=False, value=Mock()
        )
        analyzer.container.adaptive_threshold_calculator.calculate.return_value = _make_adapted(
            trade_recommended=False, regime="EXTREME", vix_level=42.0
        )
        vrp = _make_vrp(2.0, Recommendation.EXCELLENT)

        result_vrp, market_conditions = analyzer._apply_adaptive_thresholds("TEST", vrp)

        assert result_vrp.recommendation == Recommendation.SKIP
        # Original VRP math is preserved; only the recommendation is overridden.
        assert result_vrp.vrp_ratio == 2.0
        assert market_conditions is not None

    def test_recommendation_upgraded_when_thresholds_loosen(self):
        analyzer = _make_analyzer()
        analyzer.container.market_conditions_analyzer.get_current_conditions.return_value = Mock(
            is_err=False, value=Mock()
        )
        # Low-VIX regime loosens thresholds enough that a MARGINAL base call
        # (vrp_ratio=1.5 against normal vrp_good=1.4) becomes GOOD here too,
        # so use a ratio that crosses into EXCELLENT under loosened bands.
        analyzer.container.adaptive_threshold_calculator.calculate.return_value = _make_adapted(
            trade_recommended=True, vrp_excellent=1.4, vrp_good=1.2, vrp_marginal=1.0
        )
        vrp = _make_vrp(1.5, Recommendation.MARGINAL)

        result_vrp, _ = analyzer._apply_adaptive_thresholds("TEST", vrp)

        assert result_vrp.recommendation == Recommendation.EXCELLENT

    def test_recommendation_downgraded_when_thresholds_tighten(self):
        analyzer = _make_analyzer()
        analyzer.container.market_conditions_analyzer.get_current_conditions.return_value = Mock(
            is_err=False, value=Mock()
        )
        # High-VIX regime tightens thresholds so a previously-GOOD ratio
        # (1.5) now falls below the (raised) marginal bar -> SKIP.
        analyzer.container.adaptive_threshold_calculator.calculate.return_value = _make_adapted(
            trade_recommended=True, vrp_excellent=2.5, vrp_good=2.0, vrp_marginal=1.6
        )
        vrp = _make_vrp(1.5, Recommendation.GOOD)

        result_vrp, _ = analyzer._apply_adaptive_thresholds("TEST", vrp)

        assert result_vrp.recommendation == Recommendation.SKIP

    def test_unchanged_recommendation_returns_same_tier_and_market_conditions(self):
        analyzer = _make_analyzer()
        mc = Mock()
        analyzer.container.market_conditions_analyzer.get_current_conditions.return_value = Mock(
            is_err=False, value=mc
        )
        # Default adapted thresholds match the base call exactly -> no change.
        analyzer.container.adaptive_threshold_calculator.calculate.return_value = _make_adapted(
            trade_recommended=True, vrp_excellent=1.8, vrp_good=1.4, vrp_marginal=1.2,
            is_adjusted=False,
        )
        vrp = _make_vrp(1.5, Recommendation.GOOD)

        result_vrp, market_conditions = analyzer._apply_adaptive_thresholds("TEST", vrp)

        assert result_vrp.recommendation == Recommendation.GOOD
        assert market_conditions is mc

    def test_exception_during_threshold_calculation_falls_back_to_base(self):
        analyzer = _make_analyzer()
        analyzer.container.market_conditions_analyzer.get_current_conditions.return_value = Mock(
            is_err=False, value=Mock()
        )
        analyzer.container.adaptive_threshold_calculator.calculate.side_effect = RuntimeError("boom")
        vrp = _make_vrp(1.5, Recommendation.GOOD)

        result_vrp, market_conditions = analyzer._apply_adaptive_thresholds("TEST", vrp)

        assert result_vrp is vrp
        assert market_conditions is None
