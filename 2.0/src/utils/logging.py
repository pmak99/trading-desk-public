"""
Logging configuration for IV Crush 2.0.

Sets up structured logging with correlation IDs (Phase 1 enhancement).
"""

import logging
import sys
from pathlib import Path
from typing import Optional

from src.utils.tracing import CorrelationIdFilter


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    console_output: bool = True,
    log_format: Optional[str] = None,
) -> None:
    """
    Configure application logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file path for log output
        console_output: Whether to log to console
        log_format: Optional custom log format

    Phase 1 Enhancement: Adds correlation ID filter for tracing
    """
    # Default format with correlation ID
    if log_format is None:
        log_format = "%(asctime)s - [%(correlation_id)s] - %(name)s - %(levelname)s - %(message)s"

    # Create formatter
    formatter = logging.Formatter(log_format, datefmt="%Y-%m-%d %H:%M:%S")

    # Create correlation ID filter
    correlation_filter = CorrelationIdFilter()

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper()))

    # Remove existing handlers
    root_logger.handlers.clear()

    # Console handler
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(correlation_filter)
        root_logger.addHandler(console_handler)

    # File handler
    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(correlation_filter)
        root_logger.addHandler(file_handler)

    # Reduce noise from third-party libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    root_logger.info(f"Logging initialized: level={level}")


def get_logger(name: str) -> logging.Logger:
    """
    Get logger for module.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)
