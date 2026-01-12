#!/usr/bin/env python
"""CLI wrapper for /maintenance command.

System monitoring and data quality operations.

Usage:
    python -m src.cli.maintenance health    # Health check
"""

import sys
from pathlib import Path

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agents.health import HealthCheckAgent


def main():
    """Execute maintenance operations."""
    # Parse arguments
    task = sys.argv[1] if len(sys.argv) > 1 else 'health'

    # Print header
    print("=" * 60)
    print(f"MAINTENANCE: {task.upper()}")
    print("=" * 60)
    print()

    if task == 'health':
        run_health_check()
    else:
        print(f"Unknown maintenance task: {task}")
        print("Available tasks: health")
        sys.exit(1)


def run_health_check():
    """Run system health check."""
    try:
        agent = HealthCheckAgent()
        result = agent.check_health()

        # Print results
        print(f"Overall Status: {result['status'].upper()}")
        print()

        # APIs
        print("API Health:")
        for api_name, api_status in result['apis'].items():
            status = api_status['status']
            latency = api_status.get('latency_ms')
            error = api_status.get('error')

            status_symbol = '✅' if status == 'ok' else '❌'
            print(f"  {status_symbol} {api_name}: {status}")

            if latency:
                print(f"     Latency: {latency}ms")
            if error:
                print(f"     Error: {error}")

        print()

        # Database
        print("Database Health:")
        db_status = result['database']
        status = db_status['status']
        status_symbol = '✅' if status == 'ok' else '❌'
        print(f"  {status_symbol} Status: {status}")

        if db_status.get('size_mb'):
            print(f"  Size: {db_status['size_mb']} MB")
        if db_status.get('historical_moves'):
            print(f"  Historical moves: {db_status['historical_moves']}")
        if db_status.get('earnings_calendar'):
            print(f"  Earnings calendar: {db_status['earnings_calendar']}")

        if db_status.get('error'):
            print(f"  Error: {db_status['error']}")

        print()

        # Budget
        print("Budget Status:")
        budget = result['budget']
        print(f"  Daily: {budget['daily_calls']}/{budget['daily_limit']} calls")
        print(f"  Monthly: ${budget['monthly_cost']:.2f}/${budget['monthly_budget']:.2f}")

        daily_remaining = budget['daily_limit'] - budget['daily_calls']
        monthly_remaining = budget['monthly_budget'] - budget['monthly_cost']

        print(f"  Remaining: {daily_remaining} calls, ${monthly_remaining:.2f}")

        print()
        print("=" * 60)

        # Exit code based on status
        if result['status'] == 'healthy':
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
