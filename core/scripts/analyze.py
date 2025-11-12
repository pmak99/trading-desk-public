#!/usr/bin/env python3
"""
Analyze a single ticker for IV Crush opportunity.

Usage:
    python scripts/analyze.py AAPL --earnings-date 2025-01-31 --expiration 2025-02-01
"""

import sys
import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.container import Container
from src.config.config import Config

logger = logging.getLogger(__name__)


def parse_date(date_str: str) -> date:
    """Parse date string in ISO format."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Invalid date format: {date_str}. Use YYYY-MM-DD")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze IV Crush opportunity for a ticker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Analyze AAPL for upcoming earnings
    python scripts/analyze.py AAPL --earnings-date 2025-01-31 --expiration 2025-02-01

    # Use next Friday expiration
    python scripts/analyze.py TSLA --earnings-date 2025-02-15 --expiration 2025-02-21

Notes:
    - Requires TRADIER_API_KEY in .env
    - Historical data must be backfilled first for VRP calculation
        """,
    )

    parser.add_argument("ticker", type=str, help="Stock ticker symbol")
    parser.add_argument(
        "--earnings-date",
        type=parse_date,
        required=True,
        help="Earnings date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--expiration",
        type=parse_date,
        required=True,
        help="Option expiration date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(level=args.log_level)

    logger.info("=" * 80)
    logger.info(f"IV Crush 2.0 - Ticker Analysis")
    logger.info("=" * 80)
    logger.info(f"Ticker: {args.ticker}")
    logger.info(f"Earnings Date: {args.earnings_date}")
    logger.info(f"Expiration: {args.expiration}")
    logger.info("=" * 80)

    try:
        # Load configuration
        config = Config.from_env()

        # Create container
        container = Container(config)

        # Get calculators
        implied_move_calc = container.implied_move_calculator
        vrp_calc = container.vrp_calculator

        # Step 1: Calculate implied move
        logger.info("\n📊 Step 1: Calculate Implied Move")
        implied_result = implied_move_calc.calculate(
            args.ticker, args.expiration
        )

        if implied_result.is_err:
            logger.error(f"Failed to calculate implied move: {implied_result.error}")
            return 1

        implied_move = implied_result.value

        logger.info(f"✓ Implied Move: {implied_move.implied_move_pct}")
        logger.info(f"  Stock Price: {implied_move.stock_price}")
        logger.info(f"  ATM Strike: {implied_move.atm_strike}")
        logger.info(f"  Straddle Cost: {implied_move.straddle_cost}")
        logger.info(f"  Upper Bound: {implied_move.upper_bound}")
        logger.info(f"  Lower Bound: {implied_move.lower_bound}")

        if implied_move.avg_iv:
            logger.info(f"  Average IV: {implied_move.avg_iv}")

        # Step 2: Get historical moves (from database)
        logger.info("\n📊 Step 2: Get Historical Moves")
        earnings_repo = container.earnings_repository

        # TODO: This will be implemented in Week 1
        # For now, return success after implied move calculation
        logger.info("✓ Implied move calculation complete!")
        logger.info("\nℹ️  VRP calculation requires historical data (Week 1)")
        logger.info("   Run backfill.py first to populate historical moves")

        logger.info("\n" + "=" * 80)
        logger.info("Analysis complete!")
        logger.info("=" * 80)

        return 0

    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        return 130

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
