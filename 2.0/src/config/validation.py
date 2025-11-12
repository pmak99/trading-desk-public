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

    if errors:
        for error in errors:
            logger.error(f"Configuration error: {error}")
        raise ConfigurationError(
            f"{len(errors)} configuration error(s). See logs for details."
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
