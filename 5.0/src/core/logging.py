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


# Secret patterns to filter from logs. Uses suffix matching to catch secrets
# while allowing legitimate fields like 'api_key_valid', 'token_count'.
# The pattern checks if the key ENDS with these suffixes.
SECRET_SUFFIXES = ['_key', 'api_key', '_token', 'token', '_secret', '_password']
SECRET_EXACT = ['authorization', 'credentials', 'password', 'secret']


def log(level: str, message: str, **context: Any):
    """
    Log a structured JSON message.

    Args:
        level: Log level (debug, info, warn, error)
        message: Human-readable message
        **context: Additional key-value pairs
    """
    # Filter out secrets using suffix and exact matching
    def is_secret_key(key: str) -> bool:
        k = key.lower()
        # Check suffix matches (e.g., api_key, bot_token)
        if any(k.endswith(suffix) for suffix in SECRET_SUFFIXES):
            return True
        # Check exact matches
        if k in SECRET_EXACT:
            return True
        return False

    safe_context = {
        k: v for k, v in context.items()
        if v is not None and not is_secret_key(k)
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
