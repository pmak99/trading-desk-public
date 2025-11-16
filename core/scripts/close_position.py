"""
Close a position and record outcome.

Used after manually closing a trade on Fidelity.
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
from src.application.services.position_tracker import PositionTracker

logger = logging.getLogger(__name__)


def parse_date(date_str: str) -> date:
    """Parse date string in YYYY-MM-DD format."""
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def main():
    """Close position."""
    parser = argparse.ArgumentParser(description="Close Position")

    # Two modes: by position ID or by ticker
    parser.add_argument("--id", type=int, help="Position ID")
    parser.add_argument("--ticker", help="Ticker (if multiple positions, will show list)")

    # Required close info
    parser.add_argument("--date", help="Close date (YYYY-MM-DD, default: today)")
    parser.add_argument("--close-price", type=float, required=True, help="Stock closing price")
    parser.add_argument("--actual-move", type=float, required=True, help="Actual stock move %%")
    parser.add_argument("--pnl", type=float, required=True, help="Final P&L ($)")

    # Optional
    parser.add_argument("--notes", help="Exit notes")

    args = parser.parse_args()
    config = get_config()

    # Validate inputs
    if args.close_price <= 0:
        print(f"Error: Close price must be positive (got ${args.close_price})")
        sys.exit(1)

    if args.actual_move < -100 or args.actual_move > 200:
        print(f"Error: Actual move seems unrealistic ({args.actual_move}%)")
        print("Expected range: -100% to +200%")
        sys.exit(1)

    tracker = PositionTracker(config.database.path)

    # Determine position ID
    if args.id:
        position_id = args.id
        position = tracker.get_position(position_id)
        if not position:
            print(f"Error: Position {position_id} not found")
            sys.exit(1)

    elif args.ticker:
        # Find open positions for this ticker
        open_positions = tracker.get_open_positions()
        matching = [p for p in open_positions if p.ticker.upper() == args.ticker.upper()]

        if not matching:
            print(f"Error: No open positions found for {args.ticker}")
            sys.exit(1)

        if len(matching) > 1:
            print(f"\nMultiple open positions found for {args.ticker}:")
            for p in matching:
                print(f"  ID {p.id}: Entry {p.entry_date}, Exp {p.expiration_date}, "
                      f"Credit ${p.credit_received:,.0f}")
            print(f"\nPlease specify position ID with --id")
            sys.exit(1)

        position_id = matching[0].id
        position = matching[0]

    else:
        print("Error: Must specify either --id or --ticker")
        sys.exit(1)

    # Parse close date
    close_date = parse_date(args.date) if args.date else date.today()

    # Close position
    try:
        tracker.close_position(
            position_id=position_id,
            close_date=close_date,
            close_price=Decimal(str(args.close_price)),
            actual_move_pct=Decimal(str(args.actual_move)),
            final_pnl=Decimal(str(args.pnl)),
            exit_notes=args.notes
        )

        # Show summary
        win_loss = "WIN" if args.pnl > 0 else "LOSS"
        pnl_pct = (args.pnl / float(position.credit_received)) * 100

        print(f"\n✓ Position closed successfully!")
        print(f"  Ticker:       {position.ticker}")
        print(f"  Entry:        {position.entry_date}")
        print(f"  Close:        {close_date}")
        print(f"  Days Held:    {(close_date - position.entry_date).days}")
        print(f"\n  THESIS:")
        print(f"  VRP Ratio:    {position.vrp_ratio:.2f}x")
        print(f"  Implied Move: {position.implied_move_pct:.1f}%")
        print(f"  Historical:   {position.historical_avg_move_pct:.1f}%")
        print(f"\n  OUTCOME:")
        print(f"  Actual Move:  {args.actual_move:.1f}%")
        print(f"  Result:       {win_loss}")
        print(f"  P&L:          ${args.pnl:,.0f} ({pnl_pct:+.0f}%)")

        # Analyze why trade worked or failed
        if win_loss == "WIN":
            print(f"\n  ✓ Trade worked as expected!")

            # Check if thesis was correct
            if args.actual_move < float(position.implied_move_pct):
                print(f"  ✓ Actual move ({args.actual_move:.1f}%) < Implied ({position.implied_move_pct:.1f}%)")
            else:
                print(f"  ⚠️  Stock moved MORE than implied ({args.actual_move:.1f}% vs {position.implied_move_pct:.1f}%)")
                print(f"     But still profitable (volatility crushed faster)")
        else:
            print(f"\n  ✗ Trade did not work")

            # Analyze why
            if args.actual_move >= float(position.implied_move_pct):
                print(f"  ✗ Stock exceeded breakeven ({args.actual_move:.1f}% vs {position.implied_move_pct:.1f}%)")
            else:
                print(f"  ⚠️  Stock stayed within range but still lost money")
                print(f"     Possible IV re-expansion or early exit")

        print(f"\nView performance analytics with: ./trade.sh performance\n")

    except ValueError as e:
        print(f"\nError: {e}")
        print("Position may have already been closed or ID is invalid\n")
        sys.exit(1)
    except Exception as e:
        print(f"\nUnexpected error closing position: {e}\n")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
