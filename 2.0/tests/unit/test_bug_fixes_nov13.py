"""
Unit tests for bug fixes made on 2025-11-13.

These tests ensure the bugs don't regress:
1. ConsistencyAnalyzerEnhanced initialization
2. EarningsTiming enum value
3. Alpha Vantage attribute name
4. SkewAnalysis directional_bias attribute support
"""

import pytest
from datetime import date
from src.application.metrics.consistency_enhanced import ConsistencyAnalyzerEnhanced
from src.domain.enums import EarningsTiming, DirectionalBias
from src.container import Container
from src.config.config import Config


class TestBugFix1ConsistencyAnalyzer:
    """Test Bug Fix #1: ConsistencyAnalyzerEnhanced initialization."""

    def test_consistency_analyzer_no_params(self):
        """Verify ConsistencyAnalyzerEnhanced can be created without parameters."""
        # This should not raise TypeError
        analyzer = ConsistencyAnalyzerEnhanced()
        assert analyzer is not None

    def test_consistency_analyzer_has_analyze_method(self):
        """Verify the analyzer has the analyze_consistency method."""
        analyzer = ConsistencyAnalyzerEnhanced()
        assert hasattr(analyzer, 'analyze_consistency')
        assert callable(getattr(analyzer, 'analyze_consistency'))


class TestBugFix2EarningsTimingEnum:
    """Test Bug Fix #2: EarningsTiming enum value."""

    def test_earnings_timing_amc_exists(self):
        """Verify EarningsTiming.AMC exists (After Market Close)."""
        timing = EarningsTiming.AMC
        assert timing == EarningsTiming.AMC

    def test_earnings_timing_amc_is_string(self):
        """Verify AMC has correct string value."""
        assert EarningsTiming.AMC.value == "AMC"

    def test_earnings_timing_bmo_exists(self):
        """Verify EarningsTiming.BMO exists (Before Market Open)."""
        timing = EarningsTiming.BMO
        assert timing == EarningsTiming.BMO

    def test_after_close_does_not_exist(self):
        """Verify AFTER_CLOSE doesn't exist (old incorrect name)."""
        with pytest.raises(AttributeError):
            _ = EarningsTiming.AFTER_CLOSE


class TestBugFix3AlphaVantageAttribute:
    """Test Bug Fix #3: Alpha Vantage attribute name."""

    def test_container_has_alphavantage_attribute(self, container):
        """Verify Container has 'alphavantage' attribute (not 'alpha_vantage_api')."""
        # Should have 'alphavantage'
        assert hasattr(container, 'alphavantage')

        # Should NOT have 'alpha_vantage_api'
        assert not hasattr(container, 'alpha_vantage_api')

    def test_alphavantage_is_callable(self, container):
        """Verify alphavantage property is accessible."""
        # Should be able to access (lazy-loaded)
        alpha_vantage = container.alphavantage
        assert alpha_vantage is not None


class TestBugFix4SkewAnalysisDirectionalBias:
    """Test Bug Fix #4: SkewAnalysis directional_bias attribute support."""

    def test_directional_bias_enum_exists(self):
        """Verify DirectionalBias enum exists."""
        assert DirectionalBias.NEUTRAL is not None
        assert DirectionalBias.BULLISH is not None
        assert DirectionalBias.BEARISH is not None

    def test_directional_bias_values(self):
        """Verify DirectionalBias enum has correct values."""
        assert DirectionalBias.NEUTRAL.value == "neutral"
        assert DirectionalBias.BULLISH.value == "bullish"
        assert DirectionalBias.BEARISH.value == "bearish"

    def test_strategy_generator_exists(self):
        """Verify StrategyGenerator can be imported."""
        from src.application.services.strategy_generator import StrategyGenerator
        assert StrategyGenerator is not None

    def test_strategy_generator_determine_bias_method_exists(self):
        """Verify StrategyGenerator has _determine_bias method."""
        from src.application.services.strategy_generator import StrategyGenerator
        from src.config.config import StrategyConfig
        from src.application.metrics.liquidity_scorer import LiquidityScorer
        # StrategyGenerator now requires config and liquidity_scorer
        config = StrategyConfig()
        liquidity_scorer = LiquidityScorer()
        generator = StrategyGenerator(config, liquidity_scorer)
        assert hasattr(generator, '_determine_bias')
        assert callable(getattr(generator, '_determine_bias'))

    def test_skew_analysis_has_directional_bias_attribute(self):
        """Verify SkewAnalysis dataclass has directional_bias field."""
        from src.application.metrics.skew_enhanced import SkewAnalysis
        from src.domain.types import Money, Percentage

        # Create a sample SkewAnalysis with new required fields
        skew = SkewAnalysis(
            ticker="TEST",
            expiration=date(2025, 11, 20),
            stock_price=Money(100.0),
            skew_atm=Percentage(0.5),
            curvature=0.1,
            strength="smirk",
            directional_bias=DirectionalBias.BEARISH,  # Now uses enum
            confidence=0.95,
            num_points=10,
            slope_atm=0.05,  # New required field
            bias_confidence=0.8,  # New required field
        )

        assert hasattr(skew, 'directional_bias')
        assert skew.directional_bias == DirectionalBias.BEARISH

    def test_skew_analysis_directional_bias_values(self):
        """Verify SkewAnalysis supports all DirectionalBias enum values."""
        from src.application.metrics.skew_enhanced import SkewAnalysis
        from src.domain.types import Money, Percentage

        # DirectionalBias now has 7 levels
        valid_biases = [
            DirectionalBias.STRONG_BEARISH,
            DirectionalBias.BEARISH,
            DirectionalBias.WEAK_BEARISH,
            DirectionalBias.NEUTRAL,
            DirectionalBias.WEAK_BULLISH,
            DirectionalBias.BULLISH,
            DirectionalBias.STRONG_BULLISH,
        ]

        for bias in valid_biases:
            skew = SkewAnalysis(
                ticker="TEST",
                expiration=date(2025, 11, 20),
                stock_price=Money(100.0),
                skew_atm=Percentage(0.5),
                curvature=0.1,
                strength="smirk",
                directional_bias=bias,
                confidence=0.95,
                num_points=10,
                slope_atm=0.05,
                bias_confidence=0.8,
            )
            assert skew.directional_bias == bias


class TestBugFixesIntegration:
    """Integration tests to verify all bug fixes work together."""

    def test_container_creates_all_components(self, container, config):
        """Verify Container can create all Phase 4 components without errors."""
        # Should be able to access all components
        assert container.alphavantage is not None

        # ConsistencyAnalyzer can be created if enabled
        if config.algorithms.use_enhanced_consistency:
            assert container.consistency_analyzer is not None

    def test_ticker_analysis_enum_usage(self):
        """Verify TickerAnalysis can be created with correct EarningsTiming enum."""
        from src.domain.types import TickerAnalysis, ImpliedMove, VRPResult, Money, Percentage, Strike
        from src.domain.enums import Recommendation
        from datetime import datetime

        # Create a sample analysis with AMC timing
        analysis = TickerAnalysis(
            ticker="TEST",
            earnings_date=date(2025, 11, 20),
            earnings_timing=EarningsTiming.AMC,  # Using correct enum value
            entry_time=datetime.now(),
            expiration=date(2025, 11, 21),
            implied_move=ImpliedMove(
                ticker="TEST",
                expiration=date(2025, 11, 21),
                stock_price=Money(100.0),
                atm_strike=Strike(100.0),
                straddle_cost=Money(5.0),
                implied_move_pct=Percentage(5.0),
                upper_bound=Money(105.0),
                lower_bound=Money(95.0),
                avg_iv=Percentage(50.0)
            ),
            vrp=VRPResult(
                ticker="TEST",
                expiration=date(2025, 11, 21),
                vrp_ratio=2.0,
                implied_move_pct=Percentage(5.0),
                historical_mean_move_pct=Percentage(2.5),
                recommendation=Recommendation.EXCELLENT,
                edge_score=1.5
            ),
            consistency=None,
            skew=None,
            term_structure=None,
            strategies=None
        )

        assert analysis.earnings_timing == EarningsTiming.AMC
