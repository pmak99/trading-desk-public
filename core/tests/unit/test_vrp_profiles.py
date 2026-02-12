"""
Unit tests for VRP threshold profile selection.

Tests the profile-based VRP threshold system introduced in Fix #4,
ensuring correct profile loading, env var overrides, and validation.
"""

import os
import pytest
from unittest.mock import patch

from src.config.config import Config


class TestVRPProfileSelection:
    """Test VRP threshold profile selection and configuration."""

    def test_default_balanced_profile(self):
        """Test that BALANCED profile is used by default."""
        with patch.dict(os.environ, {}, clear=True):
            config = Config.from_env()

            assert config.thresholds.vrp_threshold_mode == "BALANCED"
            assert config.thresholds.vrp_excellent == 1.8
            assert config.thresholds.vrp_good == 1.4
            assert config.thresholds.vrp_marginal == 1.2

    def test_conservative_profile(self):
        """Test CONSERVATIVE profile selection."""
        with patch.dict(os.environ, {"VRP_THRESHOLD_MODE": "CONSERVATIVE"}, clear=True):
            config = Config.from_env()

            assert config.thresholds.vrp_threshold_mode == "CONSERVATIVE"
            assert config.thresholds.vrp_excellent == 2.0
            assert config.thresholds.vrp_good == 1.5
            assert config.thresholds.vrp_marginal == 1.2

    def test_aggressive_profile(self):
        """Test AGGRESSIVE profile selection."""
        with patch.dict(os.environ, {"VRP_THRESHOLD_MODE": "AGGRESSIVE"}, clear=True):
            config = Config.from_env()

            assert config.thresholds.vrp_threshold_mode == "AGGRESSIVE"
            assert config.thresholds.vrp_excellent == 1.5
            assert config.thresholds.vrp_good == 1.3
            assert config.thresholds.vrp_marginal == 1.1

    def test_legacy_profile(self):
        """Test LEGACY profile (original overfitted thresholds)."""
        with patch.dict(os.environ, {"VRP_THRESHOLD_MODE": "LEGACY"}, clear=True):
            config = Config.from_env()

            assert config.thresholds.vrp_threshold_mode == "LEGACY"
            assert config.thresholds.vrp_excellent == 7.0
            assert config.thresholds.vrp_good == 4.0
            assert config.thresholds.vrp_marginal == 1.5

    def test_case_insensitive_profile_mode(self):
        """Test that profile mode is case-insensitive."""
        with patch.dict(os.environ, {"VRP_THRESHOLD_MODE": "balanced"}, clear=True):
            config = Config.from_env()

            assert config.thresholds.vrp_threshold_mode == "BALANCED"
            assert config.thresholds.vrp_excellent == 1.8

        with patch.dict(os.environ, {"VRP_THRESHOLD_MODE": "AgGrEsSiVe"}, clear=True):
            config = Config.from_env()

            assert config.thresholds.vrp_threshold_mode == "AGGRESSIVE"
            assert config.thresholds.vrp_excellent == 1.5

    def test_invalid_profile_defaults_to_balanced(self):
        """Test that invalid profile mode defaults to BALANCED with warning."""
        with patch.dict(os.environ, {"VRP_THRESHOLD_MODE": "INVALID_MODE"}, clear=True):
            config = Config.from_env()

            # Should default to BALANCED
            assert config.thresholds.vrp_threshold_mode == "BALANCED"
            assert config.thresholds.vrp_excellent == 1.8
            assert config.thresholds.vrp_good == 1.4

    def test_individual_threshold_override(self):
        """Test that individual env vars can override profile defaults."""
        env_vars = {
            "VRP_THRESHOLD_MODE": "BALANCED",  # excellent=1.8, good=1.4, marginal=1.2
            "VRP_EXCELLENT": "2.5",  # Override excellent
        }

        with patch.dict(os.environ, env_vars, clear=True):
            config = Config.from_env()

            assert config.thresholds.vrp_threshold_mode == "BALANCED"
            assert config.thresholds.vrp_excellent == 2.5  # Overridden
            assert config.thresholds.vrp_good == 1.4       # Profile default
            assert config.thresholds.vrp_marginal == 1.2   # Profile default

    def test_multiple_threshold_overrides(self):
        """Test that multiple individual thresholds can be overridden."""
        env_vars = {
            "VRP_THRESHOLD_MODE": "CONSERVATIVE",  # excellent=2.0, good=1.5, marginal=1.2
            "VRP_EXCELLENT": "3.0",
            "VRP_GOOD": "core",
            "VRP_MARGINAL": "1.5",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            config = Config.from_env()

            assert config.thresholds.vrp_threshold_mode == "CONSERVATIVE"
            assert config.thresholds.vrp_excellent == 3.0   # All overridden
            assert config.thresholds.vrp_good == 2.0
            assert config.thresholds.vrp_marginal == 1.5

    def test_partial_override_aggressive_profile(self):
        """Test partial override on AGGRESSIVE profile."""
        env_vars = {
            "VRP_THRESHOLD_MODE": "AGGRESSIVE",  # excellent=1.5, good=1.3, marginal=1.1
            "VRP_GOOD": "1.4",  # Only override good
        }

        with patch.dict(os.environ, env_vars, clear=True):
            config = Config.from_env()

            assert config.thresholds.vrp_threshold_mode == "AGGRESSIVE"
            assert config.thresholds.vrp_excellent == 1.5  # Profile default
            assert config.thresholds.vrp_good == 1.4       # Overridden
            assert config.thresholds.vrp_marginal == 1.1   # Profile default

    def test_override_without_mode_uses_default_balanced(self):
        """Test that overrides work even without explicit mode (defaults to BALANCED)."""
        env_vars = {
            "VRP_EXCELLENT": "2.2",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            config = Config.from_env()

            assert config.thresholds.vrp_threshold_mode == "BALANCED"
            assert config.thresholds.vrp_excellent == 2.2  # Overridden
            assert config.thresholds.vrp_good == 1.4       # BALANCED default
            assert config.thresholds.vrp_marginal == 1.2   # BALANCED default

    def test_all_profiles_have_required_keys(self):
        """Test that all profiles define excellent, good, and marginal thresholds."""
        profiles = ["CONSERVATIVE", "BALANCED", "AGGRESSIVE", "LEGACY"]

        for profile_name in profiles:
            with patch.dict(os.environ, {"VRP_THRESHOLD_MODE": profile_name}, clear=True):
                config = Config.from_env()

                # All thresholds should be positive floats
                assert config.thresholds.vrp_excellent > 0
                assert config.thresholds.vrp_good > 0
                assert config.thresholds.vrp_marginal > 0

                # Excellent should be >= good >= marginal (stricter to looser)
                assert config.thresholds.vrp_excellent >= config.thresholds.vrp_good
                assert config.thresholds.vrp_good >= config.thresholds.vrp_marginal

    def test_profile_ordering_conservative(self):
        """Test that CONSERVATIVE profile has strictest thresholds (highest values)."""
        with patch.dict(os.environ, {"VRP_THRESHOLD_MODE": "CONSERVATIVE"}, clear=True):
            conservative = Config.from_env()

        with patch.dict(os.environ, {"VRP_THRESHOLD_MODE": "BALANCED"}, clear=True):
            balanced = Config.from_env()

        with patch.dict(os.environ, {"VRP_THRESHOLD_MODE": "AGGRESSIVE"}, clear=True):
            aggressive = Config.from_env()

        # CONSERVATIVE should have highest thresholds (strictest)
        assert conservative.thresholds.vrp_excellent >= balanced.thresholds.vrp_excellent
        assert conservative.thresholds.vrp_excellent >= aggressive.thresholds.vrp_excellent

        assert conservative.thresholds.vrp_good >= balanced.thresholds.vrp_good
        assert conservative.thresholds.vrp_good >= aggressive.thresholds.vrp_good

    def test_profile_ordering_aggressive(self):
        """Test that AGGRESSIVE profile has loosest thresholds (lowest values)."""
        with patch.dict(os.environ, {"VRP_THRESHOLD_MODE": "BALANCED"}, clear=True):
            balanced = Config.from_env()

        with patch.dict(os.environ, {"VRP_THRESHOLD_MODE": "AGGRESSIVE"}, clear=True):
            aggressive = Config.from_env()

        # AGGRESSIVE should have lowest thresholds (loosest)
        assert aggressive.thresholds.vrp_excellent <= balanced.thresholds.vrp_excellent
        assert aggressive.thresholds.vrp_good <= balanced.thresholds.vrp_good

    def test_legacy_profile_extreme_values(self):
        """Test that LEGACY profile has extreme (overfitted) values."""
        with patch.dict(os.environ, {"VRP_THRESHOLD_MODE": "LEGACY"}, clear=True):
            legacy = Config.from_env()

        # LEGACY should have extremely high thresholds (overfitted to 8 trades)
        assert legacy.thresholds.vrp_excellent >= 7.0
        assert legacy.thresholds.vrp_good >= 4.0

        # LEGACY excellent should be much higher than any other profile
        with patch.dict(os.environ, {"VRP_THRESHOLD_MODE": "CONSERVATIVE"}, clear=True):
            conservative = Config.from_env()

        assert legacy.thresholds.vrp_excellent > conservative.thresholds.vrp_excellent * 2

    def test_float_conversion_for_overrides(self):
        """Test that env var overrides are properly converted to floats."""
        env_vars = {
            "VRP_EXCELLENT": "1.75",
            "VRP_GOOD": "1.35",
            "VRP_MARGINAL": "1.15",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            config = Config.from_env()

            # Should be floats, not strings
            assert isinstance(config.thresholds.vrp_excellent, float)
            assert isinstance(config.thresholds.vrp_good, float)
            assert isinstance(config.thresholds.vrp_marginal, float)

            assert config.thresholds.vrp_excellent == 1.75
            assert config.thresholds.vrp_good == 1.35
            assert config.thresholds.vrp_marginal == 1.15


class TestVRPProfileIntegration:
    """Integration tests for VRP profile system with other config components."""

    def test_profile_mode_persisted_in_thresholds_config(self):
        """Test that profile mode is stored in ThresholdsConfig for auditing."""
        with patch.dict(os.environ, {"VRP_THRESHOLD_MODE": "AGGRESSIVE"}, clear=True):
            config = Config.from_env()

            # Mode should be accessible for logging/debugging
            assert hasattr(config.thresholds, 'vrp_threshold_mode')
            assert config.thresholds.vrp_threshold_mode == "AGGRESSIVE"

    def test_profile_independent_of_other_configs(self):
        """Test that VRP profile selection doesn't affect other configurations."""
        env_vars = {
            "VRP_THRESHOLD_MODE": "CONSERVATIVE",
            "RISK_BUDGET_PER_TRADE": "5000",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            config = Config.from_env()

            # VRP profile should be set
            assert config.thresholds.vrp_threshold_mode == "CONSERVATIVE"
            assert config.thresholds.vrp_excellent == 2.0

            # Other configs should still work
            assert config.strategy.risk_budget_per_trade == 5000

    def test_profile_with_kelly_criterion_enabled(self):
        """Test VRP profiles work correctly with Kelly Criterion enabled."""
        env_vars = {
            "VRP_THRESHOLD_MODE": "BALANCED",
            "USE_KELLY_SIZING": "true",
            "KELLY_FRACTION": "0.25",
        }

        with patch.dict(os.environ, env_vars, clear=True):
            config = Config.from_env()

            # Both VRP profile and Kelly should be configured
            assert config.thresholds.vrp_threshold_mode == "BALANCED"
            assert config.thresholds.vrp_excellent == 1.8
            assert config.strategy.use_kelly_sizing is True
            assert config.strategy.kelly_fraction == 0.25
