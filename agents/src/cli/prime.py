#!/usr/bin/env python
"""CLI wrapper for /prime command.

Pre-caches sentiment for upcoming earnings to enable fast /whisper lookups.

Usage:
    python -m src.cli.prime              # Next 5 days
    python -m src.cli.prime 2026-02-05   # Specific start date
"""

import sys
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

# Import 6.0 modules
# Note: Namespace collision with 2.0/src is handled by deferring 2.0 imports
# until inside Container2_0 class (after sys.path is properly configured)
from src.orchestrators.prime import PrimeOrchestrator


async def main():
    """Execute prime orchestration."""
    # Parse arguments
    start_date = None
    end_date = None
    days_ahead = 5

    if len(sys.argv) > 1:
        start_date = sys.argv[1]
        # If start date provided, calculate end date
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = start_dt + timedelta(days=days_ahead)
        end_date = end_dt.strftime('%Y-%m-%d')

    # Print header
    print("=" * 60)
    print("PRIME: Pre-cache Sentiment for Upcoming Earnings")
    print("=" * 60)
    print()

    if start_date:
        print(f"Date range: {start_date} to {end_date}")
    else:
        print(f"Date range: Next {days_ahead} days")
    print()

    # Create orchestrator
    orchestrator = PrimeOrchestrator()

    # Execute
    try:
        result = await orchestrator.orchestrate(
            start_date=start_date,
            end_date=end_date,
            days_ahead=days_ahead
        )

        # Format and print results
        output = orchestrator.format_results(result)
        print(output)

        # Return exit code
        if result.get('success'):
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    asyncio.run(main())
