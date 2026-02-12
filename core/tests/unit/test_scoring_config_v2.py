"""
Test suite for scoring configuration.

Tests scoring weights, thresholds, and configurations with the new
VRP thresholds (2.0x/1.5x/1.2x) updated from comprehensive backtesting.
"""

import pytest
from src.config.scoring_config import (
    ScoringWeights,
    ScoringThresholds,
    ScoringConfig,
    get_all_configs,
    get_config,
    list_configs,
)


class TestScoringWeights:
    """Test ScoringWeights validation."""

    def test_valid_weights_sum_to_one(self):
        """Valid weights that sum to 1.0."""
        weights = ScoringWeights(
            vrp_weight=0.40,
            consistency_weight=0.25,
            skew_weight=0.15,
            liquidity_weight=0.20,
        )
        assert weights.vrp_weight == 0.40
        assert weights.consistency_weight == 0.25
        assert weights.skew_weight == 0.15
        assert weights.liquidity_weight == 0.20

    def test_weights_must_sum_to_one(self):
        """Weights must sum to approximately 1.0."""
        with pytest.raises(ValueError, match="must sum to 1.0"):
            ScoringWeights(
                vrp_weight=0.50,
                consistency_weight=0.25,
                skew_weight=0.15,
                liquidity_weight=0.15,  # Total = 1.05, invalid
            )

    def test_weights_allow_small_floating_point_error(self):
        """Weights allow small floating point errors."""
        weights = ScoringWeights(
            vrp_weight=0.4001,  # Small rounding error
            consistency_weight=0.25,
            skew_weight=0.15,
            liquidity_weight=0.1999,
        )
        assert weights is not None

    def test_negative_weight_rejected(self):
        """Negative weights are rejected."""
        with pytest.raises(ValueError, match="between 0 and 1"):
            ScoringWeights(
                vrp_weight=-0.1,
                consistency_weight=0.55,
                skew_weight=0.15,
                liquidity_weight=0.40,
            )

    def test_weight_above_one_rejected(self):
        """Weights above 1.0 are rejected."""
        with pytest.raises(ValueError, match="between 0 and 1"):
            ScoringWeights(
                vrp_weight=1.5,
                consistency_weight=-0.2,
                skew_weight=0.15,
                liquidity_weight=-0.45,
            )


class TestScoringThresholds:
    """Test ScoringThresholds with new values."""

    def test_default_thresholds_updated_values(self):
        """Default thresholds use new research-backed values."""
        thresholds = ScoringThresholds()

        # New VRP thresholds (updated from 7.0/sentiment)
        assert thresholds.vrp_excellent == 2.0
        assert thresholds.vrp_good == 1.5
        assert thresholds.vrp_marginal == 1.2

        # Consistency thresholds unchanged
        assert thresholds.consistency_excellent == 0.8
        assert thresholds.consistency_good == 0.6
        assert thresholds.consistency_marginal == 0.4

    def test_custom_thresholds_vrp_dominant(self):
        """Custom thresholds for VRP-Dominant config."""
        thresholds = ScoringThresholds(
            vrp_excellent=2.2,  # Higher than default
            vrp_good=1.6,
            vrp_marginal=1.3,
            min_composite_score=62.0,
        )
        assert thresholds.vrp_excellent == 2.2
        assert thresholds.vrp_good == 1.6
        assert thresholds.vrp_marginal == 1.3
        assert thresholds.min_composite_score == 62.0

    def test_custom_thresholds_aggressive(self):
        """Custom thresholds for Aggressive config."""
        thresholds = ScoringThresholds(
            vrp_excellent=1.5,  # Lower than default
            vrp_good=1.3,
            vrp_marginal=1.1,
            min_composite_score=50.0,
        )
        assert thresholds.vrp_excellent == 1.5
        assert thresholds.vrp_good == 1.3
        assert thresholds.vrp_marginal == 1.1

    def test_custom_thresholds_conservative(self):
        """Custom thresholds for Conservative config."""
        thresholds = ScoringThresholds(
            vrp_excellent=2.5,  # Higher than default
            vrp_good=1.8,
            vrp_marginal=1.4,
            min_composite_score=65.0,
        )
        assert thresholds.vrp_excellent == 2.5
        assert thresholds.vrp_good == 1.8
        assert thresholds.vrp_marginal == 1.4

    def test_liquidity_thresholds(self):
        """Liquidity thresholds are properly set (4-tier aligned)."""
        thresholds = ScoringThresholds()
        assert thresholds.min_open_interest == 100
        assert thresholds.good_open_interest == 500
        assert thresholds.excellent_open_interest == 1000
        # 4-tier aligned spread thresholds
        assert thresholds.max_spread_excellent == 8.0  # <=8% excellent
        assert thresholds.max_spread_good == 12.0  # <=12% good
        assert thresholds.max_spread_warning == 15.0  # <=15% warning


class TestScoringConfig:
    """Test ScoringConfig objects."""

    def test_scoring_config_creation(self):
        """ScoringConfig can be created with all fields."""
        weights = ScoringWeights(
            vrp_weight=0.40,
            consistency_weight=0.25,
            skew_weight=0.15,
            liquidity_weight=0.20,
        )
        thresholds = ScoringThresholds()

        config = ScoringConfig(
            name="Test Config",
            description="Test description",
            weights=weights,
            thresholds=thresholds,
            max_positions=10,
            min_score=60.0,
        )

        assert config.name == "Test Config"
        assert config.description == "Test description"
        assert config.weights == weights
        assert config.thresholds == thresholds
        assert config.max_positions == 10
        assert config.min_score == 60.0


class TestPredefinedConfigs:
    """Test predefined scoring configurations."""

    def test_get_all_configs_returns_eight(self):
        """get_all_configs returns all 8 configurations."""
        configs = get_all_configs()
        assert len(configs) == 8

        expected_configs = [
            "vrp_dominant",
            "balanced",
            "liquidity_first",
            "consistency_heavy",
            "skew_aware",
            "aggressive",
            "conservative",
            "hybrid",
        ]

        for name in expected_configs:
            assert name in configs

    def test_vrp_dominant_config(self):
        """VRP-Dominant config has correct settings."""
        config = get_config("vrp_dominant")

        assert config.name == "VRP-Dominant"
        assert config.weights.vrp_weight == 0.70
        assert config.thresholds.vrp_excellent == 2.2
        assert config.thresholds.vrp_good == 1.6
        assert config.thresholds.vrp_marginal == 1.3
        assert config.max_positions == 10
        assert config.min_score == 62.0

    def test_balanced_config(self):
        """Balanced config has correct settings."""
        config = get_config("balanced")

        assert config.name == "Balanced"
        assert config.weights.vrp_weight == 0.40
        assert config.weights.consistency_weight == 0.25
        assert config.weights.skew_weight == 0.15
        assert config.weights.liquidity_weight == 0.20
        assert config.max_positions == 12
        assert config.min_score == 60.0

    def test_aggressive_config_lower_thresholds(self):
        """Aggressive config has lower VRP thresholds."""
        config = get_config("aggressive")

        assert config.name == "Aggressive"
        assert config.thresholds.vrp_excellent == 1.5  # Lower than default
        assert config.thresholds.vrp_good == 1.3
        assert config.thresholds.vrp_marginal == 1.1
        assert config.min_score == 50.0  # Lower bar
        assert config.max_positions == 15  # More trades

    def test_conservative_config_higher_thresholds(self):
        """Conservative config has higher VRP thresholds."""
        config = get_config("conservative")

        assert config.name == "Conservative"
        assert config.thresholds.vrp_excellent == 2.5  # Higher than default
        assert config.thresholds.vrp_good == 1.8
        assert config.thresholds.vrp_marginal == 1.4
        assert config.min_score == 65.0  # Higher bar
        assert config.max_positions == 6  # Fewer trades

    def test_liquidity_first_config(self):
        """Liquidity-First config prioritizes liquidity."""
        config = get_config("liquidity_first")

        assert config.name == "Liquidity-First"
        assert config.weights.liquidity_weight == 0.35  # Highest liquidity weight
        assert config.weights.vrp_weight == 0.30

    def test_consistency_heavy_config(self):
        """Consistency-Heavy config prioritizes consistency."""
        config = get_config("consistency_heavy")

        assert config.name == "Consistency-Heavy"
        assert config.weights.consistency_weight == 0.45  # Highest consistency weight
        assert config.max_positions == 8
        assert config.min_score == 65.0

    def test_skew_aware_config(self):
        """Skew-Aware config emphasizes skew analysis."""
        config = get_config("skew_aware")

        assert config.name == "Skew-Aware"
        assert config.weights.skew_weight == 0.30  # Highest skew weight

    def test_hybrid_config(self):
        """Hybrid config balances multiple factors."""
        config = get_config("hybrid")

        assert config.name == "Hybrid"
        assert config.weights.vrp_weight == 0.45
        assert config.min_score == 62.0

    def test_get_config_case_insensitive(self):
        """get_config is case-insensitive."""
        config1 = get_config("balanced")
        config2 = get_config("BALANCED")
        config3 = get_config("Balanced")

        assert config1.name == config2.name == config3.name

    def test_get_config_handles_hyphens(self):
        """get_config handles hyphens and underscores."""
        config1 = get_config("vrp-dominant")
        config2 = get_config("vrp_dominant")

        assert config1.name == config2.name

    def test_get_config_unknown_raises_error(self):
        """get_config raises KeyError for unknown config."""
        with pytest.raises(KeyError, match="Unknown config"):
            get_config("nonexistent_config")

    def test_list_configs(self):
        """list_configs returns all config names."""
        configs = list_configs()
        assert len(configs) == 8
        assert "vrp_dominant" in configs
        assert "balanced" in configs
