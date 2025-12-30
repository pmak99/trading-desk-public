"""
Structured JSON logging for 3.0 system.

Provides consistent, parseable logs for production monitoring.
"""

import json
import logging
import sys
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional
from contextlib import contextmanager
from functools import wraps

__all__ = [
    'StructuredLogger',
    'get_logger',
    'setup_logging',
    'log_context',
    'timed',
]


class JSONFormatter(logging.Formatter):
    """Format log records as JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }

        # Add exception info
        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)

        # Add extra fields
        if hasattr(record, 'extra_fields'):
            log_data.update(record.extra_fields)

        # Add correlation ID if present
        if hasattr(record, 'correlation_id'):
            log_data['correlation_id'] = record.correlation_id

        return json.dumps(log_data)


class StructuredLogger:
    """
    Logger with structured JSON output and context support.

    Example:
        logger = StructuredLogger('scanner')
        logger.info("Scanning ticker", ticker="AAPL", vrp=3.5)

        with logger.context(correlation_id="abc-123"):
            logger.info("Processing")  # Includes correlation_id
    """

    def __init__(self, name: str, level: int = logging.INFO):
        self._logger = logging.getLogger(name)
        self._logger.setLevel(level)
        self._context: Dict[str, Any] = {}

    def _log(self, level: int, message: str, **kwargs) -> None:
        """Log with extra fields."""
        extra = {'extra_fields': {**self._context, **kwargs}}
        if 'correlation_id' in self._context:
            extra['correlation_id'] = self._context['correlation_id']
        self._logger.log(level, message, extra=extra)

    def debug(self, message: str, **kwargs) -> None:
        self._log(logging.DEBUG, message, **kwargs)

    def info(self, message: str, **kwargs) -> None:
        self._log(logging.INFO, message, **kwargs)

    def warning(self, message: str, **kwargs) -> None:
        self._log(logging.WARNING, message, **kwargs)

    def error(self, message: str, **kwargs) -> None:
        self._log(logging.ERROR, message, **kwargs)

    def critical(self, message: str, **kwargs) -> None:
        self._log(logging.CRITICAL, message, **kwargs)

    @contextmanager
    def context(self, **kwargs):
        """Context manager to add fields to all logs within scope."""
        old_context = self._context.copy()
        self._context.update(kwargs)
        try:
            yield
        finally:
            self._context = old_context

    def set_correlation_id(self, correlation_id: Optional[str] = None) -> str:
        """Set correlation ID for request tracing."""
        if correlation_id is None:
            correlation_id = str(uuid.uuid4())[:8]
        self._context['correlation_id'] = correlation_id
        return correlation_id


def get_logger(name: str, level: int = logging.INFO) -> StructuredLogger:
    """Get a structured logger instance."""
    return StructuredLogger(name, level)


def setup_logging(
    level: str = "INFO",
    json_format: bool = True,
    log_file: Optional[str] = None
) -> None:
    """
    Configure logging for the application.

    Args:
        level: Log level (DEBUG, INFO, WARNING, ERROR)
        json_format: Use JSON formatting (for production)
        log_file: Optional file to write logs to
    """
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Create formatter
    if json_format:
        formatter = JSONFormatter()
    else:
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

    # Configure root logger
    root = logging.getLogger()
    root.setLevel(log_level)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # File handler (optional)
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


@contextmanager
def log_context(logger: StructuredLogger, **kwargs):
    """Context manager for adding fields to logs."""
    with logger.context(**kwargs):
        yield


def timed(logger: Optional[StructuredLogger] = None, operation: Optional[str] = None):
    """
    Decorator to log function execution time.

    Example:
        @timed(logger, "fetch_options")
        def fetch_options(ticker):
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            op_name = operation or func.__name__
            start = time.time()

            try:
                result = func(*args, **kwargs)
                duration = time.time() - start

                if logger:
                    logger.info(
                        f"Completed {op_name}",
                        operation=op_name,
                        duration_ms=round(duration * 1000, 2),
                        status="success"
                    )

                return result

            except Exception as e:
                duration = time.time() - start

                if logger:
                    logger.error(
                        f"Failed {op_name}",
                        operation=op_name,
                        duration_ms=round(duration * 1000, 2),
                        status="error",
                        error=str(e)
                    )

                raise

        return wrapper
    return decorator


class RequestLogger:
    """Log API request/response pairs."""

    def __init__(self, logger: StructuredLogger):
        self.logger = logger

    def log_request(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> str:
        """Log an outgoing request. Returns request ID."""
        request_id = str(uuid.uuid4())[:8]
        self.logger.info(
            "API request",
            request_id=request_id,
            method=method,
            url=url,
            **kwargs
        )
        return request_id

    def log_response(
        self,
        request_id: str,
        status_code: int,
        duration_ms: float,
        **kwargs
    ) -> None:
        """Log a response."""
        self.logger.info(
            "API response",
            request_id=request_id,
            status_code=status_code,
            duration_ms=duration_ms,
            **kwargs
        )

    def log_error(
        self,
        request_id: str,
        error: str,
        duration_ms: float,
        **kwargs
    ) -> None:
        """Log a request error."""
        self.logger.error(
            "API error",
            request_id=request_id,
            error=error,
            duration_ms=duration_ms,
            **kwargs
        )
