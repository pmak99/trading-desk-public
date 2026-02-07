"""
Weekly options detection filter.

Canonical implementation lives in common/filters/weekly_options.py.
This module re-exports for backward compatibility.
"""

import sys
from pathlib import Path

# Ensure common/ is importable
_root = str(Path(__file__).resolve().parent.parent.parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from common.filters.weekly_options import (  # noqa: E402
    has_weekly_options,
    WEEKLY_DETECTION_WINDOW_DAYS,
    WEEKLY_DETECTION_MIN_FRIDAYS,
)

__all__ = [
    "has_weekly_options",
    "WEEKLY_DETECTION_WINDOW_DAYS",
    "WEEKLY_DETECTION_MIN_FRIDAYS",
]
