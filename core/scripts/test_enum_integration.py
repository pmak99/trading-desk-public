#!/usr/bin/env python3
"""
Test script to verify enum integration in skew analysis and strategy generation.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.container import Container
from src.config.config import Config
from src.domain.enums import DirectionalBias


def test_enum_helpers():
    """Test the new enum helper methods."""
    print("=" * 70)
    print("TESTING ENUM HELPER METHODS")
    print("=" * 70)

    test_cases = [
        DirectionalBias.STRONG_BULLISH,
        DirectionalBias.BULLISH,
        DirectionalBias.WEAK_BULLISH,
        DirectionalBias.NEUTRAL,
        DirectionalBias.WEAK_BEARISH,
        DirectionalBias.BEARISH,
        DirectionalBias.STRONG_BEARISH,
    ]

    for bias in test_cases:
        print(f"\n{bias.value.upper().replace('_', ' ')}:")
        print(f"  is_bullish(): {bias.is_bullish()}")
        print(f"  is_bearish(): {bias.is_bearish()}")
        print(f"  is_neutral(): {bias.is_neutral()}")
        print(f"  strength(): {bias.strength()}")

    print("\n✅ Enum helper methods test complete!")


def test_skew_analysis():
    """Test skew analysis with real data."""
    print("\n" + "=" * 70)
    print("TESTING SKEW ANALYSIS WITH ENUM INTEGRATION")
    print("=" * 70)

    # Initialize container
    config = Config.from_env()
    container = Container(config)
    analyzer = container.skew_analyzer
    earnings_repo = container.earnings_repository

    # Test with AAPL
    ticker = 'AAPL'

    print(f"\nTesting skew analysis for {ticker}")
    print("-" * 70)

    # Get earnings events to find real expiration
    events_result = earnings_repo.get_upcoming_earnings(ticker, days_ahead=14)
    if events_result.is_ok and events_result.value:
        event = events_result.value[0]
        print(f"Found earnings: {event.timing.value} on {event.announcement_date}")

        # Analyze skew
        result = analyzer.analyze_skew_curve(ticker, event.expiration)
        if result.is_ok:
            analysis = result.value
            print(f"\n✅ Skew analysis successful!")
            print(f"   Stock price: ${analysis.stock_price.amount:.2f}")
            print(f"   Skew ATM: {analysis.skew_atm.value:.2f}%")
            print(f"   Directional bias: {analysis.directional_bias.value}")
            print(f"   Bias strength: {analysis.directional_bias.strength()}")
            print(f"   Bias confidence: {analysis.bias_confidence:.3f}")
            print(f"   R²: {analysis.confidence:.3f}")
            print(f"   Slope ATM: {analysis.slope_atm:.3f}")
            print(f"   Shape: {analysis.strength}")
            print(f"   Data points: {analysis.num_points}")

            # Test enum helper methods
            print(f"\n   Enum helpers:")
            print(f"   - is_bullish(): {analysis.directional_bias.is_bullish()}")
            print(f"   - is_bearish(): {analysis.directional_bias.is_bearish()}")
            print(f"   - is_neutral(): {analysis.directional_bias.is_neutral()}")

            # Verify type
            assert isinstance(analysis.directional_bias, DirectionalBias), \
                "directional_bias should be DirectionalBias enum instance"
            print(f"\n   ✅ Type check passed: directional_bias is DirectionalBias enum")
        else:
            print(f"❌ Skew analysis failed: {result.error}")
    else:
        print(f"No earnings found for {ticker}")


if __name__ == "__main__":
    test_enum_helpers()
    test_skew_analysis()

    print("\n" + "=" * 70)
    print("✅ ALL TESTS COMPLETE")
    print("=" * 70 + "\n")
