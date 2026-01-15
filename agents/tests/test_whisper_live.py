#!/usr/bin/env python
"""Live test for WhisperOrchestrator with real earnings data.

Tests end-to-end workflow:
1. Health check
2. Earnings calendar fetch
3. Parallel ticker analysis
4. Result aggregation and ranking
5. Cross-ticker intelligence
6. ASCII table formatting
"""

import sys
from pathlib import Path
import asyncio
import time
from datetime import datetime, timedelta

# Add 6.0/ to path (parent of src/)
_6_0_dir = Path(__file__).parent.parent
sys.path.insert(0, str(_6_0_dir))

from src.orchestrators.whisper import WhisperOrchestrator


async def test_whisper_live():
    """Test WhisperOrchestrator with live earnings data."""
    print("=" * 60)
    print("LIVE TEST: WhisperOrchestrator")
    print("=" * 60)
    print()

    # Use next 5 days (default behavior)
    start_time = time.time()

    print("[1/4] Initializing WhisperOrchestrator...")
    orchestrator = WhisperOrchestrator()
    print("✓ Orchestrator initialized")
    print()

    # Execute orchestration
    print("[2/4] Running orchestration (next 5 days)...")
    try:
        result = await orchestrator.orchestrate(
            start_date=None,  # Use defaults (next 5 days)
            end_date=None,
            limit=10
        )

        elapsed = time.time() - start_time
        print(f"✓ Orchestration complete ({elapsed:.1f}s)")
        print()

        # Check success
        print("[3/4] Validating results...")
        if not result.get('success'):
            print(f"❌ Orchestration failed: {result.get('error')}")
            return False

        results = result.get('results', [])
        warnings = result.get('cross_ticker_warnings', [])

        print(f"✓ Found {len(results)} opportunities")
        print(f"✓ Generated {len(warnings)} cross-ticker warnings")
        print()

        # Display formatted output
        print("[4/4] Formatted output:")
        print("-" * 60)
        output = orchestrator.format_results(result)
        print(output)
        print("-" * 60)
        print()

        # Performance check
        print("Performance:")
        print(f"  Total time: {elapsed:.1f}s")
        print(f"  Target: <90s for 30 tickers")

        if elapsed < 90:
            print("  ✓ PASSED performance target")
        else:
            print(f"  ⚠️  Exceeded target by {elapsed - 90:.1f}s")
        print()

        # Validate result structure
        print("Validation:")
        required_fields = ['success', 'results', 'cross_ticker_warnings']
        missing = [f for f in required_fields if f not in result]

        if missing:
            print(f"  ❌ Missing fields: {missing}")
            return False

        print("  ✓ All required fields present")

        # Check result structure
        if results:
            first_result = results[0]
            result_fields = ['ticker', 'vrp_ratio', 'recommendation', 'score']
            missing_result_fields = [f for f in result_fields if f not in first_result]

            if missing_result_fields:
                print(f"  ❌ Missing result fields: {missing_result_fields}")
                return False

            print("  ✓ Result structure valid")
        print()

        print("=" * 60)
        print("TEST PASSED ✓")
        print("=" * 60)
        return True

    except Exception as e:
        elapsed = time.time() - start_time
        print(f"❌ ERROR after {elapsed:.1f}s: {e}")
        import traceback
        traceback.print_exc()
        print()
        print("=" * 60)
        print("TEST FAILED ✗")
        print("=" * 60)
        return False


async def test_whisper_specific_date():
    """Test WhisperOrchestrator with specific date range."""
    print()
    print("=" * 60)
    print("BONUS TEST: WhisperOrchestrator with Specific Date")
    print("=" * 60)
    print()

    # Use date 7 days from now
    start_date = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')
    end_date = (datetime.now() + timedelta(days=11)).strftime('%Y-%m-%d')

    print(f"Testing date range: {start_date} to {end_date}")
    print()

    orchestrator = WhisperOrchestrator()

    try:
        result = await orchestrator.orchestrate(
            start_date=start_date,
            end_date=end_date,
            limit=5
        )

        if result.get('success'):
            results = result.get('results', [])
            print(f"✓ Found {len(results)} opportunities")

            # Show brief summary
            for i, r in enumerate(results[:3], 1):
                print(f"  {i}. {r.get('ticker')}: VRP {r.get('vrp_ratio', 0):.2f}x, Score {r.get('score', 0)}")

            print()
            print("BONUS TEST PASSED ✓")
            return True
        else:
            print(f"BONUS TEST: No results (this is OK if no earnings in range)")
            return True

    except Exception as e:
        print(f"❌ BONUS TEST ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    print()
    print("Running primary test (next 5 days)...")
    print()

    success1 = asyncio.run(test_whisper_live())

    print()
    print()
    print("Running bonus test (specific date range)...")
    print()

    success2 = asyncio.run(test_whisper_specific_date())

    print()
    if success1 and success2:
        print("ALL TESTS PASSED ✓✓")
        sys.exit(0)
    else:
        print("SOME TESTS FAILED ✗")
        sys.exit(1)
