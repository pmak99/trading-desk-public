#!/usr/bin/env python3
"""
Scan for IV Crush trading opportunities.

Two modes:
1. Scanning Mode: Scan earnings for a specific date and get trade recommendations
2. Ticker Mode: Analyze specific tickers from command line (no CSV required)

Usage:
    # Scanning mode - scan earnings for a specific date
    python scripts/scan.py --scan-date 2025-01-31

    # Ticker mode - analyze specific tickers
    python scripts/scan.py --tickers AAPL,MSFT,GOOGL

    # Ticker mode with custom expiration days offset
    python scripts/scan.py --tickers AAPL,MSFT --expiration-offset 1
"""

import sys
import argparse
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.container import Container
from src.config.config import Config
from src.domain.enums import EarningsTiming

logger = logging.getLogger(__name__)

# Alpha Vantage free tier rate limits
ALPHA_VANTAGE_CALLS_PER_MINUTE = 5
RATE_LIMIT_PAUSE_SECONDS = 60


def parse_date(date_str: str) -> date:
    """Parse date string in ISO format."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as e:
        raise ValueError(f"Invalid date format: {date_str}. Use YYYY-MM-DD")


def adjust_to_trading_day(target_date: date) -> date:
    """
    Adjust date to next trading day if on weekend.

    Note: Does not account for market holidays (e.g., July 4th, Christmas).
    For full holiday checking, integrate with a market calendar library.

    Args:
        target_date: Target date to check

    Returns:
        Next trading day (Monday if weekend)
    """
    weekday = target_date.weekday()
    if weekday == 5:  # Saturday -> Monday
        return target_date + timedelta(days=2)
    elif weekday == 6:  # Sunday -> Monday
        return target_date + timedelta(days=1)
    return target_date


def get_next_friday(from_date: date) -> date:
    """Get the next Friday from the given date."""
    days_until_friday = (4 - from_date.weekday()) % 7
    if days_until_friday == 0:
        # If today is Friday, get next Friday
        days_until_friday = 7
    return from_date + timedelta(days=days_until_friday)


def calculate_expiration_date(
    earnings_date: date,
    timing: EarningsTiming,
    offset_days: Optional[int] = None
) -> date:
    """
    Calculate expiration date based on earnings timing.

    Args:
        earnings_date: Date of earnings announcement
        timing: BMO (before market open), AMC (after market close), or UNKNOWN
        offset_days: Optional custom offset in days from earnings date

    Returns:
        Expiration date for options (adjusted to trading day if needed)

    Strategy:
        - BMO: Same day (0DTE) if Friday, otherwise next Friday
        - AMC: Next day if Thursday, otherwise next Friday
        - UNKNOWN: Next Friday (conservative)
        - Custom offset: earnings_date + offset_days (adjusted to trading day)
    """
    if offset_days is not None:
        target_date = earnings_date + timedelta(days=offset_days)
        return adjust_to_trading_day(target_date)

    # For BMO (before market open)
    if timing == EarningsTiming.BMO:
        # If earnings is on Friday, use 0DTE (same day)
        if earnings_date.weekday() == 4:  # Friday
            return earnings_date
        # Otherwise use next Friday
        return get_next_friday(earnings_date)

    # For AMC (after market close)
    elif timing == EarningsTiming.AMC:
        # If earnings is on Thursday, use next day (Friday) for 1DTE
        if earnings_date.weekday() == 3:  # Thursday
            return earnings_date + timedelta(days=1)
        # Otherwise use next Friday
        return get_next_friday(earnings_date)

    # For UNKNOWN timing, use conservative next Friday
    else:
        return get_next_friday(earnings_date)


def validate_expiration_date(
    expiration_date: date,
    earnings_date: date,
    ticker: str
) -> Optional[str]:
    """
    Validate expiration date is reasonable for trading.

    Args:
        expiration_date: Calculated expiration date
        earnings_date: Earnings announcement date
        ticker: Ticker symbol (for logging)

    Returns:
        Error message if invalid, None if valid
    """
    today = date.today()

    # Check if expiration is in the past
    if expiration_date < today:
        return f"Expiration date {expiration_date} is in the past (today: {today})"

    # Check if expiration is before earnings
    if expiration_date < earnings_date:
        return f"Expiration {expiration_date} is before earnings {earnings_date}"

    # Check if expiration is on weekend (should have been adjusted, but double-check)
    if expiration_date.weekday() in [5, 6]:
        return f"Expiration date {expiration_date} is on weekend (programming error)"

    # Check if expiration is too far in future (> 30 days from earnings)
    days_after_earnings = (expiration_date - earnings_date).days
    if days_after_earnings > 30:
        return f"Expiration is {days_after_earnings} days after earnings (> 30 days, likely error)"

    return None


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

    alpha_vantage = container.alpha_vantage_api
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

    Returns:
        (earnings_date, timing) tuple or None if not found
    """
    alpha_vantage = container.alpha_vantage_api
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
    logger.info(f"{ticker}: Earnings on {earnings_date} ({timing.value})")
    return (earnings_date, timing)


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

        # Validate expiration date
        validation_error = validate_expiration_date(expiration_date, earnings_date, ticker)
        if validation_error:
            logger.error(f"✗ Invalid expiration date: {validation_error}")
            return None

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


def scanning_mode(
    container: Container,
    scan_date: date,
    expiration_offset: Optional[int] = None
) -> int:
    """
    Scanning mode: Scan earnings for a specific date.

    Returns exit code (0 for success, 1 for error)
    """
    logger.info("=" * 80)
    logger.info("SCANNING MODE: Earnings Date Scan")
    logger.info("=" * 80)
    logger.info(f"Scan Date: {scan_date}")
    logger.info("")

    # Fetch earnings for the date
    earnings_events = fetch_earnings_for_date(container, scan_date)

    if not earnings_events:
        logger.warning("No earnings found for this date")
        return 0

    # Analyze each ticker
    results = []
    success_count = 0
    error_count = 0
    skip_count = 0

    for ticker, earnings_date, timing in earnings_events:
        logger.info(f"\n{ticker}: Earnings {timing.value}")

        # Calculate expiration date
        expiration_date = calculate_expiration_date(
            earnings_date, timing, expiration_offset
        )
        logger.info(f"Calculated expiration: {expiration_date}")

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

    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("Scan Complete")
    logger.info("=" * 80)
    logger.info(f"Total Earnings: {len(earnings_events)}")
    logger.info(f"✓ Analyzed: {success_count}")
    logger.info(f"⏭️  Skipped: {skip_count}")
    logger.info(f"✗ Errors: {error_count}")

    # Tradeable opportunities
    tradeable = [r for r in results if r.get('is_tradeable', False)]
    if tradeable:
        logger.info(f"\n🎯 {len(tradeable)} TRADEABLE OPPORTUNITIES:")
        for r in sorted(tradeable, key=lambda x: x['vrp_ratio'], reverse=True):
            logger.info(
                f"  {r['ticker']:6s}: "
                f"VRP {r['vrp_ratio']:.2f}x, "
                f"Edge {r['edge_score']:.2f}, "
                f"{r['recommendation'].upper()}"
            )
    else:
        logger.info("\nNo tradeable opportunities found")

    return 0 if error_count == 0 else 1


def ticker_mode(
    container: Container,
    tickers: List[str],
    expiration_offset: Optional[int] = None
) -> int:
    """
    Ticker mode: Analyze specific tickers from command line.

    Returns exit code (0 for success, 1 for error)
    """
    logger.info("=" * 80)
    logger.info("TICKER MODE: Command Line Tickers")
    logger.info("=" * 80)
    logger.info(f"Tickers: {', '.join(tickers)}")

    # Rate limit warning
    if len(tickers) > ALPHA_VANTAGE_CALLS_PER_MINUTE:
        logger.warning(
            f"⚠️  Analyzing {len(tickers)} tickers will require rate limiting "
            f"(Alpha Vantage: {ALPHA_VANTAGE_CALLS_PER_MINUTE} calls/min)"
        )
    logger.info("")

    # Analyze each ticker
    results = []
    success_count = 0
    error_count = 0
    skip_count = 0
    api_call_count = 0

    for i, ticker in enumerate(tickers):
        # Rate limit handling - pause after every N API calls
        if api_call_count > 0 and api_call_count % ALPHA_VANTAGE_CALLS_PER_MINUTE == 0:
            logger.info(
                f"⏸️  Rate limit pause ({RATE_LIMIT_PAUSE_SECONDS}s) "
                f"after {api_call_count} API calls..."
            )
            time.sleep(RATE_LIMIT_PAUSE_SECONDS)

        logger.info(f"\n{'=' * 80}")
        logger.info(f"Processing {ticker} ({i+1}/{len(tickers)})")
        logger.info(f"{'=' * 80}")

        # Fetch earnings date for ticker (counts as 1 API call)
        earnings_info = fetch_earnings_for_ticker(container, ticker)
        api_call_count += 1

        if not earnings_info:
            logger.warning(f"No upcoming earnings found for {ticker} - skipping")
            skip_count += 1
            continue

        earnings_date, timing = earnings_info

        # Calculate expiration date
        expiration_date = calculate_expiration_date(
            earnings_date, timing, expiration_offset
        )
        logger.info(f"Calculated expiration: {expiration_date}")

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

    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("Ticker Analysis Complete")
    logger.info("=" * 80)
    logger.info(f"Total Tickers: {len(tickers)}")
    logger.info(f"✓ Analyzed: {success_count}")
    logger.info(f"⏭️  Skipped: {skip_count}")
    logger.info(f"✗ Errors: {error_count}")

    # Tradeable opportunities
    tradeable = [r for r in results if r.get('is_tradeable', False)]
    if tradeable:
        logger.info(f"\n🎯 {len(tradeable)} TRADEABLE OPPORTUNITIES:")
        for r in sorted(tradeable, key=lambda x: x['vrp_ratio'], reverse=True):
            logger.info(
                f"  {r['ticker']:6s}: "
                f"VRP {r['vrp_ratio']:.2f}x, "
                f"Edge {r['edge_score']:.2f}, "
                f"{r['recommendation'].upper()}"
            )
    else:
        logger.info("\nNo tradeable opportunities found")

    return 0 if error_count == 0 else 1


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Scan for IV Crush trading opportunities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Scanning mode - scan earnings for a specific date
    python scripts/scan.py --scan-date 2025-01-31

    # Ticker mode - analyze specific tickers
    python scripts/scan.py --tickers AAPL,MSFT,GOOGL

    # Ticker mode with custom expiration offset
    python scripts/scan.py --tickers AAPL --expiration-offset 1

Expiration Date Calculation:
    - BMO (before market open): Same day if Friday, otherwise next Friday
    - AMC (after market close): Next day if Thursday, otherwise next Friday
    - UNKNOWN: Next Friday (conservative)
    - Custom offset: earnings_date + offset_days

Notes:
    - Requires TRADIER_API_KEY and ALPHA_VANTAGE_API_KEY in .env
    - Historical data should be backfilled first for VRP calculation
    - Run: python scripts/backfill.py <TICKER> to backfill data
        """,
    )

    # Mode selection (mutually exclusive)
    mode_group = parser.add_mutually_exclusive_group(required=True)
    mode_group.add_argument(
        "--scan-date",
        type=str,
        help="Scan earnings for specific date (YYYY-MM-DD)"
    )
    mode_group.add_argument(
        "--tickers",
        type=str,
        help="Comma-separated list of tickers to analyze"
    )

    # Options
    parser.add_argument(
        "--expiration-offset",
        type=int,
        help="Custom expiration offset in days from earnings date (overrides auto-calculation)"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level"
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(level=args.log_level)

    try:
        # Load configuration
        config = Config.from_env()
        container = Container(config)

        # Execute appropriate mode
        if args.scan_date:
            scan_date = parse_date(args.scan_date)
            return scanning_mode(container, scan_date, args.expiration_offset)
        else:
            tickers = [t.strip().upper() for t in args.tickers.split(',')]
            return ticker_mode(container, tickers, args.expiration_offset)

    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        return 130

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
