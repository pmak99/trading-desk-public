"""
Position tracking dashboard.

Shows current open positions and portfolio summary.
"""

import logging
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.config import get_config
from src.application.services.position_tracker import PositionTracker
from src.utils.dashboard import format_positions_dashboard

logger = logging.getLogger(__name__)


def main():
    """Show positions dashboard."""
    config = get_config()

    # Initialize tracker
    tracker = PositionTracker(config.database.path)

    # Get positions and summary
    positions = tracker.get_open_positions()
    summary = tracker.get_portfolio_summary()

    # Display dashboard
    dashboard = format_positions_dashboard(positions, summary)
    print(dashboard)


if __name__ == "__main__":
    main()
