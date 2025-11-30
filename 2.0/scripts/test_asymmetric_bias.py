#!/usr/bin/env python3
"""
Test script to verify asymmetric strike placement based on directional bias.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.domain.enums import DirectionalBias, OptionType

# Mock the _get_asymmetric_deltas method logic
def test_asymmetric_deltas(
    option_type: OptionType,
    bias: DirectionalBias,
    base_short: float = 0.25,
    base_long: float = 0.20
) -> tuple[float, float]:
    """Test asymmetric delta calculation."""

    # Delta adjustments based on bias strength
    adjustment = 0.0

    if bias in {DirectionalBias.STRONG_BULLISH, DirectionalBias.STRONG_BEARISH}:
        adjustment = 0.10
    elif bias in {DirectionalBias.BULLISH, DirectionalBias.BEARISH}:
        adjustment = 0.05
    elif bias in {DirectionalBias.WEAK_BULLISH, DirectionalBias.WEAK_BEARISH}:
        adjustment = 0.02
    else:  # NEUTRAL
        adjustment = 0.0

    # Apply adjustment based on bias direction
    is_bullish = bias in {DirectionalBias.WEAK_BULLISH, DirectionalBias.BULLISH, DirectionalBias.STRONG_BULLISH}
    is_bearish = bias in {DirectionalBias.WEAK_BEARISH, DirectionalBias.BEARISH, DirectionalBias.STRONG_BEARISH}

    if option_type == OptionType.PUT:
        if is_bullish:
            delta_short = base_short - adjustment
            delta_long = base_long - adjustment
        elif is_bearish:
            delta_short = base_short + adjustment
            delta_long = base_long + adjustment
        else:
            delta_short = base_short
            delta_long = base_long
    else:  # CALL
        if is_bearish:
            delta_short = base_short - adjustment
            delta_long = base_long - adjustment
        elif is_bullish:
            delta_short = base_short + adjustment
            delta_long = base_long + adjustment
        else:
            delta_short = base_short
            delta_long = base_long

    # Clamp to reasonable ranges
    delta_short = max(0.10, min(0.40, delta_short))
    delta_long = max(0.10, min(0.40, delta_long))

    # Ensure long is always lower delta than short
    if delta_long >= delta_short:
        delta_long = delta_short - 0.05

    return delta_short, delta_long


def print_test_case(bias: DirectionalBias):
    """Print asymmetric delta adjustments for all scenarios."""
    print(f"\n{'='*70}")
    print(f"BIAS: {bias.value.upper().replace('_', ' ')}")
    print(f"{'='*70}")

    put_short, put_long = test_asymmetric_deltas(OptionType.PUT, bias)
    call_short, call_long = test_asymmetric_deltas(OptionType.CALL, bias)

    print(f"\n📊 PUT SPREAD (below price):")
    print(f"   Short Strike: {put_short:.2f}Δ (sell premium)")
    print(f"   Long Strike:  {put_long:.2f}Δ (protection)")

    print(f"\n📞 CALL SPREAD (above price):")
    print(f"   Short Strike: {call_short:.2f}Δ (sell premium)")
    print(f"   Long Strike:  {call_long:.2f}Δ (protection)")

    # Interpretation
    if bias in {DirectionalBias.WEAK_BULLISH, DirectionalBias.BULLISH, DirectionalBias.STRONG_BULLISH}:
        print(f"\n💡 BULLISH BIAS → Asymmetric positioning:")
        print(f"   • Put spread SAFER ({put_short:.2f}Δ vs 0.25Δ baseline)")
        print(f"   • Call spread RISKIER ({call_short:.2f}Δ vs 0.25Δ baseline)")
        print(f"   • Expecting upward bias in price movement")
    elif bias in {DirectionalBias.WEAK_BEARISH, DirectionalBias.BEARISH, DirectionalBias.STRONG_BEARISH}:
        print(f"\n💡 BEARISH BIAS → Asymmetric positioning:")
        print(f"   • Call spread SAFER ({call_short:.2f}Δ vs 0.25Δ baseline)")
        print(f"   • Put spread RISKIER ({put_short:.2f}Δ vs 0.25Δ baseline)")
        print(f"   • Expecting downward bias in price movement")
    else:
        print(f"\n💡 NEUTRAL BIAS → Balanced positioning:")
        print(f"   • Both spreads at baseline 0.25Δ / 0.20Δ")


if __name__ == "__main__":
    print("\n" + "="*70)
    print("ASYMMETRIC STRIKE PLACEMENT TEST")
    print("Testing directional bias integration")
    print("="*70)

    # Test all bias levels
    for bias in DirectionalBias:
        print_test_case(bias)

    print(f"\n{'='*70}")
    print("✅ Asymmetric delta calculation test complete!")
    print(f"{'='*70}\n")
