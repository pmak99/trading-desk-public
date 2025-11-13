#!/usr/bin/env python3
"""
Batch analyze multiple tickers for IV Crush opportunities.

This script processes a list of tickers from a file and analyzes each one.
Designed for scheduled execution via cron or systemd timers.

Usage:
    python scripts/analyze_batch.py --file tickers.txt --earnings-file earnings.csv
    python scripts/analyze_batch.py --tickers AAPL,MSFT,GOOGL --earnings-file earnings.csv
"""

import sys
import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
import csv

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
        raise ValueError(f"Invalid date format: {date_str}. Use YYYY-MM-DD")


def load_tickers_from_file(file_path: str) -> List[str]:
    """Load tickers from a text file (one per line)."""
    tickers = []
    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                tickers.append(line.upper())
    return tickers


def load_earnings_calendar(file_path: str) -> Dict[str, dict]:
    """
    Load earnings calendar from CSV file.

    Expected format:
    ticker,earnings_date,expiration_date
    AAPL,2025-01-31,2025-02-01
    MSFT,2025-02-15,2025-02-21

    Returns dict: {ticker: {earnings_date, expiration_date}}
    """
    calendar = {}
    with open(file_path, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker = row['ticker'].strip().upper()
            calendar[ticker] = {
                'earnings_date': parse_date(row['earnings_date'].strip()),
                'expiration_date': parse_date(row['expiration_date'].strip())
            }
    return calendar


def analyze_ticker(
    container: Container,
    ticker: str,
    earnings_date: date,
    expiration_date: date
) -> Optional[dict]:
    """
    Analyze a single ticker for IV Crush opportunity.

    Returns dict with analysis results or None if analysis failed.
    """
    try:
        logger.info(f"\n{'=' * 80}")
        logger.info(f"Analyzing {ticker}")
        logger.info(f"{'=' * 80}")
        logger.info(f"Earnings Date: {earnings_date}")
        logger.info(f"Expiration: {expiration_date}")

        # Get calculators
        implied_move_calc = container.implied_move_calculator
        vrp_calc = container.vrp_calculator
        prices_repo = container.prices_repository

        # Step 1: Calculate implied move
        logger.info("\n📊 Calculating Implied Move...")
        implied_result = implied_move_calc.calculate(ticker, expiration_date)

        if implied_result.is_err:
            logger.warning(f"✗ Failed to calculate implied move: {implied_result.error}")
            return None

        implied_move = implied_result.value
        logger.info(f"✓ Implied Move: {implied_move.implied_move_pct}")
        logger.info(f"  Stock Price: {implied_move.stock_price}")
        logger.info(f"  ATM Strike: {implied_move.atm_strike}")
        logger.info(f"  Straddle Cost: {implied_move.straddle_cost}")

        # Step 2: Get historical moves
        logger.info("\n📊 Fetching Historical Moves...")
        hist_result = prices_repo.get_historical_moves(ticker, limit=12)

        if hist_result.is_err:
            logger.warning(f"✗ No historical data: {hist_result.error}")
            logger.info("   Run: python scripts/backfill.py " + ticker)
            return {
                'ticker': ticker,
                'earnings_date': str(earnings_date),
                'expiration_date': str(expiration_date),
                'implied_move_pct': str(implied_move.implied_move_pct),
                'stock_price': float(implied_move.stock_price.amount),
                'status': 'NO_HISTORICAL_DATA',
                'tradeable': False
            }

        historical_moves = hist_result.value
        logger.info(f"✓ Found {len(historical_moves)} historical moves")

        # Step 3: Calculate VRP
        logger.info("\n📊 Calculating VRP...")
        vrp_result = vrp_calc.calculate(
            ticker=ticker,
            expiration=expiration_date,
            implied_move=implied_move,
            historical_moves=historical_moves,
        )

        if vrp_result.is_err:
            logger.warning(f"✗ Failed to calculate VRP: {vrp_result.error}")
            return None

        vrp = vrp_result.value

        logger.info(f"✓ VRP Ratio: {vrp.vrp_ratio:.2f}x")
        logger.info(f"  Implied Move: {vrp.implied_move_pct}")
        logger.info(f"  Historical Mean: {vrp.historical_mean_move_pct}")
        logger.info(f"  Edge Score: {vrp.edge_score:.2f}")
        logger.info(f"  Recommendation: {vrp.recommendation.value.upper()}")

        if vrp.is_tradeable:
            logger.info("\n✅ TRADEABLE OPPORTUNITY")
        else:
            logger.info("\n⏭️  SKIP - Insufficient edge")

        return {
            'ticker': ticker,
            'earnings_date': str(earnings_date),
            'expiration_date': str(expiration_date),
            'stock_price': float(implied_move.stock_price.amount),
            'implied_move_pct': str(vrp.implied_move_pct),
            'historical_mean_pct': str(vrp.historical_mean_move_pct),
            'vrp_ratio': float(vrp.vrp_ratio),
            'edge_score': float(vrp.edge_score),
            'recommendation': vrp.recommendation.value,
            'is_tradeable': vrp.is_tradeable,
            'status': 'SUCCESS'
        }

    except Exception as e:
        logger.error(f"✗ Error analyzing {ticker}: {e}", exc_info=True)
        return None


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Batch analyze IV Crush opportunities for multiple tickers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Analyze tickers from file with earnings calendar
    python scripts/analyze_batch.py --file tickers.txt --earnings-file earnings.csv

    # Analyze specific tickers
    python scripts/analyze_batch.py --tickers AAPL,MSFT,GOOGL --earnings-file earnings.csv

Earnings Calendar Format (CSV):
    ticker,earnings_date,expiration_date
    AAPL,2025-01-31,2025-02-01
    MSFT,2025-02-15,2025-02-21

Notes:
    - Requires TRADIER_API_KEY in .env
    - Historical data should be backfilled first for VRP calculation
    - Results are logged and can be redirected to file
        """,
    )

    # Ticker source (mutually exclusive)
    ticker_group = parser.add_mutually_exclusive_group(required=True)
    ticker_group.add_argument(
        "--file",
        type=str,
        help="File with tickers (one per line)"
    )
    ticker_group.add_argument(
        "--tickers",
        type=str,
        help="Comma-separated list of tickers"
    )

    # Earnings calendar
    parser.add_argument(
        "--earnings-file",
        type=str,
        required=True,
        help="CSV file with earnings calendar (ticker,earnings_date,expiration_date)"
    )

    # Options
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level"
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue processing remaining tickers if one fails"
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(level=args.log_level)

    logger.info("=" * 80)
    logger.info("IV Crush 2.0 - Batch Ticker Analysis")
    logger.info("=" * 80)

    try:
        # Load configuration
        config = Config.from_env()
        container = Container(config)

        # Load tickers
        if args.file:
            tickers = load_tickers_from_file(args.file)
            logger.info(f"Loaded {len(tickers)} tickers from {args.file}")
        else:
            tickers = [t.strip().upper() for t in args.tickers.split(',')]
            logger.info(f"Processing {len(tickers)} tickers: {', '.join(tickers)}")

        # Load earnings calendar
        logger.info(f"Loading earnings calendar from {args.earnings_file}")
        earnings_calendar = load_earnings_calendar(args.earnings_file)
        logger.info(f"Loaded {len(earnings_calendar)} earnings events")

        # Process each ticker
        results = []
        success_count = 0
        error_count = 0
        skip_count = 0

        for ticker in tickers:
            # Check if ticker is in earnings calendar
            if ticker not in earnings_calendar:
                logger.warning(f"\n⚠️  {ticker} not found in earnings calendar - skipping")
                skip_count += 1
                continue

            # Get earnings dates
            event = earnings_calendar[ticker]
            earnings_date = event['earnings_date']
            expiration_date = event['expiration_date']

            # Analyze ticker
            result = analyze_ticker(
                container,
                ticker,
                earnings_date,
                expiration_date
            )

            if result:
                results.append(result)
                if result['status'] == 'SUCCESS':
                    success_count += 1
                else:
                    skip_count += 1
            else:
                error_count += 1
                if not args.continue_on_error:
                    logger.error(f"Stopping due to error with {ticker}")
                    break

        # Summary
        logger.info("\n" + "=" * 80)
        logger.info("Batch Analysis Complete")
        logger.info("=" * 80)
        logger.info(f"Total Tickers: {len(tickers)}")
        logger.info(f"✓ Analyzed: {success_count}")
        logger.info(f"⏭️  Skipped: {skip_count}")
        logger.info(f"✗ Errors: {error_count}")

        # Tradeable opportunities
        tradeable = [r for r in results if r.get('is_tradeable', False)]
        if tradeable:
            logger.info(f"\n🎯 {len(tradeable)} TRADEABLE OPPORTUNITIES:")
            for r in tradeable:
                logger.info(
                    f"  {r['ticker']}: "
                    f"VRP {r['vrp_ratio']:.2f}x, "
                    f"Edge {r['edge_score']:.2f}, "
                    f"{r['recommendation'].upper()}"
                )
        else:
            logger.info("\nNo tradeable opportunities found")

        return 0 if error_count == 0 else 1

    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        return 130

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
