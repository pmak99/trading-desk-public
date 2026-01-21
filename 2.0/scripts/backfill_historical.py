#!/usr/bin/env python3
"""
Accurate Historical Earnings Backfill Script.

Uses:
- Database earnings_calendar table for earnings dates WITH timing (BMO/AMC)
- Twelve Data for historical prices (more reliable than yfinance, 800 calls/day free)

Key fix: Handles BMO vs AMC timing correctly:
- BMO: prev_day close -> earnings_day open
- AMC: earnings_day close -> next_day open

Usage:
    python scripts/backfill_historical.py MU ORCL AVGO
    python scripts/backfill_historical.py --file tickers.txt --start-date 2025-01-01
"""

import sys
import os
import argparse
import logging
import sqlite3
import time
import requests
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass
from enum import Enum

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)

# API Configuration
TWELVE_DATA_KEY = os.environ.get("TWELVE_DATA_KEY", "")

# Rate limiting (Twelve Data free tier: 8/min, 800/day)
TWELVE_DATA_CALLS_PER_MINUTE = 8


class EarningsTiming(Enum):
    BMO = "bmo"  # Before Market Open
    AMC = "amc"  # After Market Close
    UNKNOWN = ""


@dataclass
class EarningsEvent:
    """Earnings event with timing information."""
    ticker: str
    date: date
    timing: EarningsTiming
    eps_actual: Optional[float] = None
    eps_estimate: Optional[float] = None


@dataclass
class DailyPrice:
    """Daily OHLCV price data."""
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class EarningsMove:
    """Calculated earnings move."""
    ticker: str
    earnings_date: date
    timing: EarningsTiming
    prev_close: float
    reaction_open: float
    reaction_high: float
    reaction_low: float
    reaction_close: float
    gap_move_pct: float
    intraday_move_pct: float
    close_move_pct: float
    volume_before: int
    volume_reaction: int


def get_db_earnings(
    ticker: str,
    db_path: Path,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> List[EarningsEvent]:
    """
    Get earnings dates with timing from existing database.

    Args:
        ticker: Stock symbol
        db_path: Path to database
        start_date: Optional start date filter
        end_date: Optional end date filter

    Returns:
        List of EarningsEvent with timing info
    """
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        cursor = conn.cursor()

        query = """
            SELECT ticker, earnings_date, timing
            FROM earnings_calendar
            WHERE ticker = ?
        """
        params = [ticker]

        if start_date:
            query += " AND earnings_date >= ?"
            params.append(str(start_date))
        if end_date:
            query += " AND earnings_date <= ?"
            params.append(str(end_date))

        query += " ORDER BY earnings_date"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        events = []
        for row in rows:
            _, date_str, timing_str = row
            event_date = date.fromisoformat(date_str)

            # Parse timing
            timing_str = (timing_str or "").lower()
            if timing_str == "bmo":
                timing = EarningsTiming.BMO
            elif timing_str == "amc":
                timing = EarningsTiming.AMC
            else:
                timing = EarningsTiming.UNKNOWN

            events.append(EarningsEvent(
                ticker=ticker,
                date=event_date,
                timing=timing,
            ))

        return events

    except Exception as e:
        logger.error(f"Error reading earnings from DB: {e}")
        return []


def get_twelve_data_prices(
    ticker: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> List[DailyPrice]:
    """
    Get historical daily prices from Twelve Data.

    Args:
        ticker: Stock symbol
        start_date: Start date for data
        end_date: End date for data

    Returns:
        List of DailyPrice sorted by date descending
    """
    if not TWELVE_DATA_KEY:
        logger.error("TWELVE_DATA_KEY not set")
        return []

    try:
        url = "https://api.twelvedata.com/time_series"
        params = {
            "symbol": ticker,
            "interval": "1day",
            "apikey": TWELVE_DATA_KEY,
            "outputsize": 5000,  # Max available
        }

        if start_date:
            params["start_date"] = start_date.isoformat()
        if end_date:
            params["end_date"] = end_date.isoformat()

        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()

        data = response.json()

        # Check for errors
        if data.get("status") == "error":
            logger.error(f"Twelve Data error: {data.get('message', 'Unknown error')}")
            return []

        values = data.get("values", [])
        if not values:
            logger.warning(f"No price data for {ticker}")
            return []

        prices = []
        for v in values:
            try:
                prices.append(DailyPrice(
                    date=date.fromisoformat(v["datetime"]),
                    open=float(v["open"]),
                    high=float(v["high"]),
                    low=float(v["low"]),
                    close=float(v["close"]),
                    volume=int(v["volume"]),
                ))
            except (KeyError, ValueError) as ex:
                logger.debug(f"Skipping malformed price: {ex}")
                continue

        # Sort by date descending
        prices.sort(key=lambda x: x.date, reverse=True)
        return prices

    except requests.exceptions.RequestException as e:
        logger.error(f"Twelve Data API error: {e}")
        return []


def calculate_earnings_move(
    event: EarningsEvent,
    prices: List[DailyPrice],
) -> Optional[EarningsMove]:
    """
    Calculate earnings move with correct BMO/AMC handling.

    BMO (Before Market Open):
        - Earnings announced before market opens
        - Stock reacts on earnings day
        - Move = prev_day close -> earnings_day open

    AMC (After Market Close):
        - Earnings announced after market closes
        - Stock reacts NEXT day
        - Move = earnings_day close -> next_day open

    UNKNOWN:
        - Try to infer from price action, default to BMO logic
    """
    # Build price lookup by date
    price_map: Dict[date, DailyPrice] = {p.date: p for p in prices}

    earnings_date = event.date

    # Find trading days around earnings
    sorted_dates = sorted(price_map.keys())

    # Find earnings day index
    if earnings_date not in price_map:
        # Find closest trading day
        closest = min(sorted_dates, key=lambda d: abs((d - earnings_date).days))
        if abs((closest - earnings_date).days) > 3:
            logger.warning(f"  No price data near {earnings_date}")
            return None
        earnings_date = closest
        logger.debug(f"  Adjusted earnings date to {earnings_date}")

    earnings_idx = sorted_dates.index(earnings_date)

    # Get prev day (for BMO) or earnings day close (for AMC)
    if earnings_idx == 0:
        logger.warning(f"  No previous day data for {earnings_date}")
        return None

    prev_date = sorted_dates[earnings_idx - 1]
    prev_price = price_map[prev_date]
    earnings_price = price_map[earnings_date]

    # Determine reaction day based on timing
    if event.timing == EarningsTiming.AMC:
        # AMC: reaction is next trading day
        if earnings_idx >= len(sorted_dates) - 1:
            logger.warning(f"  No next day data for AMC earnings {earnings_date}")
            return None

        next_date = sorted_dates[earnings_idx + 1]
        reaction_price = price_map[next_date]

        # For AMC: prev_close is earnings_day close, reaction is next_day
        reference_close = earnings_price.close
        volume_before = earnings_price.volume

        logger.debug(f"  AMC: {earnings_date} close ${reference_close:.2f} -> {next_date} open ${reaction_price.open:.2f}")

    else:
        # BMO or UNKNOWN: reaction is earnings day
        reaction_price = earnings_price

        # For BMO: prev_close is prev_day close
        reference_close = prev_price.close
        volume_before = prev_price.volume

        logger.debug(f"  BMO: {prev_date} close ${reference_close:.2f} -> {earnings_date} open ${reaction_price.open:.2f}")

    # Calculate moves
    if reference_close == 0:
        logger.warning(f"  Reference close is zero")
        return None

    # Gap move: reference close -> reaction open (preserves sign)
    gap_move_pct = (reaction_price.open - reference_close) / reference_close * 100

    # Intraday move: high-low range as % (always positive)
    intraday_move_pct = abs((reaction_price.high - reaction_price.low) / reference_close * 100)

    # Close move: reference close -> reaction close (preserves sign)
    close_move_pct = (reaction_price.close - reference_close) / reference_close * 100

    return EarningsMove(
        ticker=event.ticker,
        earnings_date=event.date,  # Original earnings date
        timing=event.timing,
        prev_close=reference_close,
        reaction_open=reaction_price.open,
        reaction_high=reaction_price.high,
        reaction_low=reaction_price.low,
        reaction_close=reaction_price.close,
        gap_move_pct=gap_move_pct,
        intraday_move_pct=intraday_move_pct,
        close_move_pct=close_move_pct,
        volume_before=volume_before,
        volume_reaction=reaction_price.volume,
    )


def save_to_database(db_path: Path, move: EarningsMove) -> bool:
    """Save earnings move to database."""
    try:
        conn = sqlite3.connect(str(db_path), timeout=30.0)
        cursor = conn.cursor()

        # Update earnings_calendar with timing
        cursor.execute('''
            INSERT OR REPLACE INTO earnings_calendar
            (ticker, earnings_date, timing, confirmed)
            VALUES (?, ?, ?, 1)
        ''', (move.ticker, str(move.earnings_date), move.timing.name))

        # Update historical_moves
        cursor.execute('''
            INSERT OR REPLACE INTO historical_moves
            (ticker, earnings_date, prev_close, earnings_open, earnings_high,
             earnings_low, earnings_close, intraday_move_pct, gap_move_pct,
             close_move_pct, volume_before, volume_earnings)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            move.ticker,
            str(move.earnings_date),
            move.prev_close,
            move.reaction_open,
            move.reaction_high,
            move.reaction_low,
            move.reaction_close,
            move.intraday_move_pct,
            move.gap_move_pct,
            move.close_move_pct,
            move.volume_before,
            move.volume_reaction,
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
    Backfill historical moves for a single ticker.

    Returns:
        Number of moves saved
    """
    logger.info(f"\n{'=' * 60}")
    logger.info(f"Backfilling {ticker}")
    logger.info(f"{'=' * 60}")

    # Step 1: Get earnings dates with timing from database
    logger.info(f"📅 Fetching earnings dates from database...")
    events = get_db_earnings(ticker, db_path, start_date, end_date)

    if not events:
        logger.warning(f"No earnings found for {ticker} in database")
        return 0

    logger.info(f"✓ Found {len(events)} earnings events")

    # Show timing breakdown
    bmo_count = sum(1 for e in events if e.timing == EarningsTiming.BMO)
    amc_count = sum(1 for e in events if e.timing == EarningsTiming.AMC)
    unknown_count = sum(1 for e in events if e.timing == EarningsTiming.UNKNOWN)
    logger.info(f"  Timing: {bmo_count} BMO, {amc_count} AMC, {unknown_count} unknown")

    # Step 2: Get historical prices from Twelve Data
    logger.info(f"📊 Fetching prices from Twelve Data...")

    # Expand date range to ensure we have data for prev/next day calculations
    fetch_start = (start_date - timedelta(days=10)) if start_date else None
    fetch_end = (end_date + timedelta(days=10)) if end_date else None

    prices = get_twelve_data_prices(ticker, fetch_start, fetch_end)

    if not prices:
        logger.error(f"No price data for {ticker}")
        return 0

    logger.info(f"✓ Fetched {len(prices)} days of price data")

    # Step 3: Calculate and save moves
    moves_saved = 0

    for event in events:
        logger.info(f"  Processing {event.date} ({event.timing.value or 'unknown'})...")

        move = calculate_earnings_move(event, prices)

        if move is None:
            logger.warning(f"  ❌ Could not calculate move")
            continue

        if save_to_database(db_path, move):
            direction = "+" if move.gap_move_pct >= 0 else ""
            logger.info(
                f"  ✓ Saved: {direction}{move.gap_move_pct:.2f}% gap, "
                f"{move.close_move_pct:.2f}% close ({event.timing.value})"
            )
            moves_saved += 1
        else:
            logger.error(f"  ❌ Failed to save")

    logger.info(f"\n✓ Backfilled {moves_saved}/{len(events)} moves for {ticker}")
    return moves_saved


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Accurate historical earnings backfill using database timing + Twelve Data prices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Backfill specific tickers
    python scripts/backfill_historical.py MU ORCL AVGO

    # Backfill with date range
    python scripts/backfill_historical.py MU --start-date 2025-01-01 --end-date 2025-12-31

    # Backfill from file
    python scripts/backfill_historical.py --file tickers.txt --start-date 2025-01-01

Environment variables required:
    TWELVE_DATA_KEY     - Twelve Data API key for price history (free: 800 calls/day)

Data sources:
    - Earnings dates/timing: reads from earnings_calendar table in database
    - Price history: Twelve Data time_series API
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

    # Validate API keys
    if not TWELVE_DATA_KEY:
        print("Error: TWELVE_DATA_KEY environment variable not set")
        return 1

    # Get ticker list
    tickers: List[str] = []

    if args.tickers:
        tickers.extend([t.upper() for t in args.tickers])

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
    logger.info("IV Crush 2.0 - Accurate Historical Data Backfill")
    logger.info("=" * 80)
    logger.info(f"Data sources: Database (timing) + Twelve Data (prices)")
    logger.info(f"Tickers: {len(tickers)}")
    logger.info(f"Date range: {start_date or 'all'} to {end_date or 'all'}")
    logger.info(f"Database: {args.db_path}")
    logger.info("=" * 80)

    db_path = Path(args.db_path)

    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
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

            # Rate limiting - Twelve Data is 8 calls/min on free tier
            if i < len(tickers):
                logger.info("  ⏳ Rate limit pause (8s)...")
                time.sleep(8)

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
