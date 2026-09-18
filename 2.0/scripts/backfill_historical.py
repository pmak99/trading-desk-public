#!/usr/bin/env python3
"""
Accurate Historical Earnings Backfill Script.

Uses:
- Database earnings_calendar table for earnings dates WITH timing (BMO/AMC)
- Twelve Data for historical prices (more reliable than yfinance, 800 calls/day free)

Key fix: Handles BMO vs AMC timing correctly:
- BMO: prev_day close -> earnings_day open
- AMC: earnings_day close -> next_day open

Report-only unless --execute is passed: without it, prices are fetched and
moves computed, but nothing is written to earnings_calendar or historical_moves.

Usage:
    python scripts/backfill_historical.py MU ORCL AVGO --execute
    python scripts/backfill_historical.py --file tickers.txt --start-date 2025-01-01 --execute
    python scripts/backfill_historical.py ARM COHR LITE --discover --start-date 2023-01-01 --execute
"""

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Optional

# Run by path (trade.sh, /maintenance): 2.0/ for the `scripts.*` namespace.
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

# Yahoo discovery never inserts a date this close to a stored event: it is the
# same quarter filed under an adjacent day. Same window as the cluster audit.
from common.constants import EARNINGS_SAME_EVENT_DAYS as NEAR_EXISTING_DAYS
from common.db import connect  # noqa: E402
from src.config.config import APIConfig
from src.infrastructure.api.yahoo import yahoo_call
from src.utils.logging import setup_logging

# Load .env from project root (2.0/)
load_dotenv(Path(__file__).parent.parent / ".env")

logger = logging.getLogger(__name__)

# The Twelve Data key is read by the fetch (APIConfig.from_env()), and its free
# tier (8 a minute, 800 a day) is kept by the shared client's limit, not here.

# The Twelve Data fetch, the BMO/AMC move arithmetic and the data classes live in
# scripts/earnings_moves.py, shared with jobs/record_moves.py (2026-09-13). Imported
# into this namespace so backfill_ticker and its callers see the same names.
from scripts.earnings_moves import (  # noqa: E402,F401
    DailyPrice,
    EarningsEvent,
    EarningsMove,
    EarningsTiming,
    calculate_earnings_move,
    get_twelve_data_prices,
)


def get_db_earnings(
    ticker: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> List[EarningsEvent]:
    """
    Get earnings dates with timing from existing database.

    Args:
        ticker: Stock symbol
        start_date: Optional start date filter
        end_date: Optional end date filter

    Returns:
        List of EarningsEvent with timing info
    """
    try:
        conn = connect()
        cursor = conn.cursor()

        query = """
            SELECT ticker, earnings_date, timing
            FROM earnings_calendar
            WHERE ticker = %s
        """
        params = [ticker]

        if start_date:
            query += " AND earnings_date >= %s"
            params.append(str(start_date))
        if end_date:
            query += " AND earnings_date <= %s"
            params.append(str(end_date))

        query += " ORDER BY earnings_date"

        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()

        events = []
        for row in rows:
            _, event_date, timing_str = row   # earnings_date is a date column

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


def discover_earnings_from_yahoo(
    ticker: str,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    dry_run: bool = False,
) -> int:
    """
    Discover historical earnings dates from Yahoo Finance and insert into earnings_calendar.

    Yahoo Finance's earnings_dates DataFrame includes historical earnings with timestamps
    that indicate BMO vs AMC timing. Dates with Reported EPS (not NaN) are confirmed past events.

    Args:
        ticker: Stock symbol
        start_date: Only insert dates on or after this date
        end_date: Only insert dates on or before this date
        dry_run: Count what would be inserted without inserting it

    Returns:
        Number of new dates inserted (or that would be, on a dry run)
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed - run: pip install yfinance")
        return 0

    try:
        ed = yahoo_call(lambda: yf.Ticker(ticker).earnings_dates)   # under the Yahoo limit

        if ed is None or ed.empty:
            logger.warning(f"  No Yahoo Finance earnings_dates for {ticker}")
            return 0

        conn = connect()
        cursor = conn.cursor()

        import math

        inserted = 0
        for idx, row in ed.iterrows():
            # Skip future/unconfirmed dates (no Reported EPS)
            reported_eps = row.get("Reported EPS")
            if reported_eps is None:
                continue
            try:
                if math.isnan(reported_eps):
                    continue
            except (TypeError, ValueError):
                continue

            # Parse date from timezone-aware index
            earnings_dt = idx.to_pydatetime()
            earnings_d = earnings_dt.date()

            # Only inside the requested window: a date outside it gets no move
            # computed in this run, so inserting it only adds an unmeasured row.
            if start_date and earnings_d < start_date:
                continue
            if end_date and earnings_d > end_date:
                continue

            # Determine timing from hour
            hour = earnings_dt.hour
            if hour < 10:  # Before 10 AM = BMO
                timing = "BMO"
            elif hour >= 16:  # 4 PM or later = AMC
                timing = "AMC"
            else:
                # 'UNKNOWN', not '': earnings_calendar.timing is CHECKed to
                # BMO/AMC/DMH/UNKNOWN, and '' was rejected.
                timing = "UNKNOWN"

            # ⚠️ Skip anything beside an existing event, not just an exact date.
            # Yahoo often files a pre-market release as the previous evening
            # (ASML's 2026-01-28 BMO report reads "2026-01-27 AMC"), so an
            # exact-date check inserted a second row one day off -- the same
            # quarter twice, i.e. exactly the duplicate cluster that inflates
            # VRP and that resolve_ambiguous_moves.py exists to remove. A real
            # next quarter is ~90 days away; NEAR_EXISTING_DAYS matches the
            # cluster audit window.
            cursor.execute(
                "SELECT earnings_date FROM earnings_calendar"
                " WHERE ticker = %s AND earnings_date BETWEEN %s AND %s"
                " ORDER BY abs(earnings_date - %s::date) LIMIT 1",
                (ticker, earnings_d - timedelta(days=NEAR_EXISTING_DAYS),
                 earnings_d + timedelta(days=NEAR_EXISTING_DAYS), earnings_d),
            )
            near = cursor.fetchone()
            if near is not None:
                if near[0] != earnings_d:
                    logger.info(f"  ↷ Skipped Yahoo {earnings_d}: within "
                                f"{NEAR_EXISTING_DAYS}d of stored {near[0]}")
                continue

            # confirmed = true, not 1: the column is boolean and Postgres
            # rejects an integer. Every discovery since the 2026-09-12 cutover
            # failed on that line, so a ticker new to the system never got its
            # history -- the auto-backfill in ticker_analysis.py relies on this.
            if dry_run:
                inserted += 1
                logger.info(f"  📅 [DRY RUN] Would add: {earnings_d} {timing}")
                continue
            cursor.execute(
                """INSERT INTO earnings_calendar (ticker, earnings_date, timing, confirmed)
                   VALUES (%s, %s, %s, true)
                   ON CONFLICT (ticker, earnings_date) DO NOTHING""",
                (ticker, earnings_d, timing),
            )
            inserted += cursor.rowcount
            logger.info(f"  📅 Discovered: {earnings_d} {timing}")

        conn.commit()
        conn.close()

        if inserted > 0:
            logger.info(f"  ✓ Yahoo Finance: discovered {inserted} new earnings dates for {ticker}")
        else:
            logger.info(f"  Yahoo Finance: no new dates to add for {ticker}")

        return inserted

    except Exception as e:
        logger.error(f"  Yahoo Finance discovery error for {ticker}: {e}")
        return 0


def has_move_history(ticker: str) -> bool:
    """Whether `historical_moves` holds any row for the ticker, in any date range."""
    with connect() as conn:
        return conn.execute(
            "SELECT EXISTS (SELECT 1 FROM historical_moves WHERE ticker = %s)", (ticker,)
        ).fetchone()[0]


def save_to_database(move: EarningsMove) -> bool:
    """Save earnings move to database."""
    try:
        conn = connect()
        cursor = conn.cursor()

        # Update earnings_calendar with timing
        cursor.execute('''
            INSERT INTO earnings_calendar
            (ticker, earnings_date, timing, confirmed, updated_at)
            VALUES (%s, %s, %s, true, now())
            ON CONFLICT (ticker, earnings_date) DO UPDATE SET
                timing = EXCLUDED.timing, confirmed = true, updated_at = now()
        ''', (move.ticker, str(move.earnings_date), move.timing.name))

        # Update historical_moves
        cursor.execute('''
            INSERT INTO historical_moves
            (ticker, earnings_date, prev_close, earnings_open, earnings_high,
             earnings_low, earnings_close, volume_before, volume_earnings)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ticker, earnings_date) DO UPDATE SET
                prev_close = EXCLUDED.prev_close,
                earnings_open = EXCLUDED.earnings_open,
                earnings_high = EXCLUDED.earnings_high,
                earnings_low = EXCLUDED.earnings_low,
                earnings_close = EXCLUDED.earnings_close,
                volume_before = EXCLUDED.volume_before,
                volume_earnings = EXCLUDED.volume_earnings
            -- intraday/gap/close_move_pct are GENERATED from the prices above
            -- (migration 2) and cannot be written; the values computed in
            -- compute_move() are for logging only.
            -- pre_earnings_straddle_pct and ern_iv_effect are NOT listed: the
            -- frozen ORATS backfill, which INSERT OR REPLACE wiped on every
            -- re-backfill. Same fix as prices_repository.
        ''', (
            move.ticker,
            str(move.earnings_date),
            move.prev_close,
            move.reaction_open,
            move.reaction_high,
            move.reaction_low,
            move.reaction_close,
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
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    force_discover: bool = False,
    dry_run: bool = False,
) -> int:
    """
    Backfill historical moves for a single ticker.

    Args:
        ticker: Stock symbol
        start_date: Optional start date filter
        end_date: Optional end date filter
        force_discover: Run Yahoo Finance discovery even for a ticker with move history
        dry_run: Compute moves without writing them. Discovered dates are not
            inserted either, so a dry run only computes moves for events
            already in the calendar.

    Returns:
        Number of moves saved (or that would be, on a dry run)
    """
    logger.info(f"\n{'=' * 60}")
    logger.info(f"Backfilling {ticker}")
    logger.info(f"{'=' * 60}")

    # Step 1: Get earnings dates with timing from database
    logger.info("📅 Fetching earnings dates from database...")
    events = get_db_earnings(ticker, start_date, end_date)

    # Discovery is for a ticker new to the system: one with no move row at all,
    # which the auto-backfill (trade.sh, the scan) sends with a three-year window.
    # ⚠️ Until 2026-09-14 it ran whenever the requested window held fewer than 4
    # events -- so every one-event run (--start-date D --end-date D, /maintenance
    # Step 7b, /backfill) discovered, and inserted each confirmed Yahoo date on or
    # after D on one source. A ticker with history gets new quarters from
    # jobs/record_moves.py. Either way discovery stays inside the window.
    if force_discover or not has_move_history(ticker):
        reason = "forced" if force_discover else "no move history"
        logger.info(f"🔍 Discovering earnings from Yahoo Finance ({reason})...")
        discovered = discover_earnings_from_yahoo(ticker, start_date, end_date, dry_run=dry_run)
        if discovered > 0 and not dry_run:
            events = get_db_earnings(ticker, start_date, end_date)

    if not events:
        logger.warning(f"No earnings found for {ticker} in database or Yahoo Finance")
        return 0

    logger.info(f"✓ Found {len(events)} earnings events")

    # Show timing breakdown
    bmo_count = sum(1 for e in events if e.timing == EarningsTiming.BMO)
    amc_count = sum(1 for e in events if e.timing == EarningsTiming.AMC)
    unknown_count = sum(1 for e in events if e.timing == EarningsTiming.UNKNOWN)
    logger.info(f"  Timing: {bmo_count} BMO, {amc_count} AMC, {unknown_count} unknown")

    # Step 2: Get historical prices from Twelve Data
    logger.info("📊 Fetching prices from Twelve Data...")

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
            logger.warning("  ❌ Could not calculate move")
            continue

        if dry_run:
            logger.info(
                f"  [DRY RUN] Would save: {move.gap_move_pct:+.2f}% gap, "
                f"{move.close_move_pct:.2f}% close ({event.timing.value})"
            )
            moves_saved += 1
            continue

        if save_to_database(move):
            direction = "+" if move.gap_move_pct >= 0 else ""
            logger.info(
                f"  ✓ Saved: {direction}{move.gap_move_pct:.2f}% gap, "
                f"{move.close_move_pct:.2f}% close ({event.timing.value})"
            )
            moves_saved += 1
        else:
            logger.error("  ❌ Failed to save")

    verb = "Would backfill" if dry_run else "Backfilled"
    logger.info(f"\n✓ {verb} {moves_saved}/{len(events)} moves for {ticker}")
    return moves_saved


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Accurate historical earnings backfill using database timing + Twelve Data prices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Backfill specific tickers
    python scripts/backfill_historical.py MU ORCL AVGO --execute

    # Show what would be written, without writing
    python scripts/backfill_historical.py MU ORCL AVGO

    # Backfill with date range
    python scripts/backfill_historical.py MU --start-date 2025-01-01 --end-date 2025-12-31 --execute

    # Backfill from file
    python scripts/backfill_historical.py --file tickers.txt --start-date 2025-01-01 --execute

    # Discover earnings from Yahoo Finance for tickers that already have move history
    python scripts/backfill_historical.py ARM COHR LITE --discover --start-date 2023-01-01 --execute

Environment variables required:
    TWELVE_DATA_KEY     - Twelve Data API key for price history (free: 800 calls/day)

Data sources:
    - Earnings dates/timing: reads from earnings_calendar table in database
    - Yahoo Finance: discovers historical dates, inside --start-date/--end-date, for a
      ticker with no historical_moves row (or with --discover)
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
    # No --db flag: the target comes from PG* in the environment
    # (`PGDATABASE=ivcrush_scratch ...`). A path argument would be a
    # lie -- connect() would ignore it.
    parser.add_argument(
        "--discover",
        action="store_true",
        help="Force Yahoo Finance discovery of historical earnings dates for all tickers",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write discovered dates and moves (default: report only)",
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
    if not APIConfig.from_env().twelve_data_key:
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
    logger.info("Data sources: Database (timing) + Twelve Data (prices)")
    logger.info(f"Tickers: {len(tickers)}")
    logger.info(f"Date range: {start_date or 'all'} to {end_date or 'all'}")
    logger.info(f"Mode: {'EXECUTE' if args.execute else 'REPORT ONLY (pass --execute to write)'}")
    logger.info("=" * 80)

    # No database path, and no existence check. That guard used to abort the
    # whole run with "Database not found" -- which, once 2.0/data/ivcrush.db was
    # deleted at the Postgres cutover, meant /maintenance backfill failed
    # outright. The target now comes from PG* in the environment, and an
    # unreachable server raises on connect rather than being pre-checked
    # against a file that no longer has a meaning.

    # Backfill each ticker
    total_moves = 0
    failed_tickers = []

    for i, ticker in enumerate(tickers, 1):
        logger.info(f"\n[{i}/{len(tickers)}] Processing {ticker}")

        try:
            moves = backfill_ticker(
                ticker,
                start_date=start_date,
                end_date=end_date,
                force_discover=args.discover,
                dry_run=not args.execute,
            )
            total_moves += moves

            if moves == 0:
                failed_tickers.append(ticker)
            # No pause between tickers: the Twelve Data fetch waits on the
            # process's 8-a-minute limit itself (src/infrastructure/api/twelve_data.py).

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
    logger.info(f"Total moves {'saved' if args.execute else 'that would be saved'}: {total_moves}")

    if failed_tickers:
        logger.warning(f"\nFailed tickers ({len(failed_tickers)}): {', '.join(failed_tickers)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
