"""
Initialize position tracking tables in the database.

Run this once to add position tracking features to your existing database.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.infrastructure.database.positions_schema import add_positions_tables, verify_positions_tables
from src.config.config import get_config


def main():
    """Initialize position tracking tables."""
    config = get_config()

    print("Initializing position tracking tables...")
    add_positions_tables(config.database.path)

    print("Verifying tables...")
    if verify_positions_tables(config.database.path):
        print("✓ Position tracking tables initialized successfully!")
        print("\nYou can now use:")
        print("  ./trade.sh positions         - View open positions")
        print("  ./trade.sh performance       - View analytics")
        print("  python scripts/add_position.py --help    - Add position after manual execution")
        print("  python scripts/close_position.py --help  - Close position after manual exit")
    else:
        print("✗ Table verification failed")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
