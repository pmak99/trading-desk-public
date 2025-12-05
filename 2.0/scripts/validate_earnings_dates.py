#!/usr/bin/env python3
"""
Validate and update earnings dates using cross-reference from multiple sources.

This script compares earnings dates from Alpha Vantage, Yahoo Finance, and
optionally Earnings Whisper to ensure accuracy. It updates the database with
the consensus date and flags conflicts.

Usage:
    python scripts/validate_earnings_dates.py MRVL AEO SNOW
    python scripts/validate_earnings_dates.py --file tickers.txt
    python scripts/validate_earnings_dates.py --whisper-week  # Validate whisper tickers
    python scripts/validate_earnings_dates.py --upcoming 7    # Next 7 days
"""

import sys
import argparse
import logging
from pathlib import Path
from typing import List
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.infrastructure.api.alpha_vantage import AlphaVantageAPI
from src.infrastructure.data_sources.yahoo_finance_earnings import YahooFinanceEarnings
from src.infrastructure.data_sources.earnings_whisper_scraper import EarningsWhisperScraper
from src.application.services.earnings_date_validator import EarningsDateValidator
from src.infrastructure.database.repositories.earnings_repository import EarningsRepository
import os

logger = logging.getLogger(__name__)


def validate_and_update_ticker(
    ticker: str,
    validator: EarningsDateValidator,
    earnings_repo: EarningsRepository,
    dry_run: bool = False
) -> bool:
    """
    Validate earnings date for a ticker and update database.

    Args:
        ticker: Stock ticker symbol
        validator: Earnings date validator
        earnings_repo: Earnings repository for database updates
        dry_run: If True, don't update database

    Returns:
        True if successful, False otherwise
    """
    logger.info(f"\n{'='*70}")
    logger.info(f"Validating: {ticker}")
    logger.info(f"{'='*70}")

    result = validator.validate_earnings_date(ticker)

    if result.is_err:
        logger.error(f"❌ {ticker}: {result.error}")
        return False

    validation = result.value

    # Display results
    logger.info(f"✓ Consensus: {validation.consensus_date} ({validation.consensus_timing.value})")
    logger.info(f"  Sources:")
    for src in validation.sources:
        icon = "✓" if src.earnings_date == validation.consensus_date else "✗"
        logger.info(
            f"    {icon} {src.source.value:20s}: {src.earnings_date} ({src.timing.value})"
        )

    if validation.has_conflict:
        logger.warning(f"  ⚠️  CONFLICT DETECTED: {validation.conflict_details}")

    # Update database
    if not dry_run:
        save_result = earnings_repo.save_earnings_event(
            ticker=ticker,
            earnings_date=validation.consensus_date,
            timing=validation.consensus_timing
        )

        if save_result.is_ok:
            logger.info(f"  💾 Updated database with {validation.consensus_date}")
        else:
            logger.error(f"  ❌ Failed to update database: {save_result.error}")
            return False
    else:
        logger.info(f"  🔍 DRY RUN - Would update to {validation.consensus_date}")

    return True


def get_whisper_tickers(whisper_scraper: EarningsWhisperScraper) -> List[str]:
    """Get tickers from current week's Earnings Whisper list."""
    result = whisper_scraper.get_most_anticipated_earnings()

    if result.is_err:
        logger.error(f"Failed to fetch whisper tickers: {result.error}")
        return []

    tickers = result.value
    logger.info(f"Found {len(tickers)} tickers from Earnings Whisper")
    return tickers


def get_upcoming_tickers(earnings_repo: EarningsRepository, days: int) -> List[str]:
    """Get tickers with earnings in next N days from database."""
    result = earnings_repo.get_upcoming_earnings(days_ahead=days)

    if result.is_err:
        logger.error(f"Failed to fetch upcoming earnings: {result.error}")
        return []

    earnings = result.value
    tickers = [ticker for ticker, _ in earnings]
    logger.info(f"Found {len(tickers)} tickers with earnings in next {days} days")
    return tickers


def validate_ticker_wrapper(
    ticker: str,
    validator: EarningsDateValidator,
    earnings_repo: EarningsRepository,
    dry_run: bool
) -> tuple[str, bool, bool]:
    """
    Wrapper function for parallel ticker validation.

    Returns: (ticker, success, has_conflict)
    """
    try:
        result = validator.validate_earnings_date(ticker)

        if result.is_ok:
            validation = result.value
            has_conflict = validation.has_conflict

            if not dry_run:
                save_result = earnings_repo.save_earnings_event(
                    ticker=ticker,
                    earnings_date=validation.consensus_date,
                    timing=validation.consensus_timing
                )
                if save_result.is_ok:
                    logger.info(f"✓ {ticker}: Updated to {validation.consensus_date}")
                    return (ticker, True, has_conflict)
                else:
                    logger.error(f"✗ {ticker}: Failed to update - {save_result.error}")
                    return (ticker, False, has_conflict)
            else:
                logger.info(f"✓ {ticker}: Would update to {validation.consensus_date}")
                return (ticker, True, has_conflict)
        else:
            logger.error(f"✗ {ticker}: {result.error}")
            return (ticker, False, False)

    except Exception as e:
        logger.error(f"✗ {ticker}: Unexpected error - {e}")
        return (ticker, False, False)


def main():
    parser = argparse.ArgumentParser(
        description="Validate earnings dates from multiple sources"
    )
    parser.add_argument(
        "tickers",
        nargs="*",
        help="Ticker symbols to validate"
    )
    parser.add_argument(
        "--file",
        "-f",
        help="File containing ticker symbols (one per line)"
    )
    parser.add_argument(
        "--whisper-week",
        "-w",
        action="store_true",
        help="Validate tickers from current week's Earnings Whisper list"
    )
    parser.add_argument(
        "--upcoming",
        "-u",
        type=int,
        metavar="DAYS",
        help="Validate tickers with earnings in next N days"
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Don't update database, just show what would change"
    )
    parser.add_argument(
        "--parallel",
        "-p",
        action="store_true",
        help="Process tickers in parallel for faster validation"
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=5,
        metavar="N",
        help="Number of parallel workers (default: 5)"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level"
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(level=args.log_level)

    # Collect tickers
    tickers: List[str] = []

    if args.tickers:
        tickers.extend(args.tickers)

    if args.file:
        with open(args.file) as f:
            file_tickers = [line.strip() for line in f if line.strip()]
            tickers.extend(file_tickers)

    # Initialize data sources
    from src.utils.rate_limiter import create_alpha_vantage_limiter

    alpha_vantage = AlphaVantageAPI(
        api_key=os.getenv("ALPHA_VANTAGE_KEY", ""),
        rate_limiter=create_alpha_vantage_limiter()
    )
    yahoo_finance = YahooFinanceEarnings()
    whisper_scraper = EarningsWhisperScraper()

    # Initialize validator
    validator = EarningsDateValidator(
        alpha_vantage=alpha_vantage,
        yahoo_finance=yahoo_finance
    )

    # Initialize database
    db_path = os.getenv("DB_PATH", "data/ivcrush.db")
    earnings_repo = EarningsRepository(db_path)

    # Get tickers from whisper mode
    if args.whisper_week:
        whisper_tickers = get_whisper_tickers(whisper_scraper)
        tickers.extend(whisper_tickers)

    # Get tickers from upcoming earnings
    if args.upcoming:
        upcoming_tickers = get_upcoming_tickers(earnings_repo, args.upcoming)
        tickers.extend(upcoming_tickers)

    # Remove duplicates while preserving order
    tickers = list(dict.fromkeys(tickers))

    if not tickers:
        logger.error("No tickers specified. Use --help for usage.")
        sys.exit(1)

    logger.info(f"\n{'='*70}")
    logger.info(f"Validating {len(tickers)} tickers...")
    logger.info(f"Dry run: {args.dry_run}")
    logger.info(f"Parallel: {args.parallel} (workers: {args.workers if args.parallel else 'N/A'})")
    logger.info(f"{'='*70}\n")

    # Validate each ticker
    success_count = 0
    error_count = 0
    conflict_count = 0

    if args.parallel:
        # Parallel execution using ThreadPoolExecutor
        logger.info(f"🚀 Processing {len(tickers)} tickers in parallel with {args.workers} workers...")

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            # Submit all ticker validations
            future_to_ticker = {
                executor.submit(
                    validate_ticker_wrapper,
                    ticker,
                    validator,
                    earnings_repo,
                    args.dry_run
                ): ticker
                for ticker in tickers
            }

            # Process results as they complete with progress bar
            with tqdm(total=len(tickers), desc="Validating", unit="ticker") as pbar:
                for future in as_completed(future_to_ticker):
                    ticker, success, has_conflict = future.result()
                    if success:
                        success_count += 1
                        pbar.set_postfix({"✓": success_count, "✗": error_count, "⚠": conflict_count})
                    else:
                        error_count += 1
                        pbar.set_postfix({"✓": success_count, "✗": error_count, "⚠": conflict_count})
                    if has_conflict:
                        conflict_count += 1
                        pbar.set_postfix({"✓": success_count, "✗": error_count, "⚠": conflict_count})
                    pbar.update(1)
    else:
        # Sequential execution with progress bar
        with tqdm(tickers, desc="Validating", unit="ticker") as pbar:
            for ticker in pbar:
                try:
                    result = validator.validate_earnings_date(ticker)

                    if result.is_ok:
                        validation = result.value
                        if validation.has_conflict:
                            conflict_count += 1

                        if not args.dry_run:
                            save_result = earnings_repo.save_earnings_event(
                                ticker=ticker,
                                earnings_date=validation.consensus_date,
                                timing=validation.consensus_timing
                            )
                            if save_result.is_ok:
                                success_count += 1
                                logger.info(f"✓ {ticker}: Updated to {validation.consensus_date}")
                            else:
                                error_count += 1
                                logger.error(f"✗ {ticker}: Failed to update - {save_result.error}")
                        else:
                            success_count += 1
                            logger.info(f"✓ {ticker}: Would update to {validation.consensus_date}")
                    else:
                        error_count += 1
                        logger.error(f"✗ {ticker}: {result.error}")

                    # Update progress bar postfix with stats
                    pbar.set_postfix({"✓": success_count, "✗": error_count, "⚠": conflict_count})

                except Exception as e:
                    error_count += 1
                    logger.error(f"✗ {ticker}: Unexpected error - {e}")
                    pbar.set_postfix({"✓": success_count, "✗": error_count, "⚠": conflict_count})

    # Summary
    logger.info(f"\n{'='*70}")
    logger.info(f"SUMMARY")
    logger.info(f"{'='*70}")
    logger.info(f"Total tickers: {len(tickers)}")
    logger.info(f"✓ Successful: {success_count}")
    logger.info(f"✗ Errors: {error_count}")
    logger.info(f"⚠️  Conflicts detected: {conflict_count}")
    logger.info(f"Dry run: {args.dry_run}")

    sys.exit(0 if error_count == 0 else 1)


if __name__ == "__main__":
    main()
