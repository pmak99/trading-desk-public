"""
Earnings source aggregation - AlphaVantage, Yahoo Finance, and DB lookups.

Provides earnings calendar fetching, validation, and database sync.
"""

import logging
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from tqdm import tqdm

from src.container import Container
from src.domain.enums import EarningsTiming
from src.application.services.earnings_date_validator import EarningsDateValidator
from src.infrastructure.data_sources.yahoo_finance_earnings import YahooFinanceEarnings

logger = logging.getLogger(__name__)


def fetch_earnings_for_date(
    container: Container,
    scan_date: date
) -> List[Tuple[str, date, EarningsTiming]]:
    """
    Fetch earnings calendar and filter for specific date.

    Returns:
        List of (ticker, earnings_date, timing) tuples
    """
    logger.info(f"Fetching earnings calendar for {scan_date}...")

    alpha_vantage = container.alphavantage
    result = alpha_vantage.get_earnings_calendar(horizon="3month")

    if result.is_err:
        logger.error(f"Failed to fetch earnings calendar: {result.error}")
        return []

    all_earnings = result.value
    logger.info(f"Fetched {len(all_earnings)} total earnings events")

    # Filter for specific date
    filtered = [
        (ticker, earn_date, timing)
        for ticker, earn_date, timing in all_earnings
        if earn_date == scan_date
    ]

    logger.info(f"Found {len(filtered)} earnings on {scan_date}")
    return filtered


def fetch_earnings_for_ticker(
    container: Container,
    ticker: str
) -> Optional[Tuple[date, EarningsTiming]]:
    """
    Fetch earnings date for a specific ticker.

    Priority:
    1. Database (validated, cross-referenced source of truth)
    2. Alpha Vantage API (fallback for tickers not in DB)

    Args:
        container: DI container
        ticker: Stock ticker symbol

    Returns:
        (earnings_date, timing) tuple or None if not found
    """
    # PRIORITY 1: Check database first (source of truth, validated data)
    import sqlite3
    db_path = container.config.database.path
    try:
        with sqlite3.connect(db_path, timeout=30) as conn:
            cursor = conn.cursor()
            cursor.execute(
                '''
                SELECT earnings_date, timing, updated_at, last_validated_at
                FROM earnings_calendar
                WHERE ticker = ? AND earnings_date >= date('now')
                ORDER BY earnings_date ASC
                LIMIT 1
                ''',
                (ticker,)
            )
            row = cursor.fetchone()
            if row:
                from src.domain.enums import EarningsTiming
                from datetime import datetime
                earnings_date = date.fromisoformat(row[0])
                timing = EarningsTiming(row[1])
                updated_at = datetime.fromisoformat(row[2]) if row[2] else None
                last_validated_at = datetime.fromisoformat(row[3]) if row[3] else None

                # Freshness validation: if earnings within 7 days and not recently validated,
                # check Alpha Vantage to catch date changes
                days_until_earnings = (earnings_date - date.today()).days
                # Use last_validated_at if available, otherwise fall back to updated_at
                last_checked = last_validated_at or updated_at
                hours_since_check = (datetime.now() - last_checked).total_seconds() / 3600 if last_checked else 999

                if days_until_earnings <= 7 and hours_since_check > 24:
                    logger.info(f"{ticker}: Validating stale cache ({hours_since_check:.0f}h old, earnings in {days_until_earnings}d)")
                    alpha_vantage = container.alphavantage
                    av_result = alpha_vantage.get_earnings_calendar(symbol=ticker, horizon="3month")

                    if av_result.is_ok and av_result.value:
                        _, av_date, av_timing = av_result.value[0]
                        if av_date != earnings_date:
                            date_diff_days = (av_date - earnings_date).days

                            # If API returns a date 45+ days different (earlier OR later), it's a different quarter
                            NEXT_QUARTER_THRESHOLD_DAYS = 45
                            if abs(date_diff_days) >= NEXT_QUARTER_THRESHOLD_DAYS:
                                direction = "later" if date_diff_days > 0 else "earlier"
                                logger.warning(
                                    f"{ticker}: API shows different quarter ({av_date}, {abs(date_diff_days)}d {direction}). "
                                    f"DB date {earnings_date} likely stale or mismatched. Skipping."
                                )
                                # Mark as validated but don't update to next quarter
                                cursor.execute(
                                    '''
                                    UPDATE earnings_calendar
                                    SET last_validated_at = datetime('now')
                                    WHERE ticker = ? AND earnings_date = ?
                                    ''',
                                    (ticker, earnings_date.isoformat())
                                )
                                conn.commit()
                                # Return None to skip this ticker
                                return None

                            logger.warning(f"{ticker}: Date changed! DB={earnings_date} -> API={av_date}")
                            # Delete old entry and insert new one to avoid PRIMARY KEY violation
                            try:
                                cursor.execute(
                                    'DELETE FROM earnings_calendar WHERE ticker = ? AND earnings_date = ?',
                                    (ticker, earnings_date.isoformat())
                                )
                                cursor.execute(
                                    '''
                                    INSERT OR REPLACE INTO earnings_calendar
                                    (ticker, earnings_date, timing, updated_at, last_validated_at)
                                    VALUES (?, ?, ?, datetime('now'), datetime('now'))
                                    ''',
                                    (ticker, av_date.isoformat(), av_timing.value)
                                )
                                conn.commit()
                            except sqlite3.IntegrityError as e:
                                logger.warning(f"{ticker}: DB update failed ({e}), using API date anyway")
                            logger.info(f"{ticker}: Earnings on {av_date} ({av_timing.value}) [from API - corrected]")
                            return (av_date, av_timing)
                        else:
                            # Date confirmed, update last_validated_at
                            cursor.execute(
                                '''
                                UPDATE earnings_calendar
                                SET last_validated_at = datetime('now')
                                WHERE ticker = ? AND earnings_date = ?
                                ''',
                                (ticker, earnings_date.isoformat())
                            )
                            conn.commit()
                            logger.info(f"{ticker}: Earnings on {earnings_date} ({timing.value}) [from DB - validated]")
                            return (earnings_date, timing)
                    else:
                        # API validation failed, log warning and use cached date
                        logger.warning(f"{ticker}: API validation failed, using potentially stale cache date {earnings_date}")

                logger.info(f"{ticker}: Earnings on {earnings_date} ({timing.value}) [from DB]")
                return (earnings_date, timing)
    except Exception as e:
        logger.debug(f"DB lookup failed for {ticker}: {e}")

    # PRIORITY 2: Fallback to Alpha Vantage API
    alpha_vantage = container.alphavantage
    result = alpha_vantage.get_earnings_calendar(symbol=ticker, horizon="3month")

    if result.is_err:
        logger.warning(f"Failed to fetch earnings for {ticker}: {result.error}")
        return None

    earnings = result.value
    if not earnings:
        logger.warning(f"No upcoming earnings found for {ticker}")
        return None

    # Get the nearest earnings date
    ticker_symbol, earnings_date, timing = earnings[0]
    logger.info(f"{ticker}: Earnings on {earnings_date} ({timing.value}) [from API]")
    return (earnings_date, timing)


def validate_tradeable_earnings_dates(tradeable_results: List[dict], container: Container) -> None:
    """
    Validate earnings dates for tradeable opportunities only.

    Cross-references earnings dates from Yahoo Finance and Alpha Vantage
    for tickers that passed all filters and have tradeable strategies.
    This optimizes validation by skipping tickers that won't be displayed.

    Args:
        tradeable_results: List of tradeable result dictionaries
        container: DI container with Alpha Vantage API
    """
    if not tradeable_results:
        return

    # Extract unique tickers from tradeable results
    tickers_to_validate = list({r['ticker'] for r in tradeable_results})

    if not tickers_to_validate:
        return

    logger.info(f"\n\U0001f50d Validating earnings dates for {len(tickers_to_validate)} tradeable tickers...")

    # Initialize validator
    yahoo_finance = YahooFinanceEarnings()
    validator = EarningsDateValidator(
        alpha_vantage=container.alphavantage,
        yahoo_finance=yahoo_finance
    )

    # Validate each ticker with progress bar
    success_count = 0
    conflict_count = 0

    for ticker in tqdm(tickers_to_validate, desc="Validating", unit="ticker"):
        result = validator.validate_earnings_date(ticker)

        if result.is_ok:
            validation = result.value
            success_count += 1

            if validation.has_conflict:
                conflict_count += 1
                logger.debug(f"\u26a0\ufe0f  {ticker}: Conflict detected - {validation.conflict_details}")
        else:
            logger.debug(f"\u2717 {ticker}: Validation failed - {result.error}")

    logger.info(f"\u2713 Validated {success_count}/{len(tickers_to_validate)} tickers" +
                (f" (\u26a0\ufe0f  {conflict_count} conflicts)" if conflict_count > 0 else ""))
    logger.info("")


def ensure_tickers_in_db(tickers: list[str], container: Container) -> None:
    """
    Ensure all tickers are in the database. Auto-add and sync missing tickers.

    This eliminates the manual workflow:
    - OLD: fetch tickers -> manually add to DB -> manually sync -> re-run whisper
    - NEW: fetch tickers -> auto-add -> auto-sync -> continue analysis

    Args:
        tickers: List of ticker symbols to ensure in database
        container: DI container
    """
    import sqlite3

    db_path = container.config.database.path
    placeholder_date = (date.today() + timedelta(days=7)).isoformat()

    # Check which tickers are missing from DB
    missing_tickers = []
    with sqlite3.connect(db_path, timeout=30) as conn:
        cursor = conn.cursor()
        for ticker in tickers:
            cursor.execute(
                "SELECT COUNT(*) FROM earnings_calendar WHERE ticker = ? AND earnings_date >= date('now')",
                (ticker,)
            )
            if cursor.fetchone()[0] == 0:
                missing_tickers.append(ticker)

    if not missing_tickers:
        logger.info(f"\u2713 All {len(tickers)} tickers already in database")
        return

    # Add missing tickers
    logger.info(f"\U0001f4dd Adding {len(missing_tickers)} new tickers to database...")
    with sqlite3.connect(db_path, timeout=30) as conn:
        cursor = conn.cursor()
        for ticker in missing_tickers:
            cursor.execute(
                """INSERT OR IGNORE INTO earnings_calendar
                   (ticker, earnings_date, timing, confirmed)
                   VALUES (?, ?, 'UNKNOWN', 0)""",
                (ticker, placeholder_date)
            )
        conn.commit()
    logger.info(f"\u2713 Added {len(missing_tickers)} tickers: {', '.join(missing_tickers[:10])}" +
                ("..." if len(missing_tickers) > 10 else ""))

    # Sync to fetch correct earnings dates
    logger.info("\U0001f504 Syncing earnings dates from Alpha Vantage + Yahoo Finance...")
    logger.info(f"   Note: This may take ~{len(missing_tickers) * 12 // 60} minutes due to rate limiting (5 calls/min)")

    # Call the existing sync script
    script_path = Path(__file__).parent.parent / "sync_earnings_calendar.py"
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            timeout=len(missing_tickers) * 15  # 15 seconds per ticker timeout
        )

        if result.returncode == 0:
            logger.info("\u2713 Earnings calendar synced successfully")

            # Clean up orphaned placeholders (confirmed=0) after successful sync
            logger.info("\U0001f9f9 Cleaning up placeholder entries...")
            with sqlite3.connect(db_path, timeout=30) as conn:
                cursor = conn.cursor()
                placeholders = ','.join('?' * len(missing_tickers))
                cursor.execute(
                    f"""DELETE FROM earnings_calendar
                        WHERE ticker IN ({placeholders}) AND confirmed = 0""",
                    missing_tickers
                )
                deleted = cursor.rowcount
                conn.commit()
            if deleted > 0:
                logger.info(f"\u2713 Removed {deleted} orphaned placeholder entries")
        else:
            logger.warning(f"\u26a0\ufe0f  Sync completed with warnings:\n{result.stderr}")
    except subprocess.TimeoutExpired:
        logger.warning("\u26a0\ufe0f  Sync timed out, but may have partially completed")
    except Exception as e:
        logger.error(f"\u2717 Sync failed: {e}")
        logger.info("   Continuing with API fallback...")
