"""
Structured JSON logging for IV Crush 5.0.

All logs are JSON for Cloud Logging and Grafana.
"""

import json
import uuid
from datetime import datetime, timezone
from contextvars import ContextVar
from typing import Any

from .config import now_et

_request_id: ContextVar[str] = ContextVar('request_id', default='')


def set_request_id(request_id: str = None) -> str:
    """Set request ID for current context."""
    if request_id is None:
        request_id = str(uuid.uuid4())[:8]
    _request_id.set(request_id)
    return request_id


def get_request_id() -> str:
    """Get request ID for current context."""
    rid = _request_id.get()
    if not rid:
        rid = str(uuid.uuid4())[:8]
        _request_id.set(rid)
    return rid


def log(level: str, message: str, **context: Any):
    """
    Log a structured JSON message.

    Args:
        level: Log level (debug, info, warn, error)
        message: Human-readable message
        **context: Additional key-value pairs
    """
    # Filter out secrets
    safe_context = {
        k: v for k, v in context.items()
        if v is not None
        and 'key' not in k.lower()
        and 'token' not in k.lower()
        and 'secret' not in k.lower()
    }

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "timestamp_et": now_et().strftime("%Y-%m-%d %H:%M:%S ET"),
        "level": level.upper(),
        "request_id": get_request_id(),
        "message": message,
        **safe_context
    }

    print(json.dumps(entry))
