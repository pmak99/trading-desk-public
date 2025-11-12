"""Correlation ID tracing for request tracking across services."""

import uuid
import logging
from contextvars import ContextVar
from typing import Optional

# Context variable for storing correlation ID across async boundaries
correlation_id: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)


class CorrelationIdFilter(logging.Filter):
    """Logging filter that adds correlation ID to log records.

    This filter adds a 'correlation_id' attribute to each log record,
    which can be used in log formatters to include the correlation ID
    in log messages.
    """

    def filter(self, record):
        """Add correlation ID to log record.

        Args:
            record: Log record to modify

        Returns:
            True to allow record to be logged
        """
        cid = correlation_id.get() or "?"
        record.correlation_id = cid[:8]  # Use first 8 chars for brevity
        return True


def get_correlation_id() -> str:
    """Get current correlation ID, creating one if it doesn't exist.

    Returns:
        Current correlation ID as a UUID string
    """
    cid = correlation_id.get()
    if cid is None:
        cid = str(uuid.uuid4())
        correlation_id.set(cid)
    return cid


def set_correlation_id(cid: str):
    """Set correlation ID for current context.

    Args:
        cid: Correlation ID string to set
    """
    correlation_id.set(cid)
