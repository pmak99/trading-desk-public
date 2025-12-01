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
import re
import time
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from tqdm import tqdm
import atexit
import threading

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.utils.shutdown import register_shutdown_callback
from src.container import Container, reset_container
from src.config.config import Config
from src.domain.enums import EarningsTiming
from src.domain.liquidity import (
    analyze_chain_liquidity,
    LiquidityTier
)
from src.infrastructure.data_sources.earnings_whisper_scraper import (
    EarningsWhisperScraper,
    get_week_monday
)
from src.infrastructure.cache.hybrid_cache import HybridCache

logger = logging.getLogger(__name__)

# Lazy import yfinance only when needed
YFINANCE_AVAILABLE = False
yf = None

def _ensure_yfinance():
    """Lazy load yfinance module."""
    global YFINANCE_AVAILABLE, yf
    if yf is None:
        try:
            import yfinance as yf_module
            yf = yf_module
            YFINANCE_AVAILABLE = True
        except ImportError:
            YFINANCE_AVAILABLE = False
    return YFINANCE_AVAILABLE

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

# API rate limiting
API_CALL_DELAY = 0.2             # Delay between API calls to respect rate limits

# Pre-compiled regex patterns for company name cleaning (performance optimization)
_COMPANY_SUFFIX_PATTERNS = [
    re.compile(pattern, re.IGNORECASE) for pattern in [
        r',?\s+Inc\.?$',
        r',?\s+Incorporated$',
        r',?\s+Corp\.?$',
        r',?\s+Corporation$',
        r',?\s+Ltd\.?$',
        r',?\s+Limited$',
        r',?\s+LLC$',
        r',?\s+L\.L\.C\.?$',
        r',?\s+Co\.?$',
        r',?\s+Company$',
        r',?\s+PLC$',
        r',?\s+P\.L\.C\.?$',
        r',?\s+Plc$',
        r',?\s+LP$',
        r',?\s+L\.P\.?$',
    ]
]
_TRAILING_AMPERSAND_PATTERN = re.compile(r'\s*&\s*$')

# Module-level caches and state
_ticker_info_cache: Dict[str, Tuple[Optional[float], Optional[str]]] = {}  # Combined cache for market cap + name
_liquidity_cache: Dict[Tuple[str, date], Tuple[bool, str]] = {}  # Cache for liquidity checks (ticker, expiration) -> (has_liq, tier)
_holiday_cache: Dict[int, set] = {}
_shared_cache: Optional[HybridCache] = None
_api_call_lock = threading.Lock()  # Thread-safe API rate limiting


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


def clean_company_name(name: str) -> str:
    """
    Clean company name by removing formal suffixes for colloquial display.
    Uses pre-compiled regex patterns for performance.

    Args:
        name: Full company name

    Returns:
        Cleaned colloquial name

    Examples:
        "Apple Inc." -> "Apple"
        "Tesla, Inc." -> "Tesla"
        "NVIDIA Corporation" -> "NVIDIA"
        "Meta Platforms, Inc." -> "Meta Platforms"
    """
    cleaned = name
    for pattern in _COMPANY_SUFFIX_PATTERNS:
        cleaned = pattern.sub('', cleaned)

    # Remove trailing ampersand left by "& Co." removal
    cleaned = _TRAILING_AMPERSAND_PATTERN.sub('', cleaned)

    return cleaned.strip()


def get_ticker_info(ticker: str) -> Tuple[Optional[float], Optional[str]]:
    """
    Get market cap (in millions) and company name in a single API call (OPTIMIZED).

    This combines get_market_cap_millions() and get_ticker_name() to reduce
    API calls by 50% and improve performance.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Tuple of (market_cap_millions, company_name) or (None, None) if unavailable
    """
    if not _ensure_yfinance():
        logger.debug(f"{ticker}: yfinance not available, skipping ticker info lookup")
        return (None, None)

    # Check cache first
    if ticker in _ticker_info_cache:
        cached_value = _ticker_info_cache[ticker]
        logger.debug(f"{ticker}: Ticker info from cache: market_cap={cached_value[0]}, name={cached_value[1]}")
        return cached_value

    try:
        # Thread-safe API rate limiting - CRITICAL: Keep lock until API call completes
        with _api_call_lock:
            time.sleep(API_CALL_DELAY)  # Respect rate limits
            stock = yf.Ticker(ticker)
            info = stock.info  # Actual API call - must complete before releasing lock

        # Process data outside lock (no API calls, safe to parallelize)
        market_cap = info.get('marketCap')
        market_cap_millions = None
        if market_cap and market_cap > 0:
            market_cap_millions = market_cap / 1_000_000
            logger.debug(f"{ticker}: Market cap ${market_cap_millions:.0f}M")
        else:
            logger.debug(f"{ticker}: No market cap data available")

        # Get company name
        company_name = info.get('shortName') or info.get('longName')
        cleaned_name = None
        if company_name:
            cleaned_name = clean_company_name(company_name)
            logger.debug(f"{ticker}: Company name: {cleaned_name} (original: {company_name})")
        else:
            logger.debug(f"{ticker}: No company name available")

        # Cache the result
        result = (market_cap_millions, cleaned_name)
        _ticker_info_cache[ticker] = result
        return result

    except Exception as e:
        logger.debug(f"{ticker}: Failed to fetch ticker info: {e}")
        result = (None, None)
        _ticker_info_cache[ticker] = result
        return result


def get_market_cap_millions(ticker: str) -> Optional[float]:
    """Get market cap in millions (convenience wrapper)."""
    market_cap, _ = get_ticker_info(ticker)
    return market_cap


def get_ticker_name(ticker: str) -> Optional[str]:
    """Get company name (convenience wrapper)."""
    _, name = get_ticker_info(ticker)
    return name


def check_liquidity_with_tier(ticker: str, expiration: date, container: Container) -> Tuple[bool, str]:
    """
    Check liquidity tier using LiquidityScorer (single source of truth).

    This is now a thin wrapper around the LiquidityScorer class, which provides
    the single source of truth for all liquidity tier classification across all modes.

    Args:
        ticker: Stock ticker symbol
        expiration: Options expiration date
        container: DI container for LiquidityScorer access

    Returns:
        Tuple of (has_liquidity: bool, tier: str)
        - has_liquidity: True if acceptable (WARNING or EXCELLENT), False if REJECT
        - tier: "EXCELLENT", "WARNING", or "REJECT"
    """
    # Check cache first
    cache_key = (ticker, expiration)
    if cache_key in _liquidity_cache:
        cached_result = _liquidity_cache[cache_key]
        logger.debug(f"{ticker}: Liquidity from cache: {cached_result[1]} tier")
        return cached_result

    try:
        # Get option chain (single API call)
        tradier = container.tradier
        chain_result = tradier.get_option_chain(ticker, expiration)

        if chain_result.is_err:
            logger.debug(f"{ticker}: No option chain available")
            result = (False, "REJECT")
            _liquidity_cache[cache_key] = result
            return result

        chain = chain_result.value

        # Get calls and puts lists
        calls_list = list(chain.calls.items())
        puts_list = list(chain.puts.items())

        if not calls_list or not puts_list:
            logger.debug(f"{ticker}: Empty option chain")
            result = (False, "REJECT")
            _liquidity_cache[cache_key] = result
            return result

        # Get midpoint options (closest to ATM)
        mid_call = calls_list[len(calls_list) // 2][1]
        mid_put = puts_list[len(puts_list) // 2][1]

        # Use LiquidityScorer to classify tier (single source of truth)
        liquidity_scorer = container.liquidity_scorer
        tier = liquidity_scorer.classify_straddle_tier(mid_call, mid_put)

        # Determine if has acceptable liquidity (WARNING or EXCELLENT = True, REJECT = False)
        has_liquidity = tier != "REJECT"

        logger.debug(f"{ticker}: {tier} liquidity tier (call OI={mid_call.open_interest}, put OI={mid_put.open_interest}, "
                    f"call vol={mid_call.volume}, put vol={mid_put.volume}, "
                    f"call spread={mid_call.spread_pct:.1f}%, put spread={mid_put.spread_pct:.1f}%)")

        result = (has_liquidity, tier)
        _liquidity_cache[cache_key] = result
        return result

    except Exception as e:
        logger.debug(f"{ticker}: Liquidity check failed: {e}")
        result = (False, "WARNING")
        _liquidity_cache[cache_key] = result
        return result


def check_basic_liquidity(ticker: str, expiration: date, container: Container) -> bool:
    """Quick liquidity check (convenience wrapper)."""
    has_liquidity, _ = check_liquidity_with_tier(ticker, expiration, container)
    return has_liquidity


def get_liquidity_tier_for_display(ticker: str, expiration: date, container: Container) -> str:
    """Get liquidity tier (convenience wrapper)."""
    _, tier = check_liquidity_with_tier(ticker, expiration, container)
    return tier


def should_filter_ticker(
    ticker: str,
    expiration: date,
    container: Container,
    check_market_cap: bool = True,
    check_liquidity: bool = True
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Determine if ticker should be filtered out based on market cap (LIQUIDITY NO LONGER FILTERS).

    IMPORTANT: Liquidity tier is checked and returned for display purposes, but does NOT
    cause filtering. All tradeable opportunities are shown regardless of liquidity tier,
    with appropriate warnings in the output.

    Args:
        ticker: Stock ticker symbol
        expiration: Options expiration date
        container: DI container
        check_market_cap: Whether to check market cap threshold
        check_liquidity: Whether to check liquidity tier (for display only, doesn't filter)

    Returns:
        (should_filter, reason, liquidity_tier) - True if should skip ticker, with reason and tier
    """
    liquidity_tier = None

    # Check market cap (still filters)
    if check_market_cap:
        market_cap_millions = get_market_cap_millions(ticker)
        if market_cap_millions is not None:
            min_market_cap = container.config.thresholds.min_market_cap_millions
            if market_cap_millions < min_market_cap:
                return (True, f"Market cap ${market_cap_millions:.0f}M < ${min_market_cap:.0f}M", None)

    # Check liquidity tier (for display only - does NOT filter anymore)
    if check_liquidity:
        has_liquidity, liquidity_tier = check_liquidity_with_tier(ticker, expiration, container)
        # NOTE: We no longer filter based on liquidity tier
        # All opportunities are shown with their tier displayed as a warning

    return (False, None, liquidity_tier)


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

    Strategy (aligned with user's trading workflow):
        - Mon/Tue/Wed earnings → Friday of same week
        - Thu/Fri earnings → Friday 1 week out (avoid 0DTE risk)
        - Custom offset: earnings_date + offset_days (adjusted to trading day)

    User enters positions at 3-4pm on earnings day (or day before for BMO),
    exits next trading day at 9:30-10:30am, using Friday weekly expirations.
    """
    if offset_days is not None:
        target_date = earnings_date + timedelta(days=offset_days)
        return adjust_to_trading_day(target_date)

    # User strategy: Thursday or Friday earnings → Use Friday 1 week out
    # This avoids 0DTE risk and provides buffer for exit
    weekday = earnings_date.weekday()

    if weekday in [3, 4]:  # Thursday or Friday
        # Use next Friday (1 week out)
        if weekday == 3:  # Thursday
            return earnings_date + timedelta(days=8)  # Thu + 8 = next Fri
        else:  # Friday
            return earnings_date + timedelta(days=7)  # Fri + 7 = next Fri

    # Mon/Tue/Wed: Use Friday of same week
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

        # Fetch company name early (for result dictionaries)
        company_name = get_ticker_name(ticker)

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
                                'ticker_name': company_name,
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
                            'ticker_name': company_name,
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
                        'ticker_name': company_name,
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
                        'ticker_name': company_name,
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
                    'ticker_name': company_name,
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

        # CRITICAL: Check liquidity tier (POST-LOSS ANALYSIS ADDITION)
        liquidity_tier = get_liquidity_tier_for_display(ticker, expiration_date, container)
        logger.info(f"  Liquidity Tier: {liquidity_tier}")

        if liquidity_tier == "WARNING":
            logger.warning(f"\n⚠️  WARNING: Low liquidity detected for {ticker}")
            logger.warning(f"   This ticker has moderate liquidity - expect wider spreads and potential slippage")
            logger.warning(f"   Consider reducing position size or skipping this trade")
        elif liquidity_tier == "REJECT":
            logger.warning(f"\n❌ CRITICAL: Very low liquidity for {ticker}")
            logger.warning(f"   This ticker has very poor liquidity - DO NOT TRADE")

        if vrp.is_tradeable:
            logger.info("\n✅ TRADEABLE OPPORTUNITY")
        else:
            logger.info("\n⏭️  SKIP - Insufficient edge")

        return {
            'ticker': ticker,
            'ticker_name': company_name,
            'earnings_date': str(earnings_date),
            'expiration_date': str(expiration_date),
            'stock_price': float(implied_move.stock_price.amount),
            'implied_move_pct': str(vrp.implied_move_pct),
            'historical_mean_pct': str(vrp.historical_mean_move_pct),
            'vrp_ratio': float(vrp.vrp_ratio),
            'edge_score': float(vrp.edge_score),
            'recommendation': vrp.recommendation.value,
            'is_tradeable': vrp.is_tradeable,
            'liquidity_tier': liquidity_tier,  # CRITICAL ADDITION
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

    # Progress bar for scanning (optimized update frequency)
    pbar = tqdm(
        earnings_events,
        desc="Scanning earnings",
        unit="ticker",
        bar_format='{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}] {postfix}',
        file=sys.stderr,  # Use stderr to avoid interfering with output capture
        mininterval=0.5,  # Update every 0.5 seconds (reduced overhead)
        maxinterval=2.0   # Maximum 2 seconds between updates
    )

    for ticker, earnings_date, timing in pbar:
        pbar.set_postfix_str(f"Current: {ticker}")
        sys.stderr.flush()  # Force flush after each update

        # Calculate expiration date
        expiration_date = calculate_expiration_date(
            earnings_date, timing, expiration_offset
        )

        # Apply filters (market cap + liquidity) for scan mode
        should_filter, filter_reason, _ = should_filter_ticker(
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
    logger.info(f"   🔍 Filtered (Market Cap Only): {filtered_count}")
    logger.info(f"   ✓ Successfully Analyzed: {success_count}")
    logger.info(f"   ⏭️  Skipped (No Data): {skip_count}")
    logger.info(f"   ✗ Errors: {error_count}")

    # Tradeable opportunities
    tradeable = [r for r in results if r.get('is_tradeable', False)]
    if tradeable:
        logger.info(f"\n" + "=" * 80)
        logger.info(f"✅ RESULT: {len(tradeable)} TRADEABLE OPPORTUNITIES FOUND")
        logger.info("=" * 80)
        logger.info(f"\n🎯 Sorted by VRP Ratio, Liquidity:")

        # Table header (UPDATED POST-LOSS ANALYSIS - Added Liquidity column)
        logger.info(f"   {'#':<3} {'Ticker':<8} {'Name':<28} {'VRP':<8} {'Implied':<9} {'Edge':<7} {'Recommendation':<15} {'Liquidity':<12}")
        logger.info(f"   {'-'*3} {'-'*8} {'-'*28} {'-'*8} {'-'*9} {'-'*7} {'-'*15} {'-'*12}")

        # Sort by: 1) VRP (descending), 2) Liquidity (EXCELLENT, WARNING, REJECT)
        def sort_key_scan(x):
            liquidity_priority = {'EXCELLENT': 0, 'WARNING': 1, 'REJECT': 2, 'UNKNOWN': 3}
            tier = x.get('liquidity_tier', 'UNKNOWN')
            return (-x['vrp_ratio'], liquidity_priority.get(tier, 3))

        # Table rows
        for i, r in enumerate(sorted(tradeable, key=sort_key_scan), 1):
            ticker = r['ticker']
            name = r.get('ticker_name', '')[:28] if r.get('ticker_name') else ''
            vrp = f"{r['vrp_ratio']:.2f}x"
            implied = str(r['implied_move_pct'])
            edge = f"{r['edge_score']:.2f}"
            rec = r['recommendation'].upper()

            # CRITICAL: Display liquidity tier with color coding
            liquidity_tier = r.get('liquidity_tier', 'UNKNOWN')
            if liquidity_tier == "EXCELLENT":
                liq_display = "✓ High"
            elif liquidity_tier == "WARNING":
                liq_display = "⚠️  Low"
            else:
                liq_display = "❌ REJECT"

            logger.info(
                f"   {i:<3} {ticker:<8} {name:<28} {vrp:<8} {implied:<9} {edge:<7} {rec:<15} {liq_display:<12}"
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

    # Progress bar for ticker processing (optimized update frequency)
    pbar = tqdm(
        tickers,
        desc="Analyzing tickers",
        unit="ticker",
        bar_format='{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}] {postfix}',
        file=sys.stderr,  # Use stderr to avoid interfering with output capture
        mininterval=0.5,  # Update every 0.5 seconds (reduced overhead)
        maxinterval=2.0   # Maximum 2 seconds between updates
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
        should_filter, filter_reason, _ = should_filter_ticker(
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
    logger.info(f"   🔍 Filtered (Market Cap Only): {filtered_count}")
    logger.info(f"   ✓ Successfully Analyzed: {success_count}")
    logger.info(f"   ⏭️  Skipped (No Earnings/Data): {skip_count}")
    logger.info(f"   ✗ Errors: {error_count}")

    # Tradeable opportunities
    tradeable = [r for r in results if r.get('is_tradeable', False)]
    if tradeable:
        logger.info(f"\n" + "=" * 80)
        logger.info(f"✅ RESULT: {len(tradeable)} TRADEABLE OPPORTUNITIES FOUND")
        logger.info("=" * 80)
        logger.info(f"\n🎯 Sorted by Earnings Date, VRP, Liquidity:")

        # Table header (UPDATED POST-LOSS ANALYSIS - Added Liquidity column)
        logger.info(f"   {'#':<3} {'Ticker':<8} {'Name':<28} {'VRP':<8} {'Implied':<9} {'Edge':<7} {'Recommendation':<15} {'Earnings':<12} {'Liquidity':<12}")
        logger.info(f"   {'-'*3} {'-'*8} {'-'*28} {'-'*8} {'-'*9} {'-'*7} {'-'*15} {'-'*12} {'-'*12}")

        # Sort by: 1) Earnings date (ascending), 2) VRP (descending), 3) Liquidity (EXCELLENT, WARNING, REJECT)
        def sort_key_ticker(x):
            liquidity_priority = {'EXCELLENT': 0, 'WARNING': 1, 'REJECT': 2, 'UNKNOWN': 3}
            tier = x.get('liquidity_tier', 'UNKNOWN')
            return (x['earnings_date'], -x['vrp_ratio'], liquidity_priority.get(tier, 3))

        # Table rows
        for i, r in enumerate(sorted(tradeable, key=sort_key_ticker), 1):
            ticker = r['ticker']
            name = r.get('ticker_name', '')[:28] if r.get('ticker_name') else ''
            vrp = f"{r['vrp_ratio']:.2f}x"
            implied = str(r['implied_move_pct'])
            edge = f"{r['edge_score']:.2f}"
            rec = r['recommendation'].upper()
            earnings = r['earnings_date']

            # CRITICAL: Display liquidity tier with color coding
            liquidity_tier = r.get('liquidity_tier', 'UNKNOWN')
            if liquidity_tier == "EXCELLENT":
                liq_display = "✓ High"
            elif liquidity_tier == "WARNING":
                liq_display = "⚠️  Low"
            else:
                liq_display = "❌ REJECT"

            logger.info(
                f"   {i:<3} {ticker:<8} {name:<28} {vrp:<8} {implied:<9} {edge:<7} {rec:<15} {earnings:<12} {liq_display:<12}"
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

    # Progress bar for ticker processing (optimized update frequency)
    pbar = tqdm(
        tickers,
        desc="Analyzing tickers",
        unit="ticker",
        bar_format='{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}] {postfix}',
        file=sys.stderr,  # Use stderr to avoid interfering with output capture
        mininterval=0.5,  # Update every 0.5 seconds (reduced overhead)
        maxinterval=2.0   # Maximum 2 seconds between updates
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
        should_filter, filter_reason, _ = should_filter_ticker(
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
    logger.info(f"   🔍 Filtered (Market Cap Only): {filtered_count}")
    logger.info(f"   ✓ Successfully Analyzed: {success_count}")
    logger.info(f"   ⏭️  Skipped (No Earnings/Data): {skip_count}")
    logger.info(f"   ✗ Errors: {error_count}")

    # Tradeable opportunities
    tradeable = [r for r in results if r.get('is_tradeable', False)]
    if tradeable:
        logger.info(f"\n" + "=" * 80)
        logger.info(f"✅ RESULT: {len(tradeable)} TRADEABLE OPPORTUNITIES FOUND")
        logger.info("=" * 80)
        logger.info(f"\n🎯 Most Anticipated + High VRP (Sorted by Earnings Date, VRP, Liquidity):")

        # Table header (UPDATED POST-LOSS ANALYSIS - Added Liquidity column)
        logger.info(f"   {'#':<3} {'Ticker':<8} {'Name':<28} {'VRP':<8} {'Implied':<9} {'Edge':<7} {'Recommendation':<15} {'Earnings':<12} {'Liquidity':<12}")
        logger.info(f"   {'-'*3} {'-'*8} {'-'*28} {'-'*8} {'-'*9} {'-'*7} {'-'*15} {'-'*12} {'-'*12}")

        # Sort by: 1) Earnings date (ascending), 2) VRP (descending), 3) Liquidity (EXCELLENT, WARNING, REJECT)
        def sort_key(x):
            # Liquidity tier priority: EXCELLENT=0, WARNING=1, REJECT=2
            liquidity_priority = {
                'EXCELLENT': 0,
                'WARNING': 1,
                'REJECT': 2,
                'UNKNOWN': 3
            }
            tier = x.get('liquidity_tier', 'UNKNOWN')
            return (
                x['earnings_date'],          # Sort by date (ascending - soonest first)
                -x['vrp_ratio'],             # Then by VRP (descending - highest first)
                liquidity_priority.get(tier, 3)  # Then by liquidity (EXCELLENT first, REJECT last)
            )

        # Table rows
        for i, r in enumerate(sorted(tradeable, key=sort_key), 1):
            ticker = r['ticker']
            name = r.get('ticker_name', '')[:28] if r.get('ticker_name') else ''
            vrp = f"{r['vrp_ratio']:.2f}x"
            implied = str(r['implied_move_pct'])
            edge = f"{r['edge_score']:.2f}"
            rec = r['recommendation'].upper()
            earnings = r['earnings_date']

            # CRITICAL: Display liquidity tier with color coding
            liquidity_tier = r.get('liquidity_tier', 'UNKNOWN')
            if liquidity_tier == "EXCELLENT":
                liq_display = "✓ High"
            elif liquidity_tier == "WARNING":
                liq_display = "⚠️  Low"
            else:
                liq_display = "❌ REJECT"

            logger.info(
                f"   {i:<3} {ticker:<8} {name:<28} {vrp:<8} {implied:<9} {edge:<7} {rec:<15} {earnings:<12} {liq_display:<12}"
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
