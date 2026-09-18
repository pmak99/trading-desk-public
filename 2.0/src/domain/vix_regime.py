"""
VIX Regime Classification Utility.

Provides consistent VIX regime classification across the application.
Used by MarketConditionsAnalyzer (display and analysis_log) and the
analysis_log vix_regime CHECK constraint. No regime moves a VRP tier or a size.

Regime Definitions:
- very_low: VIX < 12 (complacency)
- low: VIX 12-15 (calm markets)
- normal: VIX 15-20 (typical conditions)
- normal_high: VIX 20-25 (slightly elevated)
- elevated: VIX 25-30 (heightened uncertainty)
- elevated_high: VIX 30-35 (significant fear)
- high: VIX 35-40 (high fear)
- extreme: VIX 40+ (panic/crisis)
"""

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class VixRegime:
    """VIX regime with threshold boundaries."""
    name: str
    lower_bound: float  # inclusive
    upper_bound: float  # exclusive (except for extreme)


# Ordered list of VIX regimes with thresholds. Until 2026-09-14 each also carried
# an unread `description` ("High fear - reduce exposure", "avoid new positions")
# that read as advice no path applies.
VIX_REGIMES: Tuple[VixRegime, ...] = (
    VixRegime("very_low", 0, 12),
    VixRegime("low", 12, 15),
    VixRegime("normal", 15, 20),
    VixRegime("normal_high", 20, 25),
    VixRegime("elevated", 25, 30),
    VixRegime("elevated_high", 30, 35),
    VixRegime("high", 35, 40),
    VixRegime("extreme", 40, float('inf')),
)


def classify_vix_regime(vix_level: float) -> str:
    """
    Classify VIX level into regime category.

    This is the canonical implementation used throughout the application
    to ensure consistent regime classification.

    Args:
        vix_level: Current VIX level (must be non-negative)

    Returns:
        Regime name string

    Raises:
        ValueError: If vix_level is negative

    Examples:
        >>> classify_vix_regime(15.5)
        'normal'
        >>> classify_vix_regime(28.0)
        'elevated'
        >>> classify_vix_regime(45.0)
        'extreme'
    """
    if vix_level < 0:
        raise ValueError(f"VIX level cannot be negative: {vix_level}")

    for regime in VIX_REGIMES:
        if regime.lower_bound <= vix_level < regime.upper_bound:
            return regime.name

    # Should never reach here, but return extreme as fallback
    return "extreme"

