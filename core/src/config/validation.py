"""
Configuration validation for startup checks (Phase 2).

Validates all configuration at startup to fail fast on misconfiguration.
"""

import logging
import os
from pathlib import Path
from src.config.config import Config

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Raised when configuration is invalid."""

    pass


def validate_configuration(config: Config) -> None:
    """
    Validate all configuration at startup.

    Args:
        config: Configuration to validate

    Raises:
        ConfigurationError: If any validation fails

    Phase: 2 (Days 29-35)
    """
    errors = config.validate()

    # Additional detailed checks
    _validate_database_access(config, errors)
    _validate_logging_setup(config, errors)
    _validate_cache_config(config, errors)

    if errors:
        for error in errors:
            logger.error(f"Configuration error: {error}")
        error_summary = "\n".join(f"  - {error}" for error in errors)
        raise ConfigurationError(
            f"{len(errors)} configuration error(s):\n{error_summary}"
        )

    logger.info("✓ Configuration validated successfully")


def _validate_database_access(config: Config, errors: list) -> None:
    """Validate database directory is writable."""
    db_path = config.database.path
    db_parent = db_path.parent

    if not db_parent.exists():
        errors.append(f"Database directory does not exist: {db_parent}")
    elif not os.access(db_parent, os.W_OK):
        errors.append(f"Database directory is not writable: {db_parent}")


def _validate_logging_setup(config: Config, errors: list) -> None:
    """Validate logging configuration."""
    if config.logging.log_file:
        log_parent = config.logging.log_file.parent
        if not log_parent.exists():
            errors.append(f"Log directory does not exist: {log_parent}")
        elif not os.access(log_parent, os.W_OK):
            errors.append(f"Log directory is not writable: {log_parent}")

    # Validate log level
    valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    if config.logging.level.upper() not in valid_levels:
        errors.append(
            f"Invalid log level: {config.logging.level}. Must be one of {valid_levels}"
        )


def _validate_cache_config(config: Config, errors: list) -> None:
    """Validate cache configuration."""
    # L1 TTL must be less than L2 TTL for proper cache hierarchy
    if config.cache.l1_ttl >= config.cache.l2_ttl:
        errors.append(
            f"L1 TTL ({config.cache.l1_ttl}s) must be < L2 TTL ({config.cache.l2_ttl}s) "
            "for proper cache hierarchy"
        )

    # TTL values must be positive
    if config.cache.l1_ttl <= 0:
        errors.append(f"L1 TTL must be positive, got {config.cache.l1_ttl}")

    if config.cache.l2_ttl <= 0:
        errors.append(f"L2 TTL must be positive, got {config.cache.l2_ttl}")

    # Warn about Alpha Vantage key (not an error, but worth noting)
    if not config.api.alpha_vantage_key:
        logger.warning(
            "ALPHA_VANTAGE_KEY not set - historical data features may be limited"
        )
