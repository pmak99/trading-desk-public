#!/usr/bin/env python
"""CLI wrapper for /analyze command.

Performs comprehensive single ticker deep dive with multi-specialist analysis.

Usage:
    python -m src.cli.analyze TICKER              # Auto-detect earnings date
    python -m src.cli.analyze TICKER 2026-02-05   # Specific earnings date
"""

import sys
import asyncio
from pathlib import Path

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.orchestrators.analyze import AnalyzeOrchestrator


async def main():
    """Execute analyze orchestration."""
    # Parse arguments
    if len(sys.argv) < 2:
        print("Error: TICKER required")
        print("Usage: python -m src.cli.analyze TICKER [DATE]")
        sys.exit(1)

    ticker = sys.argv[1].upper()
    earnings_date = sys.argv[2] if len(sys.argv) > 2 else None

    # Print header
    print("=" * 60)
    print(f"ANALYZE: {ticker}")
    print("=" * 60)
    print()

    if earnings_date:
        print(f"Earnings date: {earnings_date}")
    else:
        print("Earnings date: Auto-detect")
    print()

    # Create orchestrator
    orchestrator = AnalyzeOrchestrator()

    # Execute
    try:
        result = await orchestrator.orchestrate(
            ticker=ticker,
            earnings_date=earnings_date
        )

        # Format and print results
        output = orchestrator.format_results(result)
        print(output)

        # Return exit code
        if result.get('success'):
            recommendation = result.get('recommendation', {})
            action = recommendation.get('action', 'SKIP')

            # Exit 0 for TRADE, 1 for others
            if action == 'TRADE':
                sys.exit(0)
            else:
                sys.exit(1)
        else:
            sys.exit(1)

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
