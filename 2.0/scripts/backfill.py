#!/usr/bin/env python3
"""
Backfill historical earnings moves for tickers.

Usage:
    python scripts/backfill.py AAPL MSFT GOOGL
    python scripts/backfill.py --file tickers.txt
"""

import sys
import argparse
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.container import Container
from src.config.config import Config

logger = logging.getLogger(__name__)


def backfill_ticker(
    container: Container,
    ticker: str,
    quarters: int = 12,
    timeout: Optional[int] = None,
) -> int:
    """
    Backfill historical moves for a single ticker.

    Args:
        container: DI container
        ticker: Stock symbol
        quarters: Number of past quarters to backfill
        timeout: Optional timeout in seconds per ticker (default: None = no timeout)

    Returns:
        Number of moves saved
    """
    start_time = time.time()

    def check_timeout():
        """Check if timeout exceeded."""
        if timeout and (time.time() - start_time) > timeout:
            raise TimeoutError(f"Ticker {ticker} exceeded timeout of {timeout}s")

    logger.info(f"\n{'=' * 60}")
    logger.info(f"Backfilling {ticker} ({quarters} quarters)")
    if timeout:
        logger.info(f"Timeout: {timeout}s")
    logger.info(f"{'=' * 60}")

    av_client = container.alphavantage
    prices_repo = container.prices_repository
    earnings_repo = container.earnings_repository

    # Step 1: Get earnings calendar for this ticker
    logger.info(f"📅 Fetching earnings calendar for {ticker}...")
    check_timeout()
    earnings_result = av_client.get_earnings_calendar(symbol=ticker, horizon="12month")

    if earnings_result.is_err:
        logger.error(f"Failed to get earnings calendar: {earnings_result.error}")
        return 0

    # Filter to past earnings only
    earnings_events = earnings_result.value
    today = date.today()
    past_earnings = [
        (t, d, timing)
        for t, d, timing in earnings_events
        if d < today
    ]

    if not past_earnings:
        logger.warning(f"No past earnings found for {ticker}")
        return 0

    logger.info(f"✓ Found {len(past_earnings)} past earnings events")

    # Limit to requested quarters
    past_earnings = past_earnings[:quarters]

    # Step 2: Get daily prices (full history)
    logger.info(f"📊 Fetching price history for {ticker}...")
    check_timeout()
    prices_result = av_client.get_daily_prices(ticker, outputsize="full")

    if prices_result.is_err:
        logger.error(f"Failed to get prices: {prices_result.error}")
        return 0

    daily_prices = prices_result.value
    logger.info(f"✓ Fetched {len(daily_prices)} days of price data")

    # Step 3: Calculate moves for each earnings date
    moves_saved = 0

    for i, (t, earnings_date, timing) in enumerate(past_earnings, 1):
        logger.info(f"[{i}/{len(past_earnings)}] Processing {earnings_date}...")
        check_timeout()

        # Calculate move
        move_result = av_client.calculate_earnings_move(
            ticker, earnings_date, daily_prices=daily_prices
        )

        if move_result.is_err:
            logger.warning(f"  ⚠️  Skipped: {move_result.error}")
            continue

        move = move_result.value

        # Save to database
        save_result = prices_repo.save_historical_move(move)

        if save_result.is_err:
            logger.error(f"  ❌ Failed to save: {save_result.error}")
            continue

        # Also save earnings event
        earnings_repo.save_earnings_event(ticker, earnings_date, timing)

        logger.info(
            f"  ✓ Saved: {move.intraday_move_pct} intraday "
            f"(gap: {move.gap_move_pct})"
        )
        moves_saved += 1

    logger.info(f"\n✓ Backfilled {moves_saved}/{len(past_earnings)} moves for {ticker}")
    return moves_saved


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Backfill historical earnings moves",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Backfill specific tickers
    python scripts/backfill.py AAPL MSFT GOOGL

    # Backfill from file
    python scripts/backfill.py --file tickers.txt

    # Limit to 4 quarters
    python scripts/backfill.py AAPL --quarters 4

    # Set custom timeout per ticker
    python scripts/backfill.py AAPL --timeout 600

Notes:
    - Requires ALPHA_VANTAGE_KEY in .env
    - Rate limited to 5 calls/minute (Alpha Vantage free tier)
    - Use --quarters to limit data fetched per ticker
    - Use --timeout to prevent stuck tickers (default: 300s)
        """,
    )

    parser.add_argument(
        "tickers",
        nargs="*",
        type=str,
        help="Stock ticker symbols",
    )
    parser.add_argument(
        "--file",
        type=str,
        help="File with tickers (one per line)",
    )
    parser.add_argument(
        "--quarters",
        type=int,
        default=12,
        help="Number of past quarters to backfill (default: 12)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Timeout in seconds per ticker (default: 300s = 5 minutes)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    args = parser.parse_args()

    # Get ticker list
    tickers: List[str] = []

    if args.tickers:
        tickers.extend(args.tickers)

    if args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"Error: File not found: {args.file}")
            return 1

        with open(file_path, "r") as f:
            file_tickers = [
                line.strip().upper()
                for line in f
                if line.strip() and not line.startswith("#")
            ]
            tickers.extend(file_tickers)

    if not tickers:
        parser.print_help()
        print("\nError: No tickers specified")
        return 1

    # Remove duplicates
    tickers = list(dict.fromkeys(tickers))

    # Setup logging
    setup_logging(level=args.log_level)

    logger.info("=" * 80)
    logger.info("IV Crush 2.0 - Historical Data Backfill")
    logger.info("=" * 80)
    logger.info(f"Tickers: {len(tickers)}")
    logger.info(f"Quarters per ticker: {args.quarters}")
    logger.info(f"Timeout per ticker: {args.timeout}s")
    logger.info(f"Estimated API calls: {len(tickers) * 2}")  # Calendar + prices
    logger.info(f"Estimated time: ~{len(tickers) * 25} seconds (rate limits)")
    logger.info("=" * 80)

    try:
        # Load configuration
        config = Config.from_env()

        # Create container
        container = Container(config)

        # Initialize database
        container.initialize_database()

        # Backfill each ticker
        total_moves = 0
        failed_tickers = []

        for i, ticker in enumerate(tickers, 1):
            logger.info(f"\n[{i}/{len(tickers)}] Processing {ticker}")

            try:
                moves = backfill_ticker(
                    container, ticker, args.quarters, timeout=args.timeout
                )
                total_moves += moves

                if moves == 0:
                    failed_tickers.append(ticker)

            except KeyboardInterrupt:
                logger.info("\n\nInterrupted by user")
                break

            except TimeoutError as e:
                logger.warning(f"Timeout: {e}")
                failed_tickers.append(ticker)
                continue

            except Exception as e:
                logger.error(f"Error processing {ticker}: {e}", exc_info=True)
                failed_tickers.append(ticker)
                continue

        # Summary
        logger.info("\n" + "=" * 80)
        logger.info("Backfill Complete!")
        logger.info("=" * 80)
        logger.info(f"Total tickers processed: {len(tickers) - len(failed_tickers)}/{len(tickers)}")
        logger.info(f"Total moves saved: {total_moves}")

        if failed_tickers:
            logger.warning(f"\nFailed tickers ({len(failed_tickers)}): {', '.join(failed_tickers)}")

        return 0

    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        return 130

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
