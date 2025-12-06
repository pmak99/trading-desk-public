"""Tests for adaptive VRP threshold calculator."""

import pytest
from unittest.mock import MagicMock

from src.application.metrics.adaptive_thresholds import (
    AdaptiveThresholdCalculator,
    AdaptedThresholds,
)
from src.config.config import ThresholdsConfig
from src.application.metrics.market_conditions import MarketConditions
from src.domain.types import Percentage


class TestAdaptiveThresholdCalculator:
    """Tests for AdaptiveThresholdCalculator."""

    @pytest.fixture
    def base_thresholds(self) -> ThresholdsConfig:
        """Create base thresholds config."""
        return ThresholdsConfig(
            vrp_excellent=7.0,
            vrp_good=4.0,
            vrp_marginal=1.5,
        )

    @pytest.fixture
    def calculator(self, base_thresholds) -> AdaptiveThresholdCalculator:
        """Create calculator with base thresholds."""
        return AdaptiveThresholdCalculator(base_thresholds)

    def _create_market_conditions(self, vix_level: float, regime: str) -> MarketConditions:
        """Helper to create mock market conditions."""
        mock = MagicMock(spec=MarketConditions)
        mock.regime = regime
        mock.vix_level = Percentage(vix_level)
        return mock

    def test_low_vix_no_adjustment(self, calculator):
        """Low VIX (< 15) should not adjust thresholds."""
        conditions = self._create_market_conditions(13.0, "low")
        adapted = calculator.calculate(conditions)

        assert adapted.vrp_excellent == 7.0
        assert adapted.vrp_good == 4.0
        assert adapted.vrp_marginal == 1.5
        assert adapted.adjustment_factor == 1.0
        assert adapted.trade_recommended is True
        assert not adapted.is_adjusted

    def test_normal_vix_no_adjustment(self, calculator):
        """Normal VIX (15-20) should not adjust thresholds."""
        conditions = self._create_market_conditions(18.0, "normal")
        adapted = calculator.calculate(conditions)

        assert adapted.vrp_excellent == 7.0
        assert adapted.vrp_good == 4.0
        assert adapted.vrp_marginal == 1.5
        assert adapted.adjustment_factor == 1.0
        assert adapted.trade_recommended is True

    def test_normal_high_vix_10pct_adjustment(self, calculator):
        """Normal-high VIX (20-25) should increase thresholds by 10%."""
        conditions = self._create_market_conditions(22.0, "normal_high")
        adapted = calculator.calculate(conditions)

        assert adapted.vrp_excellent == pytest.approx(7.7, rel=0.01)  # 7.0 * 1.1
        assert adapted.vrp_good == pytest.approx(4.4, rel=0.01)  # 4.0 * 1.1
        assert adapted.vrp_marginal == pytest.approx(1.65, rel=0.01)  # 1.5 * 1.1
        assert adapted.adjustment_factor == 1.1
        assert adapted.trade_recommended is True
        assert adapted.is_adjusted

    def test_elevated_vix_20pct_adjustment(self, calculator):
        """Elevated VIX (25-30) should increase thresholds by 20%."""
        conditions = self._create_market_conditions(27.0, "elevated")
        adapted = calculator.calculate(conditions)

        assert adapted.vrp_excellent == pytest.approx(8.4, rel=0.01)  # 7.0 * 1.2
        assert adapted.vrp_good == pytest.approx(4.8, rel=0.01)  # 4.0 * 1.2
        assert adapted.vrp_marginal == pytest.approx(1.8, rel=0.01)  # 1.5 * 1.2
        assert adapted.adjustment_factor == 1.2
        assert adapted.trade_recommended is True

    def test_elevated_high_vix_30pct_adjustment(self, calculator):
        """Elevated-high VIX (30-35) should increase thresholds by 30%."""
        conditions = self._create_market_conditions(32.0, "elevated_high")
        adapted = calculator.calculate(conditions)

        assert adapted.vrp_excellent == pytest.approx(9.1, rel=0.01)  # 7.0 * 1.3
        assert adapted.vrp_good == pytest.approx(5.2, rel=0.01)  # 4.0 * 1.3
        assert adapted.vrp_marginal == pytest.approx(1.95, rel=0.01)  # 1.5 * 1.3
        assert adapted.adjustment_factor == 1.3
        assert adapted.trade_recommended is True

    def test_high_vix_50pct_adjustment(self, calculator):
        """High VIX (35-40) should increase thresholds by 50%."""
        conditions = self._create_market_conditions(38.0, "high")
        adapted = calculator.calculate(conditions)

        assert adapted.vrp_excellent == pytest.approx(10.5, rel=0.01)  # 7.0 * 1.5
        assert adapted.vrp_good == pytest.approx(6.0, rel=0.01)  # 4.0 * 1.5
        assert adapted.vrp_marginal == pytest.approx(2.25, rel=0.01)  # 1.5 * 1.5
        assert adapted.adjustment_factor == 1.5
        assert adapted.trade_recommended is True

    def test_extreme_vix_no_trade(self, calculator):
        """Extreme VIX (40+) should recommend NOT trading."""
        conditions = self._create_market_conditions(45.0, "extreme")
        adapted = calculator.calculate(conditions)

        assert adapted.vrp_excellent == pytest.approx(14.0, rel=0.01)  # 7.0 * 2.0
        assert adapted.adjustment_factor == 2.0
        assert adapted.trade_recommended is False

    def test_calculate_from_vix_level(self, calculator):
        """Test direct VIX level calculation without MarketConditions."""
        # Low VIX
        adapted = calculator.calculate_from_vix(13.0)
        assert adapted.regime == "low"
        assert adapted.adjustment_factor == 1.0

        # Elevated VIX
        adapted = calculator.calculate_from_vix(27.0)
        assert adapted.regime == "elevated"
        assert adapted.adjustment_factor == 1.2

        # Extreme VIX
        adapted = calculator.calculate_from_vix(50.0)
        assert adapted.regime == "extreme"
        assert adapted.trade_recommended is False

    def test_get_recommendation_with_adjustment(self, calculator):
        """Test recommendation with adaptive thresholds."""
        # VRP of 5.0 should be GOOD at normal VIX
        rec = calculator.get_recommendation_with_adjustment(5.0, vix_level=15.0)
        assert rec == "GOOD"

        # VRP of 5.0 should be MARGINAL at elevated VIX (thresholds * 1.2)
        # good threshold becomes 4.8, excellent becomes 8.4
        # 5.0 >= 4.8 so still GOOD
        rec = calculator.get_recommendation_with_adjustment(5.0, vix_level=27.0)
        assert rec == "GOOD"

        # VRP of 4.5 should be GOOD at normal VIX (>= 4.0)
        rec = calculator.get_recommendation_with_adjustment(4.5, vix_level=15.0)
        assert rec == "GOOD"

        # VRP of 4.5 should be MARGINAL at elevated VIX (4.5 < 4.8)
        rec = calculator.get_recommendation_with_adjustment(4.5, vix_level=27.0)
        assert rec == "MARGINAL"

        # VRP of 7.5 should be EXCELLENT at normal VIX
        rec = calculator.get_recommendation_with_adjustment(7.5, vix_level=15.0)
        assert rec == "EXCELLENT"

        # VRP of 7.5 should be GOOD at high VIX (7.5 < 10.5 excellent threshold)
        rec = calculator.get_recommendation_with_adjustment(7.5, vix_level=38.0)
        assert rec == "GOOD"

    def test_extreme_vix_always_skip(self, calculator):
        """Any VRP ratio should SKIP in extreme VIX regime."""
        # Even an excellent VRP should be SKIP in extreme VIX
        rec = calculator.get_recommendation_with_adjustment(10.0, vix_level=45.0)
        assert rec == "SKIP"

    def test_no_market_data_uses_base_thresholds(self, calculator):
        """When no market data is available, use base thresholds."""
        rec = calculator.get_recommendation_with_adjustment(5.0)
        assert rec == "GOOD"

        rec = calculator.get_recommendation_with_adjustment(7.5)
        assert rec == "EXCELLENT"

        rec = calculator.get_recommendation_with_adjustment(1.0)
        assert rec == "SKIP"


class TestAdaptedThresholds:
    """Tests for AdaptedThresholds dataclass."""

    def test_is_adjusted_property(self):
        """Test is_adjusted property."""
        # Not adjusted
        adapted = AdaptedThresholds(
            vrp_excellent=7.0,
            vrp_good=4.0,
            vrp_marginal=1.5,
            regime="normal",
            vix_level=18.0,
            adjustment_factor=1.0,
            trade_recommended=True,
        )
        assert not adapted.is_adjusted

        # Adjusted
        adapted = AdaptedThresholds(
            vrp_excellent=8.4,
            vrp_good=4.8,
            vrp_marginal=1.8,
            regime="elevated",
            vix_level=27.0,
            adjustment_factor=1.2,
            trade_recommended=True,
        )
        assert adapted.is_adjusted

    def test_frozen_dataclass(self):
        """Verify AdaptedThresholds is immutable."""
        adapted = AdaptedThresholds(
            vrp_excellent=7.0,
            vrp_good=4.0,
            vrp_marginal=1.5,
            regime="normal",
            vix_level=18.0,
            adjustment_factor=1.0,
            trade_recommended=True,
        )

        with pytest.raises(Exception):  # FrozenInstanceError
            adapted.vrp_excellent = 10.0
