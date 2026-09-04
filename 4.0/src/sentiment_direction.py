"""
Sentiment-adjusted directional bias.

Canonical implementation lives in common/direction.py.
This module re-exports for backward compatibility with 4.0 imports.
"""

import sys
from pathlib import Path

# Ensure common/ is importable
_root = str(Path(__file__).resolve().parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from common.enums import AdjustedBias  # noqa: E402
from common.direction import (  # noqa: E402
    DirectionAdjustment,
    adjust_direction,
    normalize_skew_bias,
    get_size_modifier,
    get_direction,
    format_adjustment,
    quick_adjust,
    _calculate_confidence,
)
from common.constants import (  # noqa: E402
    SENTIMENT_BULLISH_THRESHOLD,
    SENTIMENT_BEARISH_THRESHOLD,
    CONFIDENCE_DIVISOR,
    STRONG_BULLISH_THRESHOLD,
    STRONG_BEARISH_THRESHOLD,
    SIZE_MODIFIER_BULLISH,
    SIZE_MODIFIER_BEARISH,
    HIGH_BULLISH_WARNING_THRESHOLD,
)

__all__ = [
    "AdjustedBias",
    "DirectionAdjustment",
    "adjust_direction",
    "normalize_skew_bias",
    "get_size_modifier",
    "get_direction",
    "format_adjustment",
    "quick_adjust",
    "_calculate_confidence",
    "SENTIMENT_BULLISH_THRESHOLD",
    "SENTIMENT_BEARISH_THRESHOLD",
    "CONFIDENCE_DIVISOR",
    "STRONG_BULLISH_THRESHOLD",
    "STRONG_BEARISH_THRESHOLD",
    "SIZE_MODIFIER_BULLISH",
    "SIZE_MODIFIER_BEARISH",
    "HIGH_BULLISH_WARNING_THRESHOLD",
]
