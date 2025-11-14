#!/usr/bin/env python3
"""
Scan for IV Crush trading opportunities.

Two modes:
1. Scanning Mode: Scan earnings for a specific date and get trade recommendations
2. Ticker Mode: Analyze specific tickers from command line (no CSV required)

Usage:
    # Scanning mode - scan earnings for a specific date
    python scripts/scan.py --scan-date 2025-01-31

    # Ticker mode - analyze specific tickers (with auto-backfill)
    python scripts/scan.py --tickers AAPL,MSFT,GOOGL

    # Ticker mode with custom expiration days offset
    python scripts/scan.py --tickers AAPL,MSFT --expiration-offset 1

Auto-Backfill:
    - Ticker mode: Automatically backfills missing historical data (last 3 years)
    - Scan mode: Does NOT auto-backfill (to avoid excessive delays with many tickers)
"""

import sys
import argparse
import logging
import time
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.container import Container
from src.config.config import Config
from src.domain.enums import EarningsTiming
from src.infrastructure.data_sources.earnings_whisper_scraper import (
    EarningsWhisperScraper,
    get_week_monday
)

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

    Returns:
        (earnings_date, timing) tuple or None if not found
    """
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
    logger.info(f"{ticker}: Earnings on {earnings_date} ({timing.value})")
    return (earnings_date, timing)


def analyze_ticker(
    container: Container,
    ticker: str,
    earnings_date: date,
    expiration_date: date,
    auto_backfill: bool = False
) -> Optional[dict]:
    """
    Analyze a single ticker for IV Crush opportunity.

    Args:
        container: Dependency injection container
        ticker: Stock ticker symbol
        earnings_date: Date of earnings announcement
        expiration_date: Options expiration date
        auto_backfill: If True, automatically backfill missing historical data

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

            # Auto-backfill if enabled (for ticker mode/list mode)
            if auto_backfill:
                logger.info(f"📊 Auto-backfilling historical earnings data for {ticker}...")

                # Calculate start date (3 years ago)
                start_date = (date.today() - timedelta(days=3*365)).isoformat()
                end_date = (date.today() - timedelta(days=1)).isoformat()

                try:
                    # Call backfill script
                    result = subprocess.run(
                        [
                            sys.executable,
                            "scripts/backfill_yfinance.py",
                            ticker,
                            "--start-date", start_date,
                            "--end-date", end_date
                        ],
                        cwd=Path(__file__).parent.parent,
                        capture_output=True,
                        text=True,
                        timeout=120
                    )

                    if result.returncode == 0:
                        logger.info(f"✓ Backfill complete for {ticker}")

                        # Retry fetching historical moves
                        logger.info("📊 Retrying historical data fetch...")
                        hist_result = prices_repo.get_historical_moves(ticker, limit=12)

                        if hist_result.is_err:
                            logger.warning(f"✗ Still no historical data after backfill: {hist_result.error}")
                            return {
                                'ticker': ticker,
                                'earnings_date': str(earnings_date),
                                'expiration_date': str(expiration_date),
                                'implied_move_pct': str(implied_move.implied_move_pct),
                                'stock_price': float(implied_move.stock_price.amount),
                                'status': 'NO_HISTORICAL_DATA',
                                'tradeable': False
                            }
                    else:
                        logger.warning(f"✗ Backfill failed for {ticker}: {result.stderr}")
                        return {
                            'ticker': ticker,
                            'earnings_date': str(earnings_date),
                            'expiration_date': str(expiration_date),
                            'implied_move_pct': str(implied_move.implied_move_pct),
                            'stock_price': float(implied_move.stock_price.amount),
                            'status': 'BACKFILL_FAILED',
                            'tradeable': False
                        }

                except subprocess.TimeoutExpired:
                    logger.warning(f"✗ Backfill timeout for {ticker}")
                    return {
                        'ticker': ticker,
                        'earnings_date': str(earnings_date),
                        'expiration_date': str(expiration_date),
                        'implied_move_pct': str(implied_move.implied_move_pct),
                        'stock_price': float(implied_move.stock_price.amount),
                        'status': 'BACKFILL_TIMEOUT',
                        'tradeable': False
                    }
                except Exception as e:
                    logger.warning(f"✗ Backfill error for {ticker}: {e}")
                    return {
                        'ticker': ticker,
                        'earnings_date': str(earnings_date),
                        'expiration_date': str(expiration_date),
                        'implied_move_pct': str(implied_move.implied_move_pct),
                        'stock_price': float(implied_move.stock_price.amount),
                        'status': 'BACKFILL_ERROR',
                        'tradeable': False
                    }
            else:
                # No auto-backfill - suggest manual backfill
                logger.info("   Run: python scripts/backfill_yfinance.py " + ticker)
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

        # Analyze ticker (no auto-backfill in scan mode to avoid excessive delays)
        result = analyze_ticker(
            container,
            ticker,
            earnings_date,
            expiration_date,
            auto_backfill=False
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
    logger.info("SCAN MODE - SUMMARY")
    logger.info("=" * 80)
    logger.info(f"\n📅 Scan Details:")
    logger.info(f"   Mode: Earnings Date Scan")
    logger.info(f"   Date: {scan_date}")
    logger.info(f"   Total Earnings Found: {len(earnings_events)}")
    logger.info(f"\n📊 Analysis Results:")
    logger.info(f"   ✓ Successfully Analyzed: {success_count}")
    logger.info(f"   ⏭️  Skipped (No Data): {skip_count}")
    logger.info(f"   ✗ Errors: {error_count}")

    # Tradeable opportunities
    tradeable = [r for r in results if r.get('is_tradeable', False)]
    if tradeable:
        logger.info(f"\n" + "=" * 80)
        logger.info(f"✅ RESULT: {len(tradeable)} TRADEABLE OPPORTUNITIES FOUND")
        logger.info("=" * 80)
        logger.info(f"\n🎯 Ranked by VRP Ratio:")
        for i, r in enumerate(sorted(tradeable, key=lambda x: x['vrp_ratio'], reverse=True), 1):
            logger.info(
                f"   {i}. {r['ticker']:6s}: "
                f"VRP {r['vrp_ratio']:.2f}x | "
                f"Edge {r['edge_score']:.2f} | "
                f"{r['recommendation'].upper()}"
            )
        logger.info(f"\n📝 Next Steps:")
        logger.info(f"   1. Analyze individual tickers with: ./trade.sh TICKER {scan_date} --strategies")
        logger.info(f"   2. Review strategy recommendations for each opportunity")
        logger.info(f"   3. Check broker pricing before entering positions")
    else:
        logger.info(f"\n" + "=" * 80)
        logger.info("⏭️  RESULT: NO TRADEABLE OPPORTUNITIES")
        logger.info("=" * 80)
        logger.info(f"\n❌ No opportunities found for {scan_date}")
        if skip_count > 0:
            logger.info(f"   Note: {skip_count} ticker(s) skipped due to missing historical data")
            logger.info(f"   Tip: Run individual analysis with auto-backfill using single ticker mode")
        logger.info(f"\n📝 Recommendation:")
        logger.info(f"   Try scanning a different earnings date or check whisper mode for anticipated earnings")

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

        # Analyze ticker (with auto-backfill enabled for ticker mode)
        result = analyze_ticker(
            container,
            ticker,
            earnings_date,
            expiration_date,
            auto_backfill=True
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
    logger.info("LIST MODE - SUMMARY")
    logger.info("=" * 80)
    logger.info(f"\n📋 Ticker List Analysis:")
    logger.info(f"   Mode: Multiple Ticker Analysis")
    logger.info(f"   Tickers Requested: {len(tickers)}")
    logger.info(f"   Tickers Analyzed: {', '.join(tickers)}")
    logger.info(f"\n📊 Analysis Results:")
    logger.info(f"   ✓ Successfully Analyzed: {success_count}")
    logger.info(f"   ⏭️  Skipped (No Earnings/Data): {skip_count}")
    logger.info(f"   ✗ Errors: {error_count}")

    # Tradeable opportunities
    tradeable = [r for r in results if r.get('is_tradeable', False)]
    if tradeable:
        logger.info(f"\n" + "=" * 80)
        logger.info(f"✅ RESULT: {len(tradeable)} TRADEABLE OPPORTUNITIES FOUND")
        logger.info("=" * 80)
        logger.info(f"\n🎯 Ranked by VRP Ratio:")
        for i, r in enumerate(sorted(tradeable, key=lambda x: x['vrp_ratio'], reverse=True), 1):
            logger.info(
                f"   {i}. {r['ticker']:6s}: "
                f"VRP {r['vrp_ratio']:.2f}x | "
                f"Edge {r['edge_score']:.2f} | "
                f"{r['recommendation'].upper()} | "
                f"Earnings {r['earnings_date']}"
            )
        logger.info(f"\n📝 Next Steps:")
        logger.info(f"   1. Analyze top opportunities with: ./trade.sh TICKER YYYY-MM-DD --strategies")
        logger.info(f"   2. Review detailed strategy recommendations")
        logger.info(f"   3. Prioritize by VRP ratio and edge score")
        logger.info(f"   4. Verify earnings dates and check broker pricing")
    else:
        logger.info(f"\n" + "=" * 80)
        logger.info("⏭️  RESULT: NO TRADEABLE OPPORTUNITIES")
        logger.info("=" * 80)
        logger.info(f"\n❌ No opportunities found among {len(tickers)} ticker(s)")
        if skip_count > 0:
            logger.info(f"   Note: {skip_count} ticker(s) had no upcoming earnings or insufficient data")
        logger.info(f"\n📝 Recommendation:")
        logger.info(f"   Try different tickers or use whisper mode for most anticipated earnings")

    return 0 if error_count == 0 else 1


def whisper_mode(
    container: Container,
    week_monday: Optional[str] = None,
    fallback_image: Optional[str] = None,
    expiration_offset: Optional[int] = None
) -> int:
    """
    Whisper mode: Analyze most anticipated earnings.

    Fetches tickers from Reddit and analyzes each with auto-backfill.

    Args:
        container: DI container
        week_monday: Monday in YYYY-MM-DD (defaults to current week)
        fallback_image: Path to earnings screenshot (PNG/JPG)
        expiration_offset: Custom expiration offset in days

    Returns:
        Exit code (0 = success, 1 = error)
    """
    logger.info("=" * 80)
    logger.info("WHISPER MODE: Most Anticipated Earnings")
    logger.info("=" * 80)

    if week_monday:
        try:
            target_date = datetime.strptime(week_monday, "%Y-%m-%d")
            monday = get_week_monday(target_date)
        except ValueError:
            logger.error(f"Invalid date: {week_monday}. Use YYYY-MM-DD")
            return 1
    else:
        monday = get_week_monday()

    logger.info(f"Week: {monday.strftime('%Y-%m-%d')}")
    if fallback_image:
        logger.info(f"Fallback: {fallback_image}")
    logger.info("")

    logger.info("Fetching ticker list...")
    scraper = EarningsWhisperScraper()
    result = scraper.get_most_anticipated_earnings(
        week_monday=monday.strftime("%Y-%m-%d"),
        fallback_image=fallback_image
    )

    if result.is_err:
        logger.error(f"Failed to fetch ticker list: {result.error}")
        return 1

    tickers = result.value
    logger.info(f"✓ Retrieved {len(tickers)} most anticipated tickers")
    logger.info(f"Tickers: {', '.join(tickers)}")
    logger.info("")

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

        # Analyze ticker (with auto-backfill enabled like ticker mode)
        result = analyze_ticker(
            container,
            ticker,
            earnings_date,
            expiration_date,
            auto_backfill=True
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
    logger.info("WHISPER MODE - SUMMARY")
    logger.info("=" * 80)
    logger.info(f"\n🔊 Most Anticipated Earnings Analysis:")
    logger.info(f"   Mode: Earnings Whispers (Reddit r/EarningsWhispers)")
    logger.info(f"   Week: {monday.strftime('%Y-%m-%d')}")
    logger.info(f"   Total Tickers: {len(tickers)}")
    logger.info(f"\n📊 Analysis Results:")
    logger.info(f"   ✓ Successfully Analyzed: {success_count}")
    logger.info(f"   ⏭️  Skipped (No Earnings/Data): {skip_count}")
    logger.info(f"   ✗ Errors: {error_count}")

    # Tradeable opportunities
    tradeable = [r for r in results if r.get('is_tradeable', False)]
    if tradeable:
        logger.info(f"\n" + "=" * 80)
        logger.info(f"✅ RESULT: {len(tradeable)} TRADEABLE OPPORTUNITIES FOUND")
        logger.info("=" * 80)
        logger.info(f"\n🎯 Most Anticipated + High VRP (Ranked by VRP Ratio):")
        for i, r in enumerate(sorted(tradeable, key=lambda x: x['vrp_ratio'], reverse=True), 1):
            logger.info(
                f"   {i}. {r['ticker']:6s}: "
                f"VRP {r['vrp_ratio']:.2f}x | "
                f"Edge {r['edge_score']:.2f} | "
                f"{r['recommendation'].upper()} | "
                f"Earnings {r['earnings_date']}"
            )
        logger.info(f"\n💡 Why This Matters:")
        logger.info(f"   These tickers combine:")
        logger.info(f"   • High retail/market attention (Most Anticipated)")
        logger.info(f"   • Strong statistical edge (VRP ratio)")
        logger.info(f"   • Better liquidity expected (High volume)")
        logger.info(f"\n📝 Next Steps:")
        logger.info(f"   1. Analyze top opportunities with: ./trade.sh TICKER YYYY-MM-DD --strategies")
        logger.info(f"   2. Review detailed strategy recommendations")
        logger.info(f"   3. Prioritize by VRP ratio and market attention")
        logger.info(f"   4. Check broker for tight bid-ask spreads")
    else:
        logger.info(f"\n" + "=" * 80)
        logger.info("⏭️  RESULT: NO TRADEABLE OPPORTUNITIES")
        logger.info("=" * 80)
        logger.info(f"\n❌ No opportunities found among most anticipated earnings")
        if skip_count > 0:
            logger.info(f"   Note: {skip_count} ticker(s) had no upcoming earnings or insufficient data")
        logger.info(f"\n📝 Recommendation:")
        logger.info(f"   High market attention doesn't always mean high VRP")
        logger.info(f"   Try: ./trade.sh scan YYYY-MM-DD for broader earnings scan")

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

    # Whisper mode - analyze most anticipated earnings (current week)
    python scripts/scan.py --whisper-week

    # Whisper mode - specific week (Monday)
    python scripts/scan.py --whisper-week 2025-11-10

    # Whisper mode with image fallback
    python scripts/scan.py --whisper-week --fallback-image data/earnings.png

    # Ticker mode with custom expiration offset
    python scripts/scan.py --tickers AAPL --expiration-offset 1

Expiration Date Calculation:
    - BMO (before market open): Same day if Friday, otherwise next Friday
    - AMC (after market close): Next day if Thursday, otherwise next Friday
    - UNKNOWN: Next Friday (conservative)
    - Custom offset: earnings_date + offset_days

Notes:
    - Requires TRADIER_API_KEY and ALPHA_VANTAGE_API_KEY in .env
    - Whisper mode auto-backfills historical data (like ticker mode)
    - Historical data is backfilled automatically for VRP calculation
    - Run: python scripts/backfill.py <TICKER> to manually backfill data
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
    mode_group.add_argument(
        "--whisper-week",
        nargs='?',
        const='',
        type=str,
        help="Analyze most anticipated earnings for week (optional: YYYY-MM-DD for Monday, defaults to current week)"
    )

    # Options
    parser.add_argument(
        "--expiration-offset",
        type=int,
        help="Custom expiration offset in days from earnings date (overrides auto-calculation)"
    )
    parser.add_argument(
        "--fallback-image",
        type=str,
        help="Path to earnings screenshot (PNG/JPG) for whisper mode fallback"
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
        elif args.whisper_week is not None:
            # whisper_week can be '' (empty string) for current week or a date string
            week_monday = args.whisper_week if args.whisper_week else None
            return whisper_mode(
                container,
                week_monday=week_monday,
                fallback_image=args.fallback_image,
                expiration_offset=args.expiration_offset
            )
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
