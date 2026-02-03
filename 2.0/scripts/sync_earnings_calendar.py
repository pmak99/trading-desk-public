#!/usr/bin/env python3
"""
Automated earnings calendar synchronization and validation.

This script proactively discovers and validates upcoming earnings dates by:
1. Fetching full earnings calendar from Alpha Vantage (3-month horizon)
2. Cross-validating new/changed dates with Yahoo Finance
3. Detecting conflicts and using consensus (Yahoo Finance priority)
4. Updating database with validated dates

Designed to run as a daily cron job (recommended: 8 PM ET after market close)

Usage:
    # Dry run (no database updates)
    python scripts/sync_earnings_calendar.py --dry-run

    # Live sync
    python scripts/sync_earnings_calendar.py

    # Custom horizon
    python scripts/sync_earnings_calendar.py --horizon 6month

    # Check staleness only
    python scripts/sync_earnings_calendar.py --check-staleness --threshold 14
"""

import sys
import argparse
import logging
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import List, Tuple, Dict, Set
from collections import defaultdict
import sqlite3
from tqdm import tqdm

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.infrastructure.api.alpha_vantage import AlphaVantageAPI
from src.infrastructure.data_sources.yahoo_finance_earnings import YahooFinanceEarnings
from src.application.services.earnings_date_validator import EarningsDateValidator
from src.infrastructure.database.repositories.earnings_repository import EarningsRepository
from src.domain.types import EarningsTiming
from src.utils.rate_limiter import create_alpha_vantage_limiter
import os

logger = logging.getLogger(__name__)

# Skip conflict validation for tickers validated within this threshold
VALIDATION_SKIP_HOURS = 48

# Data is considered stale if not updated within this threshold
STALENESS_THRESHOLD_DAYS = 14


def should_skip_validation(last_validated_at: datetime | None) -> bool:
    """
    Check if conflict validation can be skipped based on last validation time.

    Args:
        last_validated_at: Timestamp of last validation, or None if never validated

    Returns:
        True if validation was performed within VALIDATION_SKIP_HOURS, False otherwise
    """
    if last_validated_at is None:
        return False
    hours_since_validation = (datetime.now() - last_validated_at).total_seconds() / 3600
    return hours_since_validation < VALIDATION_SKIP_HOURS


class SyncStats:
    """Track synchronization statistics."""

    def __init__(self):
        self.new_dates = 0
        self.updated_dates = 0
        self.unchanged_dates = 0
        self.conflicts_detected = 0
        self.validation_skipped = 0  # Tickers skipped due to recent validation
        self.errors = 0
        self.tickers_processed: Set[str] = set()
        self.changes: List[Dict] = []

    def log_summary(self):
        """Log summary statistics."""
        logger.info("\n" + "=" * 80)
        logger.info("SYNC SUMMARY")
        logger.info("=" * 80)
        logger.info(f"Tickers processed: {len(self.tickers_processed)}")
        logger.info(f"  ✓ New earnings dates: {self.new_dates}")
        logger.info(f"  ↻ Updated dates: {self.updated_dates}")
        logger.info(f"  = Unchanged: {self.unchanged_dates}")
        logger.info(f"  ⏭️  Validation skipped (recent): {self.validation_skipped}")
        logger.info(f"  ⚠️  Conflicts detected: {self.conflicts_detected}")
        logger.info(f"  ✗ Errors: {self.errors}")

        if self.changes:
            logger.info(f"\nCHANGES DETECTED:")
            for change in self.changes:
                logger.info(
                    f"  {change['ticker']}: {change['old_date']} → {change['new_date']} "
                    f"({change['timing'].value}) {change['reason']}"
                )


def get_database_dates(
    db_path: str, horizon_days: int = 90
) -> Dict[str, Tuple[date, EarningsTiming, datetime, datetime | None]]:
    """
    Get current earnings dates from database.

    Returns:
        Dict[ticker] = (earnings_date, timing, updated_at, last_validated_at)
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cutoff_date = (date.today() - timedelta(days=7)).isoformat()
    future_date = (date.today() + timedelta(days=horizon_days)).isoformat()

    cursor.execute(
        """
        SELECT ticker, earnings_date, timing, updated_at, last_validated_at
        FROM earnings_calendar
        WHERE earnings_date >= ? AND earnings_date <= ?
        ORDER BY earnings_date
        """,
        (cutoff_date, future_date),
    )

    result = {}
    for row in cursor.fetchall():
        ticker, earnings_date_str, timing_str, updated_at_str, last_validated_str = row
        earnings_date = datetime.strptime(earnings_date_str, "%Y-%m-%d").date()
        timing = EarningsTiming(timing_str)
        # Handle both ISO format (with T and microseconds) and simple format
        updated_at = datetime.fromisoformat(updated_at_str.replace(" ", "T").split(".")[0])
        last_validated_at = None
        if last_validated_str:
            last_validated_at = datetime.fromisoformat(last_validated_str.replace(" ", "T").split(".")[0])
        result[ticker] = (earnings_date, timing, updated_at, last_validated_at)

    conn.close()
    return result


def check_staleness(db_path: str, threshold_days: int = STALENESS_THRESHOLD_DAYS) -> List[Dict]:
    """
    Check for stale earnings data.

    Returns:
        List of dicts with stale ticker info
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    future_date = (date.today() + timedelta(days=90)).isoformat()

    cursor.execute(
        """
        SELECT
            ticker,
            earnings_date,
            timing,
            updated_at,
            julianday('now') - julianday(updated_at) as days_stale
        FROM earnings_calendar
        WHERE earnings_date >= date('now')
          AND earnings_date <= ?
          AND julianday('now') - julianday(updated_at) > ?
        ORDER BY days_stale DESC
        """,
        (future_date, threshold_days),
    )

    stale = []
    for row in cursor.fetchall():
        ticker, earnings_date, timing, updated_at, days_stale = row
        stale.append(
            {
                "ticker": ticker,
                "earnings_date": earnings_date,
                "timing": timing,
                "updated_at": updated_at,
                "days_stale": days_stale,
            }
        )

    conn.close()
    return stale


def sync_earnings_calendar(
    validator: EarningsDateValidator,
    earnings_repo: EarningsRepository,
    alpha_vantage: AlphaVantageAPI,
    db_path: str,
    horizon: str = "3month",
    dry_run: bool = False,
) -> SyncStats:
    """
    Synchronize earnings calendar with latest data.

    Args:
        validator: Earnings date validator
        earnings_repo: Earnings repository
        alpha_vantage: Alpha Vantage API client
        db_path: Database path
        horizon: Time horizon (3month, 6month, 12month)
        dry_run: If True, don't update database

    Returns:
        SyncStats with results
    """
    stats = SyncStats()

    # Get current database state
    logger.info("Loading current database state...")
    db_dates = get_database_dates(db_path)
    logger.info(f"Found {len(db_dates)} existing earnings dates in database")

    # Fetch full calendar from Alpha Vantage
    logger.info(f"Fetching earnings calendar from Alpha Vantage (horizon={horizon})...")
    calendar_result = alpha_vantage.get_earnings_calendar(horizon=horizon)

    if calendar_result.is_err:
        logger.error(f"Failed to fetch calendar: {calendar_result.error}")
        return stats

    calendar = calendar_result.value
    logger.info(f"✓ Fetched {len(calendar)} earnings events from Alpha Vantage")

    # Group by ticker (in case multiple entries per ticker)
    ticker_map: Dict[str, List[Tuple[date, EarningsTiming]]] = defaultdict(list)
    for ticker, earnings_date, timing in calendar:
        ticker_map[ticker].append((earnings_date, timing))

    # OPTIMIZATION: Only process tickers already in database
    # This prevents adding thousands of unwanted tickers
    db_tickers = set(db_dates.keys())
    av_tickers = set(ticker_map.keys())
    tickers_to_process = db_tickers & av_tickers  # Intersection

    logger.info(f"\nFiltering tickers:")
    logger.info(f"  • Database: {len(db_tickers)} tickers")
    logger.info(f"  • Alpha Vantage calendar: {len(av_tickers)} tickers")
    logger.info(f"  • Intersection (to process): {len(tickers_to_process)} tickers")

    # Warn about database tickers not in Alpha Vantage
    missing_from_av = db_tickers - av_tickers
    if missing_from_av:
        logger.warning(f"  ⚠️  {len(missing_from_av)} DB tickers not found in Alpha Vantage calendar")
        if len(missing_from_av) <= 10:
            logger.warning(f"     Missing: {', '.join(sorted(missing_from_av))}")

    # Process filtered tickers with progress bar
    for ticker in tqdm(
        sorted(tickers_to_process),
        desc="Syncing tickers",
        unit="ticker",
        disable=False
    ):
        stats.tickers_processed.add(ticker)

        # Get earliest earnings date for this ticker
        dates = sorted(ticker_map[ticker], key=lambda x: x[0])
        av_date, av_timing = dates[0]

        # Check if ticker exists in database
        if ticker in db_dates:
            db_date, db_timing, db_updated_at, last_validated_at = db_dates[ticker]
            days_stale = (datetime.now() - db_updated_at).days

            # Check if date changed
            if av_date != db_date or av_timing != db_timing:
                logger.info(
                    f"\n{'='*70}\n"
                    f"CHANGE DETECTED: {ticker}\n"
                    f"  Database: {db_date} ({db_timing.value}) [updated {days_stale}d ago]\n"
                    f"  Alpha Vantage: {av_date} ({av_timing.value})\n"
                    f"{'='*70}"
                )

                # Check if we can skip validation (validated within 48 hours)
                if should_skip_validation(last_validated_at):
                    hours_ago = (datetime.now() - last_validated_at).total_seconds() / 3600
                    logger.info(
                        f"  ⏭️  Skipping validation (validated {hours_ago:.1f}h ago) - using AV date"
                    )
                    stats.validation_skipped += 1

                    # Use Alpha Vantage date directly (no cross-validation)
                    if not dry_run:
                        save_result = earnings_repo.save_earnings_event(
                            ticker=ticker,
                            earnings_date=av_date,
                            timing=av_timing,
                            update_validation_timestamp=False,  # Keep existing validation timestamp
                        )
                        if save_result.is_ok:
                            stats.updated_dates += 1
                            stats.changes.append(
                                {
                                    "ticker": ticker,
                                    "old_date": db_date,
                                    "new_date": av_date,
                                    "timing": av_timing,
                                    "reason": "Date changed (validation skipped)",
                                }
                            )
                            logger.info(f"  💾 Updated database")
                        else:
                            logger.error(f"  ✗ Failed to update: {save_result.error}")
                            stats.errors += 1
                    else:
                        stats.updated_dates += 1
                        logger.info(f"  🔍 DRY RUN - Would update database")
                else:
                    # Cross-validate with Yahoo Finance
                    result = validator.validate_earnings_date(ticker)

                    if result.is_ok:
                        validation = result.value

                        if validation.has_conflict:
                            stats.conflicts_detected += 1
                            logger.warning(
                                f"  ⚠️  CONFLICT: {validation.conflict_details}"
                            )

                        consensus_date = validation.consensus_date
                        consensus_timing = validation.consensus_timing

                        logger.info(
                            f"  ✓ Consensus: {consensus_date} ({consensus_timing.value})"
                        )

                        # Update database
                        if not dry_run:
                            save_result = earnings_repo.save_earnings_event(
                                ticker=ticker,
                                earnings_date=consensus_date,
                                timing=consensus_timing,
                            )
                            if save_result.is_ok:
                                stats.updated_dates += 1
                                stats.changes.append(
                                    {
                                        "ticker": ticker,
                                        "old_date": db_date,
                                        "new_date": consensus_date,
                                        "timing": consensus_timing,
                                        "reason": "Date changed"
                                        if db_date != consensus_date
                                        else "Timing changed",
                                    }
                                )
                                logger.info(f"  💾 Updated database")
                            else:
                                logger.error(f"  ✗ Failed to update: {save_result.error}")
                                stats.errors += 1
                        else:
                            stats.updated_dates += 1
                            logger.info(f"  🔍 DRY RUN - Would update database")
                    else:
                        logger.error(f"  ✗ Validation failed: {result.error}")
                        stats.errors += 1
            else:
                # Date unchanged, but check if stale
                if days_stale > STALENESS_THRESHOLD_DAYS:
                    # Check if we can skip validation (validated within 48 hours)
                    if should_skip_validation(last_validated_at):
                        hours_ago = (datetime.now() - last_validated_at).total_seconds() / 3600
                        logger.debug(
                            f"{ticker}: Stale ({days_stale}d) but validated {hours_ago:.1f}h ago - skipping"
                        )
                        stats.validation_skipped += 1
                        stats.unchanged_dates += 1
                    else:
                        logger.debug(
                            f"{ticker}: Unchanged but stale ({days_stale}d) - re-validating..."
                        )

                        # Re-validate to ensure still accurate
                        result = validator.validate_earnings_date(ticker)
                        if result.is_ok:
                            validation = result.value
                            if validation.consensus_date != db_date:
                                # Date changed according to validation
                                logger.info(
                                    f"\n{'='*70}\n"
                                    f"STALE DATA CORRECTION: {ticker}\n"
                                    f"  Database: {db_date} ({db_timing.value}) [{days_stale}d stale]\n"
                                    f"  Validated: {validation.consensus_date} ({validation.consensus_timing.value})\n"
                                    f"{'='*70}"
                                )

                                if not dry_run:
                                    save_result = earnings_repo.save_earnings_event(
                                        ticker=ticker,
                                        earnings_date=validation.consensus_date,
                                        timing=validation.consensus_timing,
                                    )
                                    if save_result.is_ok:
                                        stats.updated_dates += 1
                                        stats.changes.append(
                                            {
                                                "ticker": ticker,
                                                "old_date": db_date,
                                                "new_date": validation.consensus_date,
                                                "timing": validation.consensus_timing,
                                                "reason": "Stale data corrected",
                                            }
                                        )
                            else:
                                # Date confirmed unchanged, update validation timestamp
                                if not dry_run:
                                    earnings_repo.save_earnings_event(
                                        ticker=ticker,
                                        earnings_date=db_date,
                                        timing=db_timing,
                                    )
                                stats.unchanged_dates += 1
                        else:
                            stats.unchanged_dates += 1
                else:
                    stats.unchanged_dates += 1
        else:
            # New ticker
            logger.info(
                f"\n{'='*70}\n"
                f"NEW EARNINGS: {ticker}\n"
                f"  Alpha Vantage: {av_date} ({av_timing.value})\n"
                f"{'='*70}"
            )

            # Validate with Yahoo Finance
            result = validator.validate_earnings_date(ticker)

            if result.is_ok:
                validation = result.value

                if validation.has_conflict:
                    stats.conflicts_detected += 1
                    logger.warning(f"  ⚠️  CONFLICT: {validation.conflict_details}")

                logger.info(
                    f"  ✓ Consensus: {validation.consensus_date} ({validation.consensus_timing.value})"
                )

                # Save to database
                if not dry_run:
                    save_result = earnings_repo.save_earnings_event(
                        ticker=ticker,
                        earnings_date=validation.consensus_date,
                        timing=validation.consensus_timing,
                    )
                    if save_result.is_ok:
                        stats.new_dates += 1
                        logger.info(f"  💾 Saved to database")
                    else:
                        logger.error(f"  ✗ Failed to save: {save_result.error}")
                        stats.errors += 1
                else:
                    stats.new_dates += 1
                    logger.info(f"  🔍 DRY RUN - Would save to database")
            else:
                logger.error(f"  ✗ Validation failed: {result.error}")
                stats.errors += 1

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Automated earnings calendar synchronization"
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Don't update database, just show what would change",
    )
    parser.add_argument(
        "--horizon",
        default="3month",
        choices=["3month", "6month", "12month"],
        help="Time horizon for calendar fetch (default: 3month)",
    )
    parser.add_argument(
        "--check-staleness",
        "-s",
        action="store_true",
        help="Check for stale data and exit",
    )
    parser.add_argument(
        "--threshold",
        "-t",
        type=int,
        default=STALENESS_THRESHOLD_DAYS,
        help=f"Staleness threshold in days (default: {STALENESS_THRESHOLD_DAYS})",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set logging level",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(level=args.log_level)

    db_path = os.getenv("DB_PATH", "data/ivcrush.db")

    # Check staleness only
    if args.check_staleness:
        logger.info("Checking for stale earnings data...")
        stale = check_staleness(db_path, threshold_days=args.threshold)

        if stale:
            logger.warning(
                f"\n⚠️  WARNING: {len(stale)} tickers with data >{args.threshold} days stale:"
            )
            for item in stale:
                logger.warning(
                    f"  - {item['ticker']}: {item['earnings_date']} "
                    f"({item['timing']}) - {item['days_stale']:.1f} days stale "
                    f"(last updated: {item['updated_at']})"
                )
            sys.exit(1)
        else:
            logger.info(f"✓ All upcoming earnings data is fresh (<{args.threshold} days)")
            sys.exit(0)

    # Regular sync
    logger.info("=" * 80)
    logger.info("EARNINGS CALENDAR SYNC")
    logger.info("=" * 80)
    logger.info(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE SYNC'}")
    logger.info(f"Horizon: {args.horizon}")
    logger.info(f"Database: {db_path}")
    logger.info(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 80)

    # Initialize data sources
    alpha_vantage = AlphaVantageAPI(
        api_key=os.getenv("ALPHA_VANTAGE_KEY", ""),
        rate_limiter=create_alpha_vantage_limiter(),
    )
    yahoo_finance = YahooFinanceEarnings()

    # Initialize validator
    validator = EarningsDateValidator(
        alpha_vantage=alpha_vantage, yahoo_finance=yahoo_finance
    )

    # Initialize repository
    earnings_repo = EarningsRepository(db_path)

    # Run sync
    stats = sync_earnings_calendar(
        validator=validator,
        earnings_repo=earnings_repo,
        alpha_vantage=alpha_vantage,
        db_path=db_path,
        horizon=args.horizon,
        dry_run=args.dry_run,
    )

    # Print summary
    stats.log_summary()

    if args.dry_run:
        logger.info("\n🔍 DRY RUN - No changes made to database")

    # Exit with error code if there were errors
    sys.exit(1 if stats.errors > 0 else 0)


if __name__ == "__main__":
    main()
