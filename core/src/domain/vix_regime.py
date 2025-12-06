"""
VIX Regime Classification Utility.

Provides consistent VIX regime classification across the application.
Used by MarketConditionsAnalyzer and AdaptiveThresholdCalculator.

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
    description: str


# Ordered list of VIX regimes with thresholds
VIX_REGIMES: Tuple[VixRegime, ...] = (
    VixRegime("very_low", 0, 12, "Complacency - markets extremely calm"),
    VixRegime("low", 12, 15, "Low volatility - calm markets"),
    VixRegime("normal", 15, 20, "Normal conditions - typical volatility"),
    VixRegime("normal_high", 20, 25, "Slightly elevated - some uncertainty"),
    VixRegime("elevated", 25, 30, "Elevated - heightened uncertainty"),
    VixRegime("elevated_high", 30, 35, "Significant fear - caution advised"),
    VixRegime("high", 35, 40, "High fear - reduce exposure"),
    VixRegime("extreme", 40, float('inf'), "Panic/crisis - avoid new positions"),
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


def get_regime_info(regime_name: str) -> VixRegime:
    """
    Get full regime information by name.

    Args:
        regime_name: Name of the regime

    Returns:
        VixRegime with full details

    Raises:
        ValueError: If regime_name is not recognized
    """
    for regime in VIX_REGIMES:
        if regime.name == regime_name:
            return regime
    raise ValueError(f"Unknown VIX regime: {regime_name}")


def is_trading_recommended(vix_level: float) -> bool:
    """
    Check if trading is recommended at current VIX level.

    Trading is NOT recommended in extreme volatility (VIX >= 40).

    Args:
        vix_level: Current VIX level

    Returns:
        True if trading recommended, False otherwise
    """
    regime = classify_vix_regime(vix_level)
    return regime != "extreme"
