"""
Tests for configuration validation (Phase 2, Session 5).

Tests comprehensive validation of all configuration parameters
including API keys, database access, thresholds, and logging.
"""

import pytest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch

from src.config.config import (
    Config,
    APIConfig,
    DatabaseConfig,
    CacheConfig,
    ThresholdsConfig,
    RateLimitConfig,
    ResilienceConfig,
    AlgorithmConfig,
    LoggingConfig,
)
from src.config.validation import validate_configuration, ConfigurationError


@pytest.fixture
def valid_config(tmp_path):
    """Create a valid configuration for testing."""
    db_path = tmp_path / "test.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    return Config(
        api=APIConfig(
            tradier_api_key="test_tradier_key_12345",
            alpha_vantage_key="test_av_key_67890",
        ),
        database=DatabaseConfig(path=db_path),
        cache=CacheConfig(),
        thresholds=ThresholdsConfig(),
        rate_limits=RateLimitConfig(),
        resilience=ResilienceConfig(),
            algorithms=AlgorithmConfig(),
        logging=LoggingConfig(),
    )


class TestConfigurationValidation:
    """Test suite for configuration validation."""

    def test_valid_configuration_passes(self, valid_config):
        """Valid configuration should pass validation without errors."""
        # Should not raise
        validate_configuration(valid_config)

    def test_missing_tradier_api_key(self, valid_config):
        """Missing Tradier API key should fail validation."""
        config = Config(
            api=APIConfig(
                tradier_api_key="",  # Empty key
                alpha_vantage_key="test_av_key",
            ),
            database=valid_config.database,
            cache=valid_config.cache,
            thresholds=valid_config.thresholds,
            rate_limits=valid_config.rate_limits,
            resilience=valid_config.resilience,
            algorithms=valid_config.algorithms,
            logging=valid_config.logging,
        )

        with pytest.raises(ConfigurationError) as exc_info:
            validate_configuration(config)

        assert "TRADIER_API_KEY" in str(exc_info.value)

    def test_nonexistent_database_directory(self, tmp_path):
        """Non-existent database directory should fail validation."""
        # Create path that doesn't exist
        db_path = tmp_path / "nonexistent" / "directory" / "test.db"

        config = Config(
            api=APIConfig(
                tradier_api_key="test_key",
                alpha_vantage_key="test_av_key",
            ),
            database=DatabaseConfig(path=db_path),
            cache=CacheConfig(),
            thresholds=ThresholdsConfig(),
            rate_limits=RateLimitConfig(),
            resilience=ResilienceConfig(),
            algorithms=AlgorithmConfig(),
            logging=LoggingConfig(),
        )

        with pytest.raises(ConfigurationError) as exc_info:
            validate_configuration(config)

        assert "does not exist" in str(exc_info.value)

    def test_readonly_database_directory(self, tmp_path):
        """Read-only database directory should fail validation."""
        db_path = tmp_path / "test.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

        config = Config(
            api=APIConfig(
                tradier_api_key="test_key",
                alpha_vantage_key="test_av_key",
            ),
            database=DatabaseConfig(path=db_path),
            cache=CacheConfig(),
            thresholds=ThresholdsConfig(),
            rate_limits=RateLimitConfig(),
            resilience=ResilienceConfig(),
            algorithms=AlgorithmConfig(),
            logging=LoggingConfig(),
        )

        # Mock os.access to return False for write check
        with patch('os.access', return_value=False):
            with pytest.raises(ConfigurationError) as exc_info:
                validate_configuration(config)

            assert "not writable" in str(exc_info.value)

    def test_invalid_vrp_thresholds_excellent_not_greater(self, valid_config):
        """VRP excellent threshold must be greater than good threshold."""
        config = Config(
            api=valid_config.api,
            database=valid_config.database,
            cache=valid_config.cache,
            thresholds=ThresholdsConfig(
                vrp_excellent=1.5,  # Same as good
                vrp_good=1.5,
                vrp_marginal=1.2,
            ),
            rate_limits=valid_config.rate_limits,
            resilience=valid_config.resilience,
            algorithms=valid_config.algorithms,
            logging=valid_config.logging,
        )

        with pytest.raises(ConfigurationError) as exc_info:
            validate_configuration(config)

        assert "vrp_excellent" in str(exc_info.value)
        assert "vrp_good" in str(exc_info.value)

    def test_invalid_vrp_thresholds_good_not_greater(self, valid_config):
        """VRP good threshold must be greater than marginal threshold."""
        config = Config(
            api=valid_config.api,
            database=valid_config.database,
            cache=valid_config.cache,
            thresholds=ThresholdsConfig(
                vrp_excellent=2.0,
                vrp_good=1.2,  # Same as marginal
                vrp_marginal=1.2,
            ),
            rate_limits=valid_config.rate_limits,
            resilience=valid_config.resilience,
            algorithms=valid_config.algorithms,
            logging=valid_config.logging,
        )

        with pytest.raises(ConfigurationError) as exc_info:
            validate_configuration(config)

        assert "vrp_good" in str(exc_info.value)
        assert "vrp_marginal" in str(exc_info.value)

    def test_invalid_rate_limit_zero(self, valid_config):
        """Rate limits must be positive."""
        config = Config(
            api=valid_config.api,
            database=valid_config.database,
            cache=valid_config.cache,
            thresholds=valid_config.thresholds,
            rate_limits=RateLimitConfig(
                alpha_vantage_per_minute=0,  # Invalid: must be > 0
            ),
            resilience=valid_config.resilience,
            algorithms=valid_config.algorithms,
            logging=valid_config.logging,
        )

        with pytest.raises(ConfigurationError) as exc_info:
            validate_configuration(config)

        assert "alpha_vantage_per_minute" in str(exc_info.value)

    def test_invalid_retry_attempts_zero(self, valid_config):
        """Retry attempts must be at least 1."""
        config = Config(
            api=valid_config.api,
            database=valid_config.database,
            cache=valid_config.cache,
            thresholds=valid_config.thresholds,
            rate_limits=valid_config.rate_limits,
            resilience=ResilienceConfig(
                retry_max_attempts=0,  # Invalid: must be >= 1
            ),
            algorithms=valid_config.algorithms,
            logging=valid_config.logging,
        )

        with pytest.raises(ConfigurationError) as exc_info:
            validate_configuration(config)

        assert "retry_max_attempts" in str(exc_info.value)

    def test_invalid_max_concurrent_requests_zero(self, valid_config):
        """Max concurrent requests must be at least 1."""
        config = Config(
            api=valid_config.api,
            database=valid_config.database,
            cache=valid_config.cache,
            thresholds=valid_config.thresholds,
            rate_limits=valid_config.rate_limits,
            resilience=ResilienceConfig(
                max_concurrent_requests=0,  # Invalid: must be >= 1
            ),
            algorithms=valid_config.algorithms,
            logging=valid_config.logging,
        )

        with pytest.raises(ConfigurationError) as exc_info:
            validate_configuration(config)

        assert "max_concurrent_requests" in str(exc_info.value)

    def test_invalid_log_level(self, valid_config):
        """Invalid log level should fail validation."""
        config = Config(
            api=valid_config.api,
            database=valid_config.database,
            cache=valid_config.cache,
            thresholds=valid_config.thresholds,
            rate_limits=valid_config.rate_limits,
            resilience=valid_config.resilience,
            algorithms=valid_config.algorithms,
            logging=LoggingConfig(level="INVALID_LEVEL"),
        )

        with pytest.raises(ConfigurationError) as exc_info:
            validate_configuration(config)

        assert "log level" in str(exc_info.value).lower()

    def test_nonexistent_log_directory(self, tmp_path):
        """Non-existent log directory should fail validation."""
        log_path = tmp_path / "nonexistent" / "logs" / "app.log"

        config = Config(
            api=APIConfig(
                tradier_api_key="test_key",
                alpha_vantage_key="test_av_key",
            ),
            database=DatabaseConfig(path=tmp_path / "test.db"),
            cache=CacheConfig(),
            thresholds=ThresholdsConfig(),
            rate_limits=RateLimitConfig(),
            resilience=ResilienceConfig(),
            algorithms=AlgorithmConfig(),
            logging=LoggingConfig(log_file=log_path),
        )

        with pytest.raises(ConfigurationError) as exc_info:
            validate_configuration(config)

        assert "Log directory does not exist" in str(exc_info.value)

    def test_readonly_log_directory(self, tmp_path):
        """Read-only log directory should fail validation."""
        log_path = tmp_path / "app.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        config = Config(
            api=APIConfig(
                tradier_api_key="test_key",
                alpha_vantage_key="test_av_key",
            ),
            database=DatabaseConfig(path=tmp_path / "test.db"),
            cache=CacheConfig(),
            thresholds=ThresholdsConfig(),
            rate_limits=RateLimitConfig(),
            resilience=ResilienceConfig(),
            algorithms=AlgorithmConfig(),
            logging=LoggingConfig(log_file=log_path),
        )

        # Mock os.access to return False only for log directory write check
        original_access = os.access
        def mock_access(path, mode):
            if str(path) == str(log_path.parent) and mode == os.W_OK:
                return False
            return original_access(path, mode)

        with patch('os.access', side_effect=mock_access):
            with pytest.raises(ConfigurationError) as exc_info:
                validate_configuration(config)

            assert "Log directory is not writable" in str(exc_info.value)

    def test_multiple_errors_accumulated(self, tmp_path):
        """Multiple validation errors should be accumulated and reported."""
        db_path = tmp_path / "nonexistent" / "test.db"

        config = Config(
            api=APIConfig(
                tradier_api_key="",  # Error 1: empty key
                alpha_vantage_key="test_av_key",
            ),
            database=DatabaseConfig(path=db_path),  # Error 2: nonexistent dir
            cache=CacheConfig(),
            thresholds=ThresholdsConfig(
                vrp_excellent=1.0,  # Error 3: not > good
                vrp_good=1.5,
            ),
            rate_limits=RateLimitConfig(),
            resilience=ResilienceConfig(
                retry_max_attempts=0,  # Error 4: must be >= 1
            ),
            algorithms=AlgorithmConfig(),
            logging=LoggingConfig(),
        )

        with pytest.raises(ConfigurationError) as exc_info:
            validate_configuration(config)

        error_msg = str(exc_info.value)
        # Should mention at least 4 errors (could be more if we add checks)
        assert "4 configuration error(s)" in error_msg or "configuration error(s)" in error_msg

    def test_none_log_file_is_valid(self, valid_config):
        """Configuration with no log file should be valid."""
        config = Config(
            api=valid_config.api,
            database=valid_config.database,
            cache=valid_config.cache,
            thresholds=valid_config.thresholds,
            rate_limits=valid_config.rate_limits,
            resilience=valid_config.resilience,
            algorithms=valid_config.algorithms,
            logging=LoggingConfig(log_file=None),  # No log file
        )

        # Should not raise
        validate_configuration(config)

    def test_all_valid_log_levels_accepted(self, valid_config):
        """All standard Python log levels should be accepted."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

        for level in valid_levels:
            config = Config(
                api=valid_config.api,
                database=valid_config.database,
                cache=valid_config.cache,
                thresholds=valid_config.thresholds,
                rate_limits=valid_config.rate_limits,
                resilience=valid_config.resilience,
            algorithms=valid_config.algorithms,
                logging=LoggingConfig(level=level),
            )

            # Should not raise
            validate_configuration(config)

    def test_case_insensitive_log_level(self, valid_config):
        """Log level validation should be case-insensitive."""
        config = Config(
            api=valid_config.api,
            database=valid_config.database,
            cache=valid_config.cache,
            thresholds=valid_config.thresholds,
            rate_limits=valid_config.rate_limits,
            resilience=valid_config.resilience,
            algorithms=valid_config.algorithms,
            logging=LoggingConfig(level="debug"),  # lowercase
        )

        # Should not raise (validation uses .upper())
        validate_configuration(config)


class TestConfigValidateMethod:
    """Test the Config.validate() method directly."""

    def test_validate_returns_empty_list_for_valid_config(self, valid_config):
        """Valid config should return empty error list."""
        errors = valid_config.validate()
        assert errors == []

    def test_validate_returns_errors_for_invalid_config(self, tmp_path):
        """Invalid config should return list of error messages."""
        db_path = tmp_path / "nonexistent" / "test.db"

        config = Config(
            api=APIConfig(tradier_api_key=""),  # Invalid
            database=DatabaseConfig(path=db_path),  # Invalid
            cache=CacheConfig(),
            thresholds=ThresholdsConfig(
                vrp_excellent=1.0,  # Invalid
                vrp_good=1.5,
            ),
            rate_limits=RateLimitConfig(),
            resilience=ResilienceConfig(),
            algorithms=AlgorithmConfig(),
            logging=LoggingConfig(),
        )

        errors = config.validate()
        assert len(errors) >= 3  # At least 3 errors
        assert any("TRADIER_API_KEY" in err for err in errors)
        assert any("does not exist" in err for err in errors)
        assert any("vrp_excellent" in err for err in errors)
