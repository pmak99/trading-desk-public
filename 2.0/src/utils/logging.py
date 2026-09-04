"""
Logging configuration for IV Crush 2.0.

Sets up structured logging with correlation IDs and optional JSON output.

Formats:
- TEXT: Human-readable format for development/debugging
- JSON: Machine-readable format for log aggregation (ELK, Datadog, etc.)

Usage:
    # Text logging (default)
    setup_logging(level="INFO")

    # JSON logging for production
    setup_logging(level="INFO", json_format=True)

    # JSON logging to file
    setup_logging(level="INFO", log_file=Path("logs/app.json"), json_format=True)
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

class _CorrelationIdFilter(logging.Filter):
    """Add a placeholder correlation_id to log records for format string compatibility."""

    def filter(self, record):
        if not hasattr(record, "correlation_id"):
            record.correlation_id = "-"
        return True


class JSONFormatter(logging.Formatter):
    """
    JSON log formatter for structured logging.

    Outputs logs in JSON format suitable for log aggregation systems
    like ELK Stack, Datadog, Splunk, or CloudWatch.

    Output format:
    {
        "timestamp": "2025-01-15T10:30:00.123Z",
        "level": "INFO",
        "logger": "src.application.services.analyzer",
        "message": "Analyzing AAPL...",
        "correlation_id": "abc-123",
        "extra": {...}  // Any extra fields passed to logger
    }
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON string."""
        # Base log structure
        log_data: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add correlation ID if available
        if hasattr(record, "correlation_id"):
            log_data["correlation_id"] = record.correlation_id

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        # Add source location for debugging
        if record.levelno >= logging.WARNING:
            log_data["source"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            }

        # Add any extra fields from the log record
        # These are fields added via logger.info("msg", extra={...})
        standard_attrs = {
            "name", "msg", "args", "created", "filename", "funcName",
            "levelname", "levelno", "lineno", "module", "msecs",
            "pathname", "process", "processName", "relativeCreated",
            "stack_info", "exc_info", "exc_text", "thread", "threadName",
            "taskName", "correlation_id", "message",
        }

        extra = {}
        for key, value in record.__dict__.items():
            if key not in standard_attrs:
                try:
                    # Ensure value is JSON serializable
                    json.dumps(value)
                    extra[key] = value
                except (TypeError, ValueError):
                    extra[key] = str(value)

        if extra:
            log_data["extra"] = extra

        return json.dumps(log_data, default=str)


def setup_logging(
    level: str = "INFO",
    log_file: Optional[Path] = None,
    console_output: bool = True,
    log_format: Optional[str] = None,
    json_format: bool = False,
) -> None:
    """
    Configure application logging.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Optional file path for log output
        console_output: Whether to log to console
        log_format: Optional custom log format (ignored if json_format=True)
        json_format: If True, output logs in JSON format for log aggregation

    Features:
        - Correlation ID tracking for request tracing
        - JSON format for production log aggregation
        - Text format for development debugging
    """
    correlation_filter = _CorrelationIdFilter()

    # Create formatter based on format type
    if json_format:
        formatter = JSONFormatter()
    else:
        # Default text format with correlation ID
        if log_format is None:
            log_format = "%(asctime)s - [%(correlation_id)s] - %(name)s - %(levelname)s - %(message)s"
        formatter = logging.Formatter(log_format, datefmt="%Y-%m-%d %H:%M:%S")

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

    format_type = "JSON" if json_format else "TEXT"
    root_logger.info(f"Logging initialized: level={level}, format={format_type}")


def get_logger(name: str) -> logging.Logger:
    """
    Get logger for module.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)
