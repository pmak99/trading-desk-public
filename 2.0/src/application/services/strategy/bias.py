"""Directional bias read from the skew, for display.

It chooses no spread, strike or score: skew measures hedge demand, not
conviction (CLAUDE.md §6). Until 2026-09-14 select_strategy_types built only
the bias side under a STRONG bias, and a MODERATE one outside EXCELLENT; the
generator now builds both spreads for every ticker (generator.OFFERED_SPREADS).
"""
from typing import Optional

from src.domain.enums import DirectionalBias
from src.domain.types import SkewResult


def determine_bias(skew: Optional[SkewResult]) -> DirectionalBias:
    """
    Determine directional bias from IV skew with 7-level strength scale.

    Args:
        skew: Skew analysis (optional)

    Returns:
        DirectionalBias enum (7 levels)
    """
    if not skew:
        return DirectionalBias.NEUTRAL

    # Handle SkewAnalysis with DirectionalBias enum (new format)
    if hasattr(skew, 'directional_bias'):
        # Already an enum, return directly
        if isinstance(skew.directional_bias, DirectionalBias):
            return skew.directional_bias

        # Legacy string support (backward compatibility)
        bias_str = skew.directional_bias
        bias_map = {
            'strong_bearish': DirectionalBias.STRONG_BEARISH,
            'bearish': DirectionalBias.BEARISH,
            'weak_bearish': DirectionalBias.WEAK_BEARISH,
            'neutral': DirectionalBias.NEUTRAL,
            'weak_bullish': DirectionalBias.WEAK_BULLISH,
            'bullish': DirectionalBias.BULLISH,
            'strong_bullish': DirectionalBias.STRONG_BULLISH,
            'put_bias': DirectionalBias.BEARISH,
            'call_bias': DirectionalBias.BULLISH,
        }
        return bias_map.get(bias_str, DirectionalBias.NEUTRAL)

    # Fallback to old SkewResult format
    elif hasattr(skew, 'direction'):
        if skew.direction == 'bearish':
            return DirectionalBias.BEARISH
        elif skew.direction == 'bullish':
            return DirectionalBias.BULLISH
        else:
            return DirectionalBias.NEUTRAL

    return DirectionalBias.NEUTRAL
