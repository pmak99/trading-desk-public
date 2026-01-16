#!/usr/bin/env python
"""CLI wrapper for /whisper command.

Discovers most anticipated earnings with parallel analysis.

Usage:
    python -m src.cli.whisper              # Next 5 days
    python -m src.cli.whisper 2026-02-05   # Specific start date
"""

import sys
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.orchestrators.whisper import WhisperOrchestrator


async def main():
    """Execute whisper orchestration."""
    # Parse arguments
    start_date = None
    end_date = None

    if len(sys.argv) > 1:
        start_date = sys.argv[1]
        # If start date provided, calculate end date (4 days ahead)
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = start_dt + timedelta(days=4)
        end_date = end_dt.strftime('%Y-%m-%d')

    # Print header
    print("=" * 60)
    print("WHISPER: Most Anticipated Earnings")
    print("=" * 60)
    print()

    if start_date:
        print(f"Date range: {start_date} to {end_date}")
    else:
        print("Date range: Next 5 days")
    print()

    # Create orchestrator
    orchestrator = WhisperOrchestrator()

    # Execute
    try:
        result = await orchestrator.orchestrate(
            start_date=start_date,
            end_date=end_date,
            limit=10
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
