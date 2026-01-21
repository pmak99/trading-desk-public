#!/usr/bin/env python3
"""
ARCHIVED: January 2026

Reason: Superseded by backfill_historical.py which uses:
    - Twelve Data for prices (more accurate than yfinance)
    - Database earnings_calendar for timing (from Finnhub, more reliable)

This script used yfinance for both prices AND timing inference, which was
less accurate. The BMO/AMC timing logic was correct but the data sources
were unreliable.

Use instead: python 2.0/scripts/backfill_historical.py TICKER

Original docstring below:
--------------------------------------------------------------------------------

Backfill historical earnings moves using yfinance for earnings dates.

This is a FALLBACK script that uses yfinance for both earnings dates AND
historical price data. No API key required, but less accurate than Twelve Data.

IMPORTANT - BMO/AMC Timing:
    - BMO (Before Market Open): prev_day close → earnings_day reaction
    - AMC (After Market Close): earnings_day close → next_day reaction
    - Timing is inferred from yfinance earnings_dates datetime (hour >= 16 = AMC)

Usage:
    python scripts/backfill_yfinance.py AAPL MSFT GOOGL
    python scripts/backfill_yfinance.py --file tickers.txt --start-date 2024-07-01 --end-date 2024-12-31
"""

import sys
import argparse
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple
import sqlite3

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.domain.types import EarningsTiming, HistoricalMove, Money, Percentage

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("Error: yfinance not installed. Run: pip install yfinance")
    sys.exit(1)

logger = logging.getLogger(__name__)


def get_earnings_timing(earnings_time: datetime) -> EarningsTiming:
    """
    Determine if earnings were BMO or AMC based on time.

    Args:
        earnings_time: Datetime of earnings announcement

    Returns:
        EarningsTiming enum value
    """
    hour = earnings_time.hour

    # Before 9:30 AM = BMO
    if hour < 9 or (hour == 9 and earnings_time.minute < 30):
        return EarningsTiming.BMO
    # After 4:00 PM = AMC
    elif hour >= 16:
        return EarningsTiming.AMC
    # During market hours = DMH (rare)
    else:
        return EarningsTiming.DMH


def calculate_earnings_move(
    ticker: str,
    earnings_date: date,
    price_data: pd.DataFrame,
    timing: EarningsTiming = EarningsTiming.BMO
) -> Optional[HistoricalMove]:
    """
    Calculate price movement for an earnings event.

    For BMO (Before Market Open):
        - prev_close = trading day before earnings
        - reaction = earnings day (market reacts at open)

    For AMC (After Market Close):
        - prev_close = earnings day close (before announcement)
        - reaction = next trading day (market reacts next morning)

    Args:
        ticker: Stock symbol
        earnings_date: Date of earnings announcement
        price_data: DataFrame with OHLCV data indexed by date
        timing: BMO, AMC, DMH, or UNKNOWN (defaults to BMO behavior)

    Returns:
        HistoricalMove object or None if insufficient data
    """
    try:
        available_dates = sorted(price_data.index.date)

        if timing == EarningsTiming.AMC:
            # AMC: Earnings announced after market close on earnings_date
            # prev_close = earnings_date close (before announcement)
            # reaction = next trading day
            if earnings_date not in available_dates:
                logger.warning(f"  No price data for earnings date {earnings_date}")
                return None

            # Find next trading day after earnings
            next_dates = [d for d in available_dates if d > earnings_date]
            if not next_dates:
                logger.warning(f"  No price data after {earnings_date} (AMC - need next day)")
                return None
            reaction_date = min(next_dates)

            # prev_close is the earnings day close (before AMC announcement)
            prev_close = float(price_data.loc[str(earnings_date)]['Close'])
            volume_before = int(price_data.loc[str(earnings_date)]['Volume'])

            # Reaction is on the next trading day
            reaction_open = float(price_data.loc[str(reaction_date)]['Open'])
            reaction_high = float(price_data.loc[str(reaction_date)]['High'])
            reaction_low = float(price_data.loc[str(reaction_date)]['Low'])
            reaction_close = float(price_data.loc[str(reaction_date)]['Close'])
            volume_reaction = int(price_data.loc[str(reaction_date)]['Volume'])

            logger.debug(f"  AMC: prev_close={earnings_date}, reaction={reaction_date}")

        else:
            # BMO/DMH/UNKNOWN: Earnings announced before/during market open
            # prev_close = trading day before earnings
            # reaction = earnings day itself

            # Find previous trading day
            prev_dates = [d for d in available_dates if d < earnings_date]
            if not prev_dates:
                logger.warning(f"  No price data before {earnings_date}")
                return None
            prev_date = max(prev_dates)

            if earnings_date not in available_dates:
                logger.warning(f"  No price data for {earnings_date}")
                return None

            reaction_date = earnings_date

            prev_close = float(price_data.loc[str(prev_date)]['Close'])
            volume_before = int(price_data.loc[str(prev_date)]['Volume'])

            reaction_open = float(price_data.loc[str(reaction_date)]['Open'])
            reaction_high = float(price_data.loc[str(reaction_date)]['High'])
            reaction_low = float(price_data.loc[str(reaction_date)]['Low'])
            reaction_close = float(price_data.loc[str(reaction_date)]['Close'])
            volume_reaction = int(price_data.loc[str(reaction_date)]['Volume'])

            logger.debug(f"  BMO: prev_close={prev_date}, reaction={reaction_date}")

        # Calculate moves (all percentages use prev_close as denominator)
        gap_move_pct = (reaction_open - prev_close) / prev_close * 100
        intraday_move_pct = abs((reaction_high - reaction_low) / prev_close * 100)
        close_move_pct = (reaction_close - prev_close) / prev_close * 100

        return HistoricalMove(
            ticker=ticker,
            earnings_date=earnings_date,
            prev_close=Money(prev_close),
            earnings_open=Money(reaction_open),
            earnings_high=Money(reaction_high),
            earnings_low=Money(reaction_low),
            earnings_close=Money(reaction_close),
            intraday_move_pct=Percentage(intraday_move_pct),
            gap_move_pct=Percentage(gap_move_pct),
            close_move_pct=Percentage(close_move_pct),
            volume_before=volume_before,
            volume_earnings=volume_reaction,
        )

    except Exception as e:
        logger.error(f"  Error calculating move: {e}")
        return None


def save_to_database(
    db_path: Path,
    ticker: str,
    earnings_date: date,
    timing: EarningsTiming,
    move: HistoricalMove
) -> bool:
    """
    Save earnings event and historical move to database.

    Args:
        db_path: Path to SQLite database
        ticker: Stock symbol
        earnings_date: Date of earnings
        timing: BMO/AMC/DMH/UNKNOWN
        move: Historical move data

    Returns:
        True if saved successfully
    """
    try:
        # Use timeout and isolation level for concurrent access
        conn = sqlite3.connect(str(db_path), timeout=30.0, isolation_level='DEFERRED')
        cursor = conn.cursor()

        # Save earnings event
        cursor.execute('''
            INSERT OR REPLACE INTO earnings_calendar
            (ticker, earnings_date, timing, confirmed)
            VALUES (?, ?, ?, 1)
        ''', (ticker, str(earnings_date), timing.value))

        # Save historical move
        # Convert Money (.amount) and Percentage (.value) objects to float values
        cursor.execute('''
            INSERT OR REPLACE INTO historical_moves
            (ticker, earnings_date, prev_close, earnings_open, earnings_high,
             earnings_low, earnings_close, intraday_move_pct, gap_move_pct,
             close_move_pct, volume_before, volume_earnings)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            move.ticker,
            str(move.earnings_date),
            float(move.prev_close.amount),
            float(move.earnings_open.amount),
            float(move.earnings_high.amount),
            float(move.earnings_low.amount),
            float(move.earnings_close.amount),
            move.intraday_move_pct.value,
            move.gap_move_pct.value,
            move.close_move_pct.value,
            move.volume_before,
            move.volume_earnings,
        ))

        conn.commit()
        conn.close()
        return True

    except Exception as e:
        logger.error(f"  Database error: {e}")
        return False


def backfill_ticker(
    ticker: str,
    db_path: Path,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> int:
    """
    Backfill historical moves for a single ticker using yfinance.

    Args:
        ticker: Stock symbol
        db_path: Path to database
        start_date: Optional start date filter
        end_date: Optional end date filter

    Returns:
        Number of moves saved
    """
    logger.info(f"\n{'=' * 60}")
    logger.info(f"Backfilling {ticker}")
    logger.info(f"{'=' * 60}")

    try:
        # Get ticker object
        stock = yf.Ticker(ticker)

        # Step 1: Get earnings dates
        logger.info(f"📅 Fetching earnings dates for {ticker}...")
        earnings_dates_df = stock.earnings_dates

        if earnings_dates_df is None or len(earnings_dates_df) == 0:
            logger.warning(f"No earnings dates found for {ticker}")
            return 0

        logger.info(f"✓ Found {len(earnings_dates_df)} earnings dates")

        # Filter by date range if specified
        if start_date or end_date:
            mask = pd.Series([True] * len(earnings_dates_df), index=earnings_dates_df.index)
            if start_date:
                mask &= (earnings_dates_df.index.date >= start_date)
            if end_date:
                mask &= (earnings_dates_df.index.date <= end_date)
            earnings_dates_df = earnings_dates_df[mask]
            logger.info(f"✓ Filtered to {len(earnings_dates_df)} earnings in date range")

        if len(earnings_dates_df) == 0:
            logger.warning(f"No earnings in specified date range")
            return 0

        # Step 2: Get historical price data
        logger.info(f"📊 Fetching price history for {ticker}...")
        # Get prices for a wider range to ensure we have data
        fetch_start = earnings_dates_df.index.min() - pd.Timedelta(days=30)
        fetch_end = earnings_dates_df.index.max() + pd.Timedelta(days=30)

        price_data = stock.history(start=fetch_start, end=fetch_end)

        if price_data is None or len(price_data) == 0:
            logger.error(f"No price data found for {ticker}")
            return 0

        logger.info(f"✓ Fetched {len(price_data)} days of price data")

        # Step 3: Calculate moves for each earnings date
        moves_saved = 0

        for earnings_dt in earnings_dates_df.index:
            earnings_date = earnings_dt.date()

            # Determine timing (BMO vs AMC)
            timing = get_earnings_timing(earnings_dt)
            logger.info(f"  Processing {earnings_date} ({timing.value})...")

            # Calculate move with timing-aware logic
            move = calculate_earnings_move(ticker, earnings_date, price_data, timing)

            if move is None:
                continue

            # Save to database
            if save_to_database(db_path, ticker, earnings_date, timing, move):
                logger.info(
                    f"  ✓ Saved: {move.intraday_move_pct.value:.2f}% intraday "
                    f"(gap: {move.gap_move_pct.value:.2f}%, close: {move.close_move_pct.value:.2f}%)"
                )
                moves_saved += 1
            else:
                logger.error(f"  ❌ Failed to save move")

        logger.info(f"\n✓ Backfilled {moves_saved}/{len(earnings_dates_df)} moves for {ticker}")
        return moves_saved

    except Exception as e:
        logger.error(f"Error processing {ticker}: {e}", exc_info=True)
        return 0


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Backfill historical earnings moves using yfinance",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Backfill specific tickers for Q3-Q4 2024
    python scripts/backfill_yfinance.py AAPL MSFT GOOGL --start-date 2024-07-01 --end-date 2024-12-31

    # Backfill from file
    python scripts/backfill_yfinance.py --file tickers.txt --start-date 2024-07-01 --end-date 2024-12-31

    # Backfill all available historical data
    python scripts/backfill_yfinance.py AAPL

Notes:
    - Uses yfinance for both earnings dates and price data
    - No API key required (yfinance is free)
    - Recommended for backtesting historical data
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
        "--start-date",
        type=str,
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        help="End date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="data/ivcrush.db",
        help="Path to database file (default: data/ivcrush.db)",
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

    # Parse dates
    start_date = None
    end_date = None

    if args.start_date:
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()

    if args.end_date:
        end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()

    # Setup logging
    setup_logging(level=args.log_level)

    logger.info("=" * 80)
    logger.info("IV Crush 2.0 - Historical Data Backfill (yfinance)")
    logger.info("=" * 80)
    logger.info(f"Tickers: {len(tickers)}")
    logger.info(f"Date range: {start_date or 'all'} to {end_date or 'all'}")
    logger.info(f"Database: {args.db_path}")
    logger.info("=" * 80)

    db_path = Path(args.db_path)

    # Verify database exists
    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        logger.error("Run: python -c \"from src.infrastructure.database.init_schema import init_database; from pathlib import Path; init_database(Path('data/ivcrush.db'))\"")
        return 1

    # Backfill each ticker
    total_moves = 0
    failed_tickers = []

    for i, ticker in enumerate(tickers, 1):
        logger.info(f"\n[{i}/{len(tickers)}] Processing {ticker}")

        try:
            moves = backfill_ticker(
                ticker,
                db_path,
                start_date=start_date,
                end_date=end_date,
            )
            total_moves += moves

            if moves == 0:
                failed_tickers.append(ticker)

        except KeyboardInterrupt:
            logger.info("\n\nInterrupted by user")
            break

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


if __name__ == "__main__":
    sys.exit(main())
