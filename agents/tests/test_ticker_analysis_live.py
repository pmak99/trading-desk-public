#!/usr/bin/env python
"""Live test for TickerAnalysisAgent with real ticker data.

Tests Result type handling from 2.0's analyzer.
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta

# Add agents/ to path (parent of src/)
_6_0_dir = Path(__file__).parent.parent
sys.path.insert(0, str(_6_0_dir))

from src.agents.ticker_analysis import TickerAnalysisAgent


def test_ticker_analysis_live():
    """Test TickerAnalysisAgent with live ticker."""
    print("=" * 60)
    print("LIVE TEST: TickerAnalysisAgent")
    print("=" * 60)
    print()

    # Use a ticker with known upcoming earnings
    # For testing, we'll use a date in the near future
    ticker = "AAPL"
    earnings_date = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')

    print(f"Testing: {ticker}")
    print(f"Earnings Date: {earnings_date}")
    print()

    # Initialize agent
    print("[1/3] Initializing TickerAnalysisAgent...")
    agent = TickerAnalysisAgent()
    print("✓ Agent initialized")
    print()

    # Call analyze
    print("[2/3] Calling analyze()...")
    try:
        result = agent.analyze(
            ticker=ticker,
            earnings_date=earnings_date,
            generate_strategies=True
        )

        print("✓ Analysis complete")
        print()

        # Display result
        print("[3/3] Result:")
        print("-" * 60)

        if result.get('error'):
            print(f"ERROR: {result['error']}")
            return False

        print(f"Ticker: {result.get('ticker')}")
        print(f"VRP Ratio: {result.get('vrp_ratio')}")
        print(f"Recommendation: {result.get('recommendation')}")
        print(f"Liquidity Tier: {result.get('liquidity_tier')}")
        print(f"Score: {result.get('score')}")

        if result.get('strategies'):
            print(f"Strategies: {len(result['strategies'])} generated")

        print("-" * 60)
        print()

        # Validate result structure
        required_fields = ['ticker', 'vrp_ratio', 'recommendation', 'liquidity_tier', 'score']
        missing = [f for f in required_fields if f not in result]

        if missing:
            print(f"❌ Missing fields: {missing}")
            return False

        print("✓ All required fields present")
        print()

        # Test historical moves
        print("[BONUS] Testing get_historical_moves()...")
        historical = agent.get_historical_moves(ticker, limit=5)
        print(f"✓ Retrieved {len(historical)} historical moves")
        print()

        print("=" * 60)
        print("TEST PASSED ✓")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        print()
        print("=" * 60)
        print("TEST FAILED ✗")
        print("=" * 60)
        return False


def inspect_2_0_result():
    """Inspect what core actually returns to understand structure."""
    print("=" * 60)
    print("INSPECTING core RESULT STRUCTURE")
    print("=" * 60)
    print()

    from src.integration.container_2_0 import Container2_0

    ticker = "AAPL"
    earnings_date_str = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')
    expiration_str = (datetime.now() + timedelta(days=14)).strftime('%Y-%m-%d')

    # Convert to date objects (2.0 expects date objects)
    earnings_date = datetime.strptime(earnings_date_str, '%Y-%m-%d').date()
    expiration = datetime.strptime(expiration_str, '%Y-%m-%d').date()

    print(f"Calling core analyzer for {ticker}...")
    print()

    try:
        container = Container2_0()
        result = container.analyze_ticker(
            ticker=ticker,
            earnings_date=earnings_date,
            expiration=expiration,
            generate_strategies=True
        )

        print("Result type:", type(result))
        print()

        # Check if Result type
        # Note: 2.0's Result type uses `is_err` property, not `is_error()` method
        if hasattr(result, 'is_err'):
            print("✓ Result type detected")
            print(f"  is_err: {result.is_err}")

            if result.is_err:
                print(f"  error: {result.error}")
            else:
                print(f"  has value: {hasattr(result, 'value')}")
                if hasattr(result, 'value'):
                    value = result.value
                    print(f"  value type: {type(value)}")
                    print()
                    print("Value structure:")
                    if isinstance(value, dict):
                        for key in value.keys():
                            print(f"    {key}: {type(value[key])}")
                    else:
                        print(f"    Attributes: {dir(value)}")
        else:
            print("Direct dict/object (not Result type)")
            if isinstance(result, dict):
                print("Dict keys:")
                for key in result.keys():
                    print(f"  {key}: {type(result[key])}")
            else:
                print("Attributes:")
                attrs = [a for a in dir(result) if not a.startswith('_')]
                for attr in attrs:
                    print(f"  {attr}")

        print()
        print("=" * 60)

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    print()
    print("Running inspection first to understand core result structure...")
    print()
    inspect_2_0_result()
    print()
    print()
    print("Now running live test...")
    print()
    success = test_ticker_analysis_live()
    sys.exit(0 if success else 1)
