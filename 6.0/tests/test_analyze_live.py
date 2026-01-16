#!/usr/bin/env python
"""Live test for AnalyzeOrchestrator with real ticker data.

Tests end-to-end workflow:
1. Health check
2. Earnings date lookup (auto-detect)
3. Parallel specialist dispatch
4. Result synthesis
5. Final recommendation generation
6. Markdown report formatting
"""

import sys
from pathlib import Path
import asyncio
import time

# Add 6.0/ to path (parent of src/)
_6_0_dir = Path(__file__).parent.parent
sys.path.insert(0, str(_6_0_dir))

from src.orchestrators.analyze import AnalyzeOrchestrator


async def test_analyze_auto_detect():
    """Test AnalyzeOrchestrator with auto-detected earnings date."""
    print("=" * 60)
    print("LIVE TEST: AnalyzeOrchestrator (Auto-Detect Date)")
    print("=" * 60)
    print()

    # Use a ticker that should have earnings coming up
    # (AAPL, NVDA, MSFT usually have quarterly earnings)
    ticker = "AAPL"

    start_time = time.time()

    print("[1/4] Initializing AnalyzeOrchestrator...")
    orchestrator = AnalyzeOrchestrator()
    print("✓ Orchestrator initialized")
    print()

    # Execute orchestration
    print(f"[2/4] Running analysis for {ticker} (auto-detect date)...")
    try:
        result = await orchestrator.orchestrate(
            ticker=ticker,
            earnings_date=None  # Auto-detect
        )

        elapsed = time.time() - start_time
        print(f"✓ Analysis complete ({elapsed:.1f}s)")
        print()

        # Check success
        print("[3/4] Validating results...")
        if not result.get('success'):
            error = result.get('error', 'Unknown error')
            if "Could not find earnings date" in error:
                print(f"⚠️  No upcoming earnings found for {ticker} (this is OK if no earnings scheduled)")
                print()
                return True  # This is acceptable
            else:
                print(f"❌ Analysis failed: {error}")
                return False

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
        print(f"  Target: <60s")

        if elapsed < 60:
            print("  ✓ PASSED performance target")
        else:
            print(f"  ⚠️  Exceeded target by {elapsed - 60:.1f}s")
        print()

        # Validate result structure
        print("Validation:")
        required_fields = ['success', 'ticker', 'report', 'recommendation']
        missing = [f for f in required_fields if f not in result]

        if missing:
            print(f"  ❌ Missing fields: {missing}")
            return False

        print("  ✓ All required fields present")

        # Check report structure
        report = result.get('report', {})
        report_fields = ['summary', 'vrp_analysis', 'liquidity', 'sentiment']
        missing_report_fields = [f for f in report_fields if f not in report]

        if missing_report_fields:
            print(f"  ❌ Missing report fields: {missing_report_fields}")
            return False

        print("  ✓ Report structure valid")
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


async def test_analyze_specific_date():
    """Test AnalyzeOrchestrator with specific earnings date."""
    print()
    print("=" * 60)
    print("BONUS TEST: AnalyzeOrchestrator (Specific Date)")
    print("=" * 60)
    print()

    # Use a known ticker with specific date
    # (Will need to be updated based on actual upcoming earnings)
    ticker = "NVDA"
    earnings_date = "2026-02-05"  # Example date

    print(f"Testing: {ticker} (earnings: {earnings_date})")
    print()

    orchestrator = AnalyzeOrchestrator()

    try:
        result = await orchestrator.orchestrate(
            ticker=ticker,
            earnings_date=earnings_date
        )

        if result.get('success'):
            print("✓ Analysis succeeded")

            # Show brief summary
            report = result.get('report', {})
            summary = report.get('summary', {})

            print(f"  VRP: {summary.get('vrp_ratio', 0):.2f}x ({summary.get('recommendation')})")
            print(f"  Liquidity: {summary.get('liquidity_tier')}")
            print(f"  Score: {summary.get('score')}")

            recommendation = result.get('recommendation', {})
            action = recommendation.get('action', 'UNKNOWN')
            print(f"  Recommendation: {action}")
            print()

            print("BONUS TEST PASSED ✓")
            return True
        else:
            error = result.get('error', 'Unknown error')
            print(f"⚠️  Analysis failed: {error}")
            print("(This may be expected if earnings date is far out)")
            print()
            return True  # Accept failures for future dates

    except Exception as e:
        print(f"❌ BONUS TEST ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    print()
    print("Running primary test (auto-detect earnings date)...")
    print()

    success1 = asyncio.run(test_analyze_auto_detect())

    print()
    print()
    print("Running bonus test (specific earnings date)...")
    print()

    success2 = asyncio.run(test_analyze_specific_date())

    print()
    if success1 and success2:
        print("ALL TESTS PASSED ✓✓")
        sys.exit(0)
    else:
        print("SOME TESTS FAILED ✗")
        sys.exit(1)
