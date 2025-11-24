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
from tqdm import tqdm
import atexit

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.utils.shutdown import register_shutdown_callback
from src.container import Container, reset_container
from src.config.config import Config
from src.domain.enums import EarningsTiming
from src.infrastructure.data_sources.earnings_whisper_scraper import (
    EarningsWhisperScraper,
    get_week_monday
)
from src.infrastructure.cache.hybrid_cache import HybridCache

# Try to import yfinance for market cap data
try:
    import yfinance as yf
    YFINANCE_AVAILABLE = True
except ImportError:
    YFINANCE_AVAILABLE = False

logger = logging.getLogger(__name__)

# Alpha Vantage free tier rate limits
ALPHA_VANTAGE_CALLS_PER_MINUTE = 5
RATE_LIMIT_PAUSE_SECONDS = 60

# Cache configuration
CACHE_L1_TTL_SECONDS = 3600      # 1 hour in-memory cache
CACHE_L2_TTL_SECONDS = 518400    # 6 days persistent cache (until next Monday)
CACHE_MAX_L1_SIZE = 100          # Max items in L1 memory cache

# Backfill configuration
BACKFILL_TIMEOUT_SECONDS = 120   # 2 minutes timeout for backfill subprocess
BACKFILL_YEARS = 3               # Years of historical data to backfill

# Trading day adjustment
MAX_TRADING_DAY_ITERATIONS = 10  # Max iterations to find next trading day (handles holiday clusters)


class ScanContext:
    """
    Encapsulates scan session state (replaces module-level globals).

    Holds caches and session-specific state that was previously in
    module-level variables. This makes the code more testable and
    eliminates hidden global state.

    Attributes:
        market_cap_cache: Cache for market cap lookups
        holiday_cache: Cache for holiday data by year
        shared_cache: Shared HybridCache instance for earnings data
    """

    def __init__(self):
        """Initialize scan context with empty caches."""
        self.market_cap_cache: Dict[str, Optional[float]] = {}
        self.holiday_cache: Dict[int, set] = {}
        self.shared_cache: Optional[HybridCache] = None

    def get_shared_cache(self, container: Container) -> HybridCache:
        """
        Get or create a shared cache instance for earnings data.

        Args:
            container: DI container for config access

        Returns:
            Shared HybridCache instance
        """
        if self.shared_cache is None:
            cache_db_path = container.config.database.path.parent / "scan_cache.db"
            self.shared_cache = HybridCache(
                db_path=cache_db_path,
                l1_ttl_seconds=CACHE_L1_TTL_SECONDS,
                l2_ttl_seconds=CACHE_L2_TTL_SECONDS,
                max_l1_size=CACHE_MAX_L1_SIZE
            )
        return self.shared_cache


# Legacy global variables (DEPRECATED - use ScanContext instead)
# Kept for backward compatibility during transition
_market_cap_cache: Dict[str, Optional[float]] = {}
_holiday_cache: Dict[int, set] = {}
_shared_cache: Optional[HybridCache] = None


def get_shared_cache(container: Container) -> HybridCache:
    """
    Get or create a shared cache instance for earnings data.

    This cache is shared between ticker_mode and whisper_mode to avoid
    duplicate API calls and maintain consistent data across modes.

    Args:
        container: DI container for config access

    Returns:
        Shared HybridCache instance
    """
    global _shared_cache

    if _shared_cache is None:
        cache_db_path = container.config.database.path.parent / "scan_cache.db"
        _shared_cache = HybridCache(
            db_path=cache_db_path,
            l1_ttl_seconds=CACHE_L1_TTL_SECONDS,
            l2_ttl_seconds=CACHE_L2_TTL_SECONDS,
            max_l1_size=CACHE_MAX_L1_SIZE
        )

    return _shared_cache


def get_market_cap_millions(ticker: str) -> Optional[float]:
    """
    Get market cap in millions using yfinance (with caching).

    Args:
        ticker: Stock ticker symbol

    Returns:
        Market cap in millions or None if unavailable
    """
    if not YFINANCE_AVAILABLE:
        logger.debug(f"yfinance not available, skipping market cap check for {ticker}")
        return None

    # Check cache first
    if ticker in _market_cap_cache:
        cached_value = _market_cap_cache[ticker]
        logger.debug(f"{ticker}: Market cap from cache: {cached_value}")
        return cached_value

    try:
        stock = yf.Ticker(ticker)
        info = stock.info

        # Get market cap (in dollars)
        market_cap = info.get('marketCap')
        if market_cap and market_cap > 0:
            market_cap_millions = market_cap / 1_000_000
            logger.debug(f"{ticker}: Market cap ${market_cap_millions:.0f}M")
            _market_cap_cache[ticker] = market_cap_millions
            return market_cap_millions

        logger.debug(f"{ticker}: No market cap data available")
        _market_cap_cache[ticker] = None
        return None

    except Exception as e:
        logger.debug(f"{ticker}: Failed to fetch market cap: {e}")
        _market_cap_cache[ticker] = None
        return None


def check_basic_liquidity(ticker: str, expiration: date, container: Container) -> bool:
    """
    Quick liquidity check: Do options exist with reasonable OI?

    Args:
        ticker: Stock ticker symbol
        expiration: Options expiration date
        container: DI container for Tradier access

    Returns:
        True if liquidity is acceptable, False otherwise
    """
    try:
        # Get option chain
        tradier = container.tradier
        chain_result = tradier.get_option_chain(ticker, expiration)

        if chain_result.is_err:
            logger.debug(f"{ticker}: No option chain available")
            return False

        chain = chain_result.value

        # Check if we have any options with decent OI
        min_oi = container.config.thresholds.min_open_interest

        # Check both calls and puts
        total_options = len(chain.calls) + len(chain.puts)
        if total_options == 0:
            logger.debug(f"{ticker}: No options found")
            return False

        # Check if at least some options have acceptable OI
        # chain.calls and chain.puts are Dict[Strike, OptionQuote]
        acceptable_calls = sum(1 for strike, opt in chain.calls.items() if opt.open_interest >= min_oi)
        acceptable_puts = sum(1 for strike, opt in chain.puts.items() if opt.open_interest >= min_oi)

        if acceptable_calls == 0 or acceptable_puts == 0:
            logger.debug(f"{ticker}: Insufficient open interest (min {min_oi})")
            return False

        logger.debug(f"{ticker}: Liquidity OK ({acceptable_calls} calls, {acceptable_puts} puts with OI>={min_oi})")
        return True

    except Exception as e:
        logger.debug(f"{ticker}: Liquidity check failed: {e}")
        return False


def should_filter_ticker(
    ticker: str,
    expiration: date,
    container: Container,
    check_market_cap: bool = True,
    check_liquidity: bool = True
) -> Tuple[bool, Optional[str]]:
    """
    Determine if ticker should be filtered out based on market cap and liquidity.

    Args:
        ticker: Stock ticker symbol
        expiration: Options expiration date
        container: DI container
        check_market_cap: Whether to check market cap threshold
        check_liquidity: Whether to check liquidity threshold

    Returns:
        (should_filter, reason) - True if should skip ticker, with reason string
    """
    # Check market cap
    if check_market_cap:
        market_cap_millions = get_market_cap_millions(ticker)
        if market_cap_millions is not None:
            min_market_cap = container.config.thresholds.min_market_cap_millions
            if market_cap_millions < min_market_cap:
                return (True, f"Market cap ${market_cap_millions:.0f}M < ${min_market_cap:.0f}M")

    # Check liquidity
    if check_liquidity:
        has_liquidity = check_basic_liquidity(ticker, expiration, container)
        if not has_liquidity:
            return (True, "Insufficient liquidity")

    return (False, None)


def parse_date(date_str: str) -> date:
    """Parse date string in ISO format."""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError as e:
        raise ValueError(f"Invalid date format: {date_str}. Use YYYY-MM-DD")


def get_us_market_holidays(year: int) -> set:
    """
    Get US stock market holidays for a given year (with caching).

    Returns fixed-date holidays and approximations for floating holidays.
    Note: Good Friday requires Easter calculation which is complex,
    so it's omitted here. For production, consider using a library like
    pandas_market_calendars or exchange_calendars.

    Args:
        year: The year to get holidays for

    Returns:
        Set of date objects representing market holidays
    """
    # Check cache first
    if year in _holiday_cache:
        return _holiday_cache[year]

    holidays = set()

    # Fixed holidays
    # New Year's Day (Jan 1)
    new_years = date(year, 1, 1)
    if new_years.weekday() == 5:  # Saturday -> observed Friday
        holidays.add(date(year - 1, 12, 31))
    elif new_years.weekday() == 6:  # Sunday -> observed Monday
        holidays.add(date(year, 1, 2))
    else:
        holidays.add(new_years)

    # Juneteenth (June 19) - observed since 2021
    if year >= 2021:
        juneteenth = date(year, 6, 19)
        if juneteenth.weekday() == 5:
            holidays.add(date(year, 6, 18))
        elif juneteenth.weekday() == 6:
            holidays.add(date(year, 6, 20))
        else:
            holidays.add(juneteenth)

    # Independence Day (July 4)
    july_4th = date(year, 7, 4)
    if july_4th.weekday() == 5:
        holidays.add(date(year, 7, 3))
    elif july_4th.weekday() == 6:
        holidays.add(date(year, 7, 5))
    else:
        holidays.add(july_4th)

    # Christmas Day (Dec 25)
    christmas = date(year, 12, 25)
    if christmas.weekday() == 5:
        holidays.add(date(year, 12, 24))
    elif christmas.weekday() == 6:
        holidays.add(date(year, 12, 26))
    else:
        holidays.add(christmas)

    # Floating holidays (approximations)
    # MLK Day (3rd Monday of January)
    jan_first = date(year, 1, 1)
    days_to_monday = (7 - jan_first.weekday()) % 7
    first_monday = jan_first + timedelta(days=days_to_monday)
    mlk_day = first_monday + timedelta(weeks=2)
    holidays.add(mlk_day)

    # Presidents' Day (3rd Monday of February)
    feb_first = date(year, 2, 1)
    days_to_monday = (7 - feb_first.weekday()) % 7
    first_monday = feb_first + timedelta(days=days_to_monday)
    presidents_day = first_monday + timedelta(weeks=2)
    holidays.add(presidents_day)

    # Memorial Day (last Monday of May)
    may_last = date(year, 5, 31)
    days_since_monday = may_last.weekday()
    memorial_day = may_last - timedelta(days=days_since_monday)
    holidays.add(memorial_day)

    # Labor Day (1st Monday of September)
    sep_first = date(year, 9, 1)
    days_to_monday = (7 - sep_first.weekday()) % 7
    labor_day = sep_first + timedelta(days=days_to_monday)
    holidays.add(labor_day)

    # Thanksgiving (4th Thursday of November)
    nov_first = date(year, 11, 1)
    days_to_thursday = (3 - nov_first.weekday()) % 7
    first_thursday = nov_first + timedelta(days=days_to_thursday)
    thanksgiving = first_thursday + timedelta(weeks=3)
    holidays.add(thanksgiving)

    # Cache the result
    _holiday_cache[year] = holidays
    return holidays


def is_market_holiday(target_date: date) -> bool:
    """
    Check if a date is a US stock market holiday.

    Args:
        target_date: Date to check

    Returns:
        True if the date is a market holiday
    """
    holidays = get_us_market_holidays(target_date.year)
    return target_date in holidays


def adjust_to_trading_day(target_date: date) -> date:
    """
    Adjust date to next trading day if on weekend or holiday.

    Args:
        target_date: Target date to check

    Returns:
        Next trading day (skips weekends and US market holidays)
    """
    adjusted = target_date

    # Keep adjusting until we find a trading day
    for _ in range(MAX_TRADING_DAY_ITERATIONS):
        weekday = adjusted.weekday()

        # Skip weekends
        if weekday == 5:  # Saturday -> Monday
            adjusted = adjusted + timedelta(days=2)
            continue
        elif weekday == 6:  # Sunday -> Monday
            adjusted = adjusted + timedelta(days=1)
            continue

        # Skip market holidays
        if is_market_holiday(adjusted):
            adjusted = adjusted + timedelta(days=1)
            continue

        # Found a trading day
        break

    return adjusted


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
    ticker: str,
    cached_calendar: Optional[List[Tuple[str, date, EarningsTiming]]] = None
) -> Optional[Tuple[date, EarningsTiming]]:
    """
    Fetch earnings date for a specific ticker.

    Args:
        container: DI container
        ticker: Stock ticker symbol
        cached_calendar: Optional pre-fetched full calendar to filter from

    Returns:
        (earnings_date, timing) tuple or None if not found
    """
    # If we have a cached full calendar, filter it locally (much faster)
    if cached_calendar is not None:
        ticker_earnings = [
            (sym, dt, timing) for sym, dt, timing in cached_calendar
            if sym == ticker
        ]
        if ticker_earnings:
            # Sort by date and get nearest
            ticker_earnings.sort(key=lambda x: x[1])
            ticker_symbol, earnings_date, timing = ticker_earnings[0]
            logger.info(f"{ticker}: Earnings on {earnings_date} ({timing.value})")
            return (earnings_date, timing)
        else:
            logger.warning(f"No upcoming earnings found for {ticker}")
            return None

    # Fallback: individual API call (slower, more API calls)
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
                start_date = (date.today() - timedelta(days=BACKFILL_YEARS*365)).isoformat()
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
                        timeout=BACKFILL_TIMEOUT_SECONDS
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
    filtered_count = 0

    # Progress bar for scanning (force real-time updates)
    pbar = tqdm(
        earnings_events,
        desc="Scanning earnings",
        unit="ticker",
        bar_format='{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}] {postfix}',
        file=sys.stderr,  # Use stderr to avoid interfering with output capture
        mininterval=0.1,  # Update every 0.1 seconds
        maxinterval=1.0   # Maximum 1 second between updates
    )

    for ticker, earnings_date, timing in pbar:
        pbar.set_postfix_str(f"Current: {ticker}")
        sys.stderr.flush()  # Force flush after each update

        # Calculate expiration date
        expiration_date = calculate_expiration_date(
            earnings_date, timing, expiration_offset
        )

        # Apply filters (market cap + liquidity) for scan mode
        should_filter, filter_reason = should_filter_ticker(
            ticker, expiration_date, container,
            check_market_cap=True,
            check_liquidity=True
        )

        if should_filter:
            filtered_count += 1
            logger.info(f"⏭️  {ticker}: Filtered ({filter_reason})")
            pbar.set_postfix_str(f"{ticker}: Filtered")
            sys.stderr.flush()
            continue

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
                pbar.set_postfix_str(f"{ticker}: ✓ Complete")
            else:
                skip_count += 1
                pbar.set_postfix_str(f"{ticker}: No data")
        else:
            error_count += 1
            pbar.set_postfix_str(f"{ticker}: ✗ Error")
        sys.stderr.flush()

    pbar.close()

    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("SCAN MODE - SUMMARY")
    logger.info("=" * 80)
    logger.info(f"\n📅 Scan Details:")
    logger.info(f"   Mode: Earnings Date Scan")
    logger.info(f"   Date: {scan_date}")
    logger.info(f"   Total Earnings Found: {len(earnings_events)}")
    logger.info(f"\n📊 Analysis Results:")
    logger.info(f"   🔍 Filtered (Market Cap/Liquidity): {filtered_count}")
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
                f"Implied {r['implied_move_pct']} | "
                f"Edge {r['edge_score']:.2f} | "
                f"{r['recommendation'].upper()}"
            )
        logger.info(f"\n💡 Run './trade.sh TICKER YYYY-MM-DD' for detailed strategy recommendations")
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

    # Return 0 if we successfully completed the scan (even if some tickers had errors)
    # Only return 1 for fatal errors (calendar fetch failure, etc.)
    return 0


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
    logger.info("")

    # Use shared cache for earnings data
    shared_cache = get_shared_cache(container)

    # Fetch full earnings calendar ONCE and cache it
    logger.info("Fetching full earnings calendar...")
    cache_key = f"earnings_calendar:{date.today().isoformat()}"
    full_calendar = shared_cache.get(cache_key)

    if full_calendar is None:
        logger.info("Cache MISS - fetching from Alpha Vantage...")
        calendar_result = container.alphavantage.get_earnings_calendar(horizon="3month")
        if calendar_result.is_err:
            logger.error(f"Failed to fetch earnings calendar: {calendar_result.error}")
            return 1
        full_calendar = calendar_result.value
        shared_cache.set(cache_key, full_calendar)
        logger.info(f"✓ Fetched {len(full_calendar)} total earnings events (cached)")
    else:
        logger.info(f"✓ Cache HIT - using cached calendar ({len(full_calendar)} events)")

    # Analyze each ticker
    results = []
    success_count = 0
    error_count = 0
    skip_count = 0
    filtered_count = 0

    # Progress bar for ticker processing (force real-time updates)
    pbar = tqdm(
        tickers,
        desc="Analyzing tickers",
        unit="ticker",
        bar_format='{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}] {postfix}',
        file=sys.stderr,  # Use stderr to avoid interfering with output capture
        mininterval=0.1,  # Update every 0.1 seconds
        maxinterval=1.0   # Maximum 1 second between updates
    )

    for ticker in pbar:
        pbar.set_postfix_str(f"Current: {ticker}")
        sys.stderr.flush()  # Force flush after each update

        # Fetch earnings date for ticker (using cached full calendar - no API call!)
        earnings_info = fetch_earnings_for_ticker(container, ticker, cached_calendar=full_calendar)

        if not earnings_info:
            skip_count += 1
            pbar.set_postfix_str(f"{ticker}: No earnings")
            sys.stderr.flush()
            continue

        earnings_date, timing = earnings_info

        # Calculate expiration date
        expiration_date = calculate_expiration_date(
            earnings_date, timing, expiration_offset
        )

        # Apply filters (market cap + liquidity) for list mode
        should_filter, filter_reason = should_filter_ticker(
            ticker, expiration_date, container,
            check_market_cap=True,
            check_liquidity=True
        )

        if should_filter:
            filtered_count += 1
            logger.info(f"⏭️  {ticker}: Filtered ({filter_reason})")
            pbar.set_postfix_str(f"{ticker}: Filtered")
            sys.stderr.flush()
            continue

        # Update progress
        pbar.set_postfix_str(f"{ticker}: Analyzing VRP")
        sys.stderr.flush()

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
                pbar.set_postfix_str(f"{ticker}: ✓ Complete")
            else:
                skip_count += 1
                pbar.set_postfix_str(f"{ticker}: Skipped")
        else:
            error_count += 1
            pbar.set_postfix_str(f"{ticker}: ✗ Error")
        sys.stderr.flush()

    pbar.close()

    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("LIST MODE - SUMMARY")
    logger.info("=" * 80)
    logger.info(f"\n📋 Ticker List Analysis:")
    logger.info(f"   Mode: Multiple Ticker Analysis")
    logger.info(f"   Tickers Requested: {len(tickers)}")
    logger.info(f"   Tickers Analyzed: {', '.join(tickers)}")
    logger.info(f"\n📊 Analysis Results:")
    logger.info(f"   🔍 Filtered (Market Cap/Liquidity): {filtered_count}")
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
                f"Implied {r['implied_move_pct']} | "
                f"Edge {r['edge_score']:.2f} | "
                f"{r['recommendation'].upper()} | "
                f"Earnings {r['earnings_date']}"
            )
        logger.info(f"\n💡 Run './trade.sh TICKER YYYY-MM-DD' for detailed strategy recommendations")
    else:
        logger.info(f"\n" + "=" * 80)
        logger.info("⏭️  RESULT: NO TRADEABLE OPPORTUNITIES")
        logger.info("=" * 80)
        logger.info(f"\n❌ No opportunities found among {len(tickers)} ticker(s)")
        if skip_count > 0:
            logger.info(f"   Note: {skip_count} ticker(s) had no upcoming earnings or insufficient data")
        logger.info(f"\n📝 Recommendation:")
        logger.info(f"   Try different tickers or use whisper mode for most anticipated earnings")

    # Return 0 if we successfully completed the scan (even if some tickers had errors)
    # Only return 1 for fatal errors (calendar fetch failure, etc.)
    return 0


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

    # Calculate week range (Monday to Sunday)
    week_end = monday + timedelta(days=6)
    logger.info(f"Week: {monday.strftime('%Y-%m-%d')} to {week_end.strftime('%Y-%m-%d')}")
    if fallback_image:
        logger.info(f"Fallback: {fallback_image}")
    logger.info("")

    # Use shared cache for earnings data
    shared_cache = get_shared_cache(container)

    logger.info("Fetching ticker list...")
    scraper = EarningsWhisperScraper(cache=shared_cache)
    result = scraper.get_most_anticipated_earnings(
        week_monday=monday.strftime("%Y-%m-%d"),
        fallback_image=fallback_image
    )

    if result.is_err:
        logger.error(f"Failed to fetch ticker list: {result.error}")
        return 1

    tickers = result.value

    # Validate we got some tickers
    if not tickers:
        logger.warning("⚠️  No tickers retrieved from Earnings Whispers")
        logger.info("   This may indicate:")
        logger.info("   - Reddit API rate limiting")
        logger.info("   - No anticipated earnings for this week")
        logger.info("   - Network connectivity issues")
        logger.info("")
        logger.info("📝 Try:")
        logger.info("   - Use a different week: ./trade.sh whisper 2025-11-17")
        logger.info("   - Use scan mode: ./trade.sh scan 2025-11-20")
        return 1

    logger.info(f"✓ Retrieved {len(tickers)} most anticipated tickers")
    logger.info(f"Tickers: {', '.join(tickers)}")
    logger.info("")

    # Fetch full earnings calendar ONCE and cache it
    logger.info("Fetching full earnings calendar...")
    cache_key = f"earnings_calendar:{date.today().isoformat()}"
    full_calendar = shared_cache.get(cache_key)

    if full_calendar is None:
        logger.info("Cache MISS - fetching from Alpha Vantage...")
        calendar_result = container.alphavantage.get_earnings_calendar(horizon="3month")
        if calendar_result.is_err:
            logger.error(f"Failed to fetch earnings calendar: {calendar_result.error}")
            return 1
        full_calendar = calendar_result.value
        shared_cache.set(cache_key, full_calendar)
        logger.info(f"✓ Fetched {len(full_calendar)} total earnings events (cached)")
    else:
        logger.info(f"✓ Cache HIT - using cached calendar ({len(full_calendar)} events)")

    # Analyze each ticker
    results = []
    success_count = 0
    error_count = 0
    skip_count = 0
    filtered_count = 0

    # Progress bar for ticker processing (force real-time updates)
    pbar = tqdm(
        tickers,
        desc="Analyzing tickers",
        unit="ticker",
        bar_format='{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}] {postfix}',
        file=sys.stderr,  # Use stderr to avoid interfering with output capture
        mininterval=0.1,  # Update every 0.1 seconds
        maxinterval=1.0   # Maximum 1 second between updates
    )

    for ticker in pbar:
        pbar.set_postfix_str(f"Current: {ticker}")
        sys.stderr.flush()  # Force flush after each update

        # Fetch earnings date for ticker (using cached full calendar - no API call!)
        earnings_info = fetch_earnings_for_ticker(container, ticker, cached_calendar=full_calendar)

        if not earnings_info:
            skip_count += 1
            pbar.set_postfix_str(f"{ticker}: No earnings")
            sys.stderr.flush()
            continue

        earnings_date, timing = earnings_info

        # Check if earnings date is within target week
        if not (monday.date() <= earnings_date <= week_end.date()):
            skip_count += 1
            logger.info(f"⏭️  {ticker}: Earnings {earnings_date} outside target week ({monday.date()} to {week_end.date()})")
            pbar.set_postfix_str(f"{ticker}: Outside week")
            sys.stderr.flush()
            continue

        # Calculate expiration date
        expiration_date = calculate_expiration_date(
            earnings_date, timing, expiration_offset
        )

        # Apply filters (market cap + liquidity) for whisper mode
        should_filter, filter_reason = should_filter_ticker(
            ticker, expiration_date, container,
            check_market_cap=True,
            check_liquidity=True
        )

        if should_filter:
            filtered_count += 1
            logger.info(f"⏭️  {ticker}: Filtered ({filter_reason})")
            pbar.set_postfix_str(f"{ticker}: Filtered")
            sys.stderr.flush()
            continue

        # Update progress with current action
        pbar.set_postfix_str(f"{ticker}: Analyzing VRP")
        sys.stderr.flush()

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
                pbar.set_postfix_str(f"{ticker}: ✓ Complete")
            else:
                skip_count += 1
                pbar.set_postfix_str(f"{ticker}: Skipped")
        else:
            error_count += 1
            pbar.set_postfix_str(f"{ticker}: ✗ Error")
        sys.stderr.flush()

    pbar.close()

    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("WHISPER MODE - SUMMARY")
    logger.info("=" * 80)
    logger.info(f"\n🔊 Most Anticipated Earnings Analysis:")
    logger.info(f"   Mode: Earnings Whispers (Reddit r/EarningsWhispers)")
    logger.info(f"   Week: {monday.strftime('%Y-%m-%d')} to {week_end.strftime('%Y-%m-%d')}")
    logger.info(f"   Total Tickers: {len(tickers)}")
    logger.info(f"\n📊 Analysis Results:")
    logger.info(f"   🔍 Filtered (Market Cap/Liquidity): {filtered_count}")
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
                f"Implied {r['implied_move_pct']} | "
                f"Edge {r['edge_score']:.2f} | "
                f"{r['recommendation'].upper()} | "
                f"Earnings {r['earnings_date']}"
            )
        logger.info(f"\n💡 Run './trade.sh TICKER YYYY-MM-DD' for detailed strategy recommendations")
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

    # Return 0 if we successfully completed the scan (even if some tickers had errors)
    # Only return 1 for fatal errors (calendar fetch failure, etc.)
    return 0


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

        # Register graceful shutdown callbacks
        def shutdown_cleanup():
            """Cleanup on shutdown."""
            reset_container()

        register_shutdown_callback(shutdown_cleanup)

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
