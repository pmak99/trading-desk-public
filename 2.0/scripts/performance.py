"""
Performance analytics dashboard.

Shows learning loops and insights from closed trades.
"""

import logging
import sys
import argparse
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.config import get_config
from src.application.services.performance_analytics import PerformanceAnalytics
from src.utils.dashboard import format_performance_report

logger = logging.getLogger(__name__)


def main():
    """Show performance analytics."""
    parser = argparse.ArgumentParser(description="Performance Analytics Dashboard")
    parser.add_argument(
        "--days",
        type=int,
        help="Number of days to analyze (default: all time)"
    )

    args = parser.parse_args()
    config = get_config()

    # Initialize analytics
    analytics = PerformanceAnalytics(config.database.path)

    # Generate report
    report = analytics.generate_report(lookback_days=args.days)

    # Display report
    formatted = format_performance_report(report)
    print(formatted)


if __name__ == "__main__":
    main()
