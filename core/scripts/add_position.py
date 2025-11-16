"""
Add a position to tracking.

Used after manually executing a trade on Fidelity.
"""

import logging
import sys
import argparse
from pathlib import Path
from datetime import date, datetime
from decimal import Decimal

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.config import get_config
from src.application.services.position_tracker import PositionTracker, Position

logger = logging.getLogger(__name__)


def parse_date(date_str: str) -> date:
    """Parse date string in YYYY-MM-DD format."""
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def main():
    """Add position to tracking."""
    parser = argparse.ArgumentParser(description="Add Position to Tracking")

    # Required arguments
    parser.add_argument("ticker", help="Stock ticker symbol")
    parser.add_argument("entry_date", help="Entry date (YYYY-MM-DD)")
    parser.add_argument("earnings_date", help="Earnings date (YYYY-MM-DD)")
    parser.add_argument("expiration_date", help="Expiration date (YYYY-MM-DD)")
    parser.add_argument("credit", type=float, help="Credit received ($)")
    parser.add_argument("max_loss", type=float, help="Maximum loss ($)")

    # Strategy info
    parser.add_argument(
        "--strategy",
        default="STRADDLE",
        choices=["STRADDLE", "STRANGLE", "IRON_CONDOR", "BULL_PUT_SPREAD",
                 "BEAR_CALL_SPREAD", "IRON_BUTTERFLY", "OTHER"],
        help="Strategy type"
    )
    parser.add_argument("--contracts", type=int, default=1, help="Number of contracts")

    # Thesis metrics
    parser.add_argument("--vrp", type=float, required=True, help="VRP ratio")
    parser.add_argument("--implied-move", type=float, required=True, help="Implied move %%")
    parser.add_argument("--historical-move", type=float, required=True, help="Historical avg move %%")
    parser.add_argument("--edge-score", type=float, help="Edge score")

    # Position sizing
    parser.add_argument("--position-size", type=float, default=5.0, help="Position size %% of account")
    parser.add_argument("--kelly-fraction", type=float, help="Kelly fraction used")

    # Risk parameters
    parser.add_argument("--stop-loss", type=float, help="Stop loss amount ($)")
    parser.add_argument("--target-profit", type=float, help="Target profit amount ($)")

    # Metadata
    parser.add_argument("--notes", help="Entry notes")
    parser.add_argument("--sector", help="Stock sector")

    args = parser.parse_args()
    config = get_config()

    # Parse dates
    entry_date = parse_date(args.entry_date)
    earnings_date = parse_date(args.earnings_date)
    expiration_date = parse_date(args.expiration_date)

    # Validate dates
    if earnings_date < entry_date:
        print(f"Error: Earnings date must be >= entry date")
        sys.exit(1)

    if expiration_date < earnings_date:
        print(f"Error: Expiration date must be >= earnings date")
        sys.exit(1)

    # Validate numerical inputs
    if args.credit <= 0:
        print(f"Error: Credit must be positive (got ${args.credit})")
        sys.exit(1)

    if args.max_loss <= 0:
        print(f"Error: Max loss must be positive (got ${args.max_loss})")
        sys.exit(1)

    if args.vrp <= 0:
        print(f"Error: VRP ratio must be positive (got {args.vrp})")
        sys.exit(1)

    if args.implied_move < 0 or args.implied_move > 100:
        print(f"Error: Implied move must be 0-100% (got {args.implied_move}%)")
        sys.exit(1)

    if args.historical_move < 0 or args.historical_move > 100:
        print(f"Error: Historical move must be 0-100% (got {args.historical_move}%)")
        sys.exit(1)

    if args.position_size <= 0 or args.position_size > 100:
        print(f"Error: Position size must be 0-100% (got {args.position_size}%)")
        sys.exit(1)

    # Sanity check: credit vs max loss
    if args.credit > args.max_loss * 2:
        print(f"Warning: Credit (${args.credit}) seems high vs max loss (${args.max_loss})")
        print(f"Typically credit ≈ 50% of max loss for spreads")
        print("Continue anyway? (y/n): ", end='')
        if input().lower() != 'y':
            print("Cancelled")
            sys.exit(0)

    # Create position
    position = Position(
        id=None,
        ticker=args.ticker.upper(),
        entry_date=entry_date,
        earnings_date=earnings_date,
        expiration_date=expiration_date,
        strategy_type=args.strategy,
        num_contracts=args.contracts,
        credit_received=Decimal(str(args.credit)),
        max_loss=Decimal(str(args.max_loss)),
        vrp_ratio=Decimal(str(args.vrp)),
        implied_move_pct=Decimal(str(args.implied_move)),
        historical_avg_move_pct=Decimal(str(args.historical_move)),
        edge_score=Decimal(str(args.edge_score)) if args.edge_score else None,
        position_size_pct=Decimal(str(args.position_size)),
        kelly_fraction=Decimal(str(args.kelly_fraction)) if args.kelly_fraction else None,
        stop_loss_amount=Decimal(str(args.stop_loss)) if args.stop_loss else None,
        target_profit_amount=Decimal(str(args.target_profit)) if args.target_profit else None,
        status="OPEN",
        entry_notes=args.notes,
        sector=args.sector,
    )

    # Add to tracker
    tracker = PositionTracker(config.database.path)

    try:
        position_id = tracker.add_position(position)
        print(f"\n✓ Position added successfully!")
        print(f"  Position ID: {position_id}")
        print(f"  Ticker:      {args.ticker}")
        print(f"  Entry:       {entry_date}")
        print(f"  Credit:      ${args.credit:,.0f}")
        print(f"  Max Loss:    ${args.max_loss:,.0f}")
        print(f"  VRP Ratio:   {args.vrp:.2f}x")
        print(f"\nMonitor this position with: ./trade.sh positions\n")

    except Exception as e:
        print(f"\nError adding position: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
