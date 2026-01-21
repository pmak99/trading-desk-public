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

Composite Quality Scoring (Dec 2025):
    MIGRATION NOTICE: Ranking changed from VRP-only to multi-factor composite scoring.

    Pre-Dec 2025: Results ranked purely by VRP ratio (descending)
    Post-Dec 2025: Results ranked by composite quality score (0-100 points)

    Scoring Factors:
    - VRP Edge (35 pts): Volatility risk premium vs target
    - Edge Score (30 pts): Combined VRP + historical edge
    - Liquidity (20 pts): Execution quality (EXCELLENT/WARNING/REJECT)
    - Implied Move (15 pts): Difficulty factor (easier = higher)

    Directional Bias Handling:
    - Scan stage: NO directional penalty (all opportunities surface)
    - Strategy stage: Directional alignment applied by strategy_scorer.py
      (e.g., STRONG BEARISH + Bear Call Spread = +8 pts alignment bonus)

    This change better identifies risk-adjusted opportunities, not just highest VRP.
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
from src.utils.concurrent_scanner import ConcurrentScanner, BatchScanResult
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
from src.application.services.earnings_date_validator import EarningsDateValidator
from src.infrastructure.data_sources.yahoo_finance_earnings import YahooFinanceEarnings
from src.utils.market_hours import is_market_open, get_market_status, is_trading_day
import os

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

# Composite quality scoring constants (Dec 2025)
# OPTIMIZED via A/B testing with Monte Carlo simulation (100 iterations)
# Key findings:
#   - Edge score REMOVED: 80-95% correlated with VRP (redundant)
#   - Continuous scoring: Eliminates cliff effects, improves correlation
#   - Higher VRP target (4.0): More selective for quality trades
#   - VRP dominates: Primary edge signal should outweigh secondary factors
#
# Weight Rationale (Dec 6 revision):
#   - VRP is THE edge signal - a 3.87x VRP should beat 2.54x VRP
#   - Move is secondary risk factor, not primary edge
#   - Original 45/35 split let move penalty offset VRP advantage too much
#
# A/B Test Results (vs old config):
#   - Score separation: +38% (17.4 -> 24.0)
#   - Score-PnL correlation: +12% (0.196 -> 0.22)
#   - Win rate delta: +5% (52% -> 57%)

# VRP Factor (55 points) - PRIMARY edge signal
SCORE_VRP_MAX_POINTS = 55                   # Dominant weight - VRP is the core edge metric
SCORE_VRP_TARGET = 4.0                      # Higher bar for full points - more selective
SCORE_VRP_USE_LINEAR = True                 # Continuous scaling, no hard cap at target

# Edge Factor (DISABLED) - Removed due to redundancy with VRP
# edge_score = vrp_ratio / (1 + consistency), so ~85% correlated with VRP
# Having both double-counts the same signal, hurting performance
SCORE_EDGE_MAX_POINTS = 0                   # DISABLED - redundant with VRP
SCORE_EDGE_TARGET = 1.0                     # N/A (disabled)

# Liquidity Factor (20 points) - Moderate penalty for illiquidity
# 4-Tier System: EXCELLENT (>=5x OI, <=8%), GOOD (2-5x, 8-12%), WARNING (1-2x, 12-15%), REJECT (<1x, >15%)
SCORE_LIQUIDITY_MAX_POINTS = 20             # Moderate weight (don't over-penalize)
SCORE_LIQUIDITY_EXCELLENT_POINTS = 20       # Full points for excellent liquidity (>=5x OI, <=8% spread)
SCORE_LIQUIDITY_GOOD_POINTS = 16            # Good liquidity - tradeable at full size (2-5x OI, 8-12% spread)
SCORE_LIQUIDITY_WARNING_POINTS = 12         # Low liquidity - consider reducing size (1-2x OI, 12-15% spread)
SCORE_LIQUIDITY_REJECT_POINTS = 4           # Very low - small penalty, not zero (some REJECT trades win!)

# Implied Move Factor (25 points) - Secondary risk factor
# Lower implied move = easier trade, but VRP edge matters more
SCORE_MOVE_MAX_POINTS = 25                  # Reduced weight - secondary to VRP
SCORE_MOVE_USE_CONTINUOUS = True            # Linear interpolation (no cliff effects)
SCORE_MOVE_BASELINE_PCT = 20.0              # 20% implied move = 0 points

# Market hours indicator
MARKET_CLOSED_INDICATOR = "*"  # Appended to tier when using OI-only scoring

def parse_liquidity_tier(tier_display: str) -> Tuple[str, bool]:
    """
    Parse liquidity tier display string into base tier and market status.

    Args:
        tier_display: Tier string like "EXCELLENT", "WARNING*", "REJECT*"

    Returns:
        Tuple of (base_tier, is_oi_only)
        - base_tier: "EXCELLENT", "WARNING", or "REJECT"
        - is_oi_only: True if asterisk present (market closed, OI-only scoring)
    """
    is_oi_only = tier_display.endswith(MARKET_CLOSED_INDICATOR)
    base_tier = tier_display.rstrip(MARKET_CLOSED_INDICATOR)
    return (base_tier, is_oi_only)


def format_liquidity_display(tier_display: str) -> str:
    """
    Format liquidity tier for display with appropriate indicator.

    4-Tier System:
    - EXCELLENT: ✓ High (>=5x OI, <=8% spread)
    - GOOD: ✓ Good (2-5x OI, 8-12% spread)
    - WARNING: ⚠️ Low (1-2x OI, 12-15% spread)
    - REJECT: ❌ REJECT (<1x OI, >15% spread)

    Args:
        tier_display: Tier string from check_liquidity_with_tier

    Returns:
        Formatted display string like "✓ High", "✓ Good*", "⚠️  Low*", "❌ REJECT*"
    """
    base_tier, is_oi_only = parse_liquidity_tier(tier_display)
    suffix = "*" if is_oi_only else ""

    if base_tier == "EXCELLENT":
        return f"✓ High{suffix}"
    elif base_tier == "GOOD":
        return f"✓ Good{suffix}"
    elif base_tier == "WARNING":
        return f"⚠️  Low{suffix}"
    else:
        return f"❌ REJECT{suffix}"


# Discrete thresholds (fallback if continuous disabled)
SCORE_MOVE_EASY_THRESHOLD = 8.0             # Implied move % considered "easy" (full points)
SCORE_MOVE_MODERATE_THRESHOLD = 12.0        # Implied move % considered "moderate"
SCORE_MOVE_MODERATE_POINTS = 18             # Points for moderate difficulty (scaled to 25 max)
SCORE_MOVE_CHALLENGING_THRESHOLD = 15.0     # Implied move % considered "challenging"
SCORE_MOVE_CHALLENGING_POINTS = 11          # Points for challenging difficulty
SCORE_MOVE_EXTREME_POINTS = 4               # Points for extreme difficulty (>15%)
SCORE_DEFAULT_MOVE_POINTS = 12.5            # Default when implied move is missing (middle)

# Liquidity tier priority for sorting (lower number = higher priority)
# 4-Tier System: EXCELLENT > GOOD > WARNING > REJECT
LIQUIDITY_PRIORITY_ORDER = {
    'EXCELLENT': 0,
    'GOOD': 1,
    'WARNING': 2,
    'REJECT': 3,
    'UNKNOWN': 4
}

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
    Check liquidity tier using LiquidityScorer with market-hours awareness.

    This is now a thin wrapper around the LiquidityScorer class, which provides
    the single source of truth for all liquidity tier classification across all modes.

    When markets are closed (weekends, holidays, after-hours), volume is always 0.
    In these cases, the scorer uses OI-only mode to avoid false REJECT classifications.

    Args:
        ticker: Stock ticker symbol
        expiration: Options expiration date
        container: DI container for LiquidityScorer access

    Returns:
        Tuple of (has_liquidity: bool, tier: str)
        - has_liquidity: True if acceptable (WARNING or EXCELLENT), False if REJECT
        - tier: "EXCELLENT", "WARNING", or "REJECT" (with market status suffix when closed)
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

        # Use LiquidityScorer with market-hours awareness
        liquidity_scorer = container.liquidity_scorer
        tier, market_open, market_reason = liquidity_scorer.classify_straddle_tier_market_aware(mid_call, mid_put)

        # Determine if has acceptable liquidity (WARNING or EXCELLENT = True, REJECT = False)
        has_liquidity = tier != "REJECT"

        # Add market status indicator when closed
        if not market_open:
            display_tier = f"{tier}*"  # Asterisk indicates OI-only scoring
            logger.debug(f"{ticker}: {tier} liquidity tier (OI-only, market: {market_reason}) "
                        f"(call OI={mid_call.open_interest}, put OI={mid_put.open_interest}, "
                        f"call spread={mid_call.spread_pct:.1f}%, put spread={mid_put.spread_pct:.1f}%)")
        else:
            display_tier = tier
            logger.debug(f"{ticker}: {tier} liquidity tier (call OI={mid_call.open_interest}, put OI={mid_put.open_interest}, "
                        f"call vol={mid_call.volume}, put vol={mid_put.volume}, "
                        f"call spread={mid_call.spread_pct:.1f}%, put spread={mid_put.spread_pct:.1f}%)")

        result = (has_liquidity, display_tier)
        _liquidity_cache[cache_key] = result
        return result

    except Exception as e:
        # Log at warning level since this could indicate real issues
        logger.warning(f"{ticker}: Liquidity check failed: {e}")
        # Don't cache errors - allow retry on next call
        # Return REJECT to be conservative when we can't verify liquidity
        return (False, "REJECT")


def check_basic_liquidity(ticker: str, expiration: date, container: Container) -> bool:
    """Quick liquidity check (convenience wrapper)."""
    has_liquidity, _ = check_liquidity_with_tier(ticker, expiration, container)
    return has_liquidity


def get_liquidity_tier_for_display(ticker: str, expiration: date, container: Container) -> str:
    """Get liquidity tier (convenience wrapper)."""
    _, tier = check_liquidity_with_tier(ticker, expiration, container)
    return tier


# Cache for hybrid liquidity checks (key includes implied move since it affects strike selection)
_hybrid_liquidity_cache: Dict[Tuple[str, date, float], Tuple[bool, str, Dict]] = {}


def check_liquidity_hybrid(
    ticker: str,
    expiration: date,
    implied_move_pct: float,
    container: Container,
    max_loss_budget: float = 20000.0,
    use_dynamic_thresholds: bool = True,
) -> Tuple[bool, str, Dict]:
    """
    Hybrid liquidity check using C-then-B approach with dynamic thresholds.

    This is the RECOMMENDED liquidity check for scan stage. It evaluates liquidity
    at strikes that will actually be traded (outside implied move or 20-delta),
    not mid-chain ATM strikes.

    Method C: Check strikes just outside implied move (preferred)
    Method B: Fall back to 20-delta strikes if C fails

    Dynamic thresholds are based on position size for $20k max loss:
    - REJECT: OI < 1x position size
    - WARNING: OI < 5x position size
    - EXCELLENT: OI >= 5x position size

    Args:
        ticker: Stock ticker symbol
        expiration: Options expiration date
        implied_move_pct: Implied move as percentage (e.g., 8.5 for 8.5%)
        container: DI container for API access
        max_loss_budget: Maximum loss budget (default $20,000)
        use_dynamic_thresholds: Whether to use dynamic or static thresholds

    Returns:
        Tuple of (has_liquidity, display_tier, details)
        - has_liquidity: True if WARNING or EXCELLENT, False if REJECT
        - display_tier: "EXCELLENT", "WARNING", "REJECT" (with * if market closed)
        - details: Dict with method used, strikes, OI values, thresholds, etc.
    """
    # Check cache first (include implied move in key since it affects strike selection)
    cache_key = (ticker, expiration, round(implied_move_pct, 1))
    if cache_key in _hybrid_liquidity_cache:
        cached = _hybrid_liquidity_cache[cache_key]
        logger.debug(f"{ticker}: Hybrid liquidity from cache: {cached[1]}")
        return cached

    try:
        # Get option chain
        tradier = container.tradier
        chain_result = tradier.get_option_chain(ticker, expiration)

        if chain_result.is_err:
            logger.debug(f"{ticker}: No option chain available for hybrid check")
            result = (False, "REJECT", {'method': 'NO_CHAIN', 'error': str(chain_result.error)})
            _hybrid_liquidity_cache[cache_key] = result
            return result

        chain = chain_result.value

        # Use LiquidityScorer's hybrid classification
        liquidity_scorer = container.liquidity_scorer
        tier, market_open, market_reason, details = liquidity_scorer.classify_hybrid_tier_market_aware(
            chain=chain,
            implied_move_pct=implied_move_pct,
            max_loss_budget=max_loss_budget,
            use_dynamic_thresholds=use_dynamic_thresholds,
        )

        # Determine if has acceptable liquidity
        has_liquidity = tier != "REJECT"

        # Add market status indicator when closed
        if not market_open:
            display_tier = f"{tier}*"
            logger.debug(
                f"{ticker}: HYBRID {tier} (OI-only, {market_reason}) "
                f"method={details['method']}, "
                f"call ${details['call_strike']} OI={details['call_oi']}, "
                f"put ${details['put_strike']} OI={details['put_oi']}, "
                f"min_oi={details['min_oi']}, ratio={details.get('oi_ratio', 'N/A')}"
            )
        else:
            display_tier = tier
            logger.debug(
                f"{ticker}: HYBRID {tier} "
                f"method={details['method']}, "
                f"call ${details['call_strike']} OI={details['call_oi']}, "
                f"put ${details['put_strike']} OI={details['put_oi']}, "
                f"min_oi={details['min_oi']}, ratio={details.get('oi_ratio', 'N/A')}"
            )

        result = (has_liquidity, display_tier, details)
        _hybrid_liquidity_cache[cache_key] = result
        return result

    except Exception as e:
        logger.warning(f"{ticker}: Hybrid liquidity check failed: {e}")
        return (False, "REJECT", {'method': 'ERROR', 'error': str(e)})


def should_filter_ticker(
    ticker: str,
    expiration: date,
    container: Container,
    check_market_cap: bool = True,
    check_liquidity: bool = True,
    implied_move_pct: Optional[float] = None,
    use_hybrid_liquidity: bool = False,
    max_loss_budget: float = 20000.0,
) -> Tuple[bool, Optional[str], Optional[str], Optional[Dict]]:
    """
    Determine if ticker should be filtered out based on market cap (LIQUIDITY NO LONGER FILTERS).

    IMPORTANT: Liquidity tier is checked and returned for display purposes, but does NOT
    cause filtering. All tradeable opportunities are shown regardless of liquidity tier,
    with appropriate warnings in the output.

    When use_hybrid_liquidity=True and implied_move_pct is provided, uses the new
    C-then-B hybrid liquidity check which evaluates strikes at actual trading levels
    (outside implied move) with dynamic thresholds based on position size.

    Args:
        ticker: Stock ticker symbol
        expiration: Options expiration date
        container: DI container
        check_market_cap: Whether to check market cap threshold
        check_liquidity: Whether to check liquidity tier (for display only, doesn't filter)
        implied_move_pct: Implied move percentage (required for hybrid check)
        use_hybrid_liquidity: Use C-then-B hybrid check instead of mid-chain
        max_loss_budget: Maximum loss budget for dynamic thresholds (default $20k)

    Returns:
        (should_filter, reason, liquidity_tier, hybrid_details)
        - should_filter: True if should skip ticker
        - reason: Why filtered
        - liquidity_tier: "EXCELLENT", "WARNING", or "REJECT"
        - hybrid_details: Dict with method, strikes, OI if hybrid check used
    """
    liquidity_tier = None
    hybrid_details = None

    # Check market cap (still filters)
    if check_market_cap:
        market_cap_millions = get_market_cap_millions(ticker)
        if market_cap_millions is not None:
            min_market_cap = container.config.thresholds.min_market_cap_millions
            if market_cap_millions < min_market_cap:
                return (True, f"Market cap ${market_cap_millions:.0f}M < ${min_market_cap:.0f}M", None, None)

    # Check liquidity tier (for display only - does NOT filter anymore)
    if check_liquidity:
        if use_hybrid_liquidity and implied_move_pct is not None:
            # Use new hybrid C-then-B approach with dynamic thresholds
            has_liquidity, liquidity_tier, hybrid_details = check_liquidity_hybrid(
                ticker=ticker,
                expiration=expiration,
                implied_move_pct=implied_move_pct,
                container=container,
                max_loss_budget=max_loss_budget,
                use_dynamic_thresholds=True,
            )
        else:
            # Fall back to old mid-chain approach
            has_liquidity, liquidity_tier = check_liquidity_with_tier(ticker, expiration, container)
        # NOTE: We no longer filter based on liquidity tier
        # All opportunities are shown with their tier displayed as a warning

    return (False, None, liquidity_tier, hybrid_details)


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


def calculate_implied_move_expiration(earnings_date: date) -> date:
    """
    Calculate the expiration date for implied move calculation.

    For IV crush analysis, we always use the FIRST expiration after earnings
    to capture the pure implied volatility that will collapse post-earnings.

    Args:
        earnings_date: Date of earnings announcement

    Returns:
        First trading day after earnings (adjusted for weekends)

    Note:
        This differs from trading expiration (which may use Fridays for
        liquidity). Implied move must use first post-earnings expiration
        to accurately measure the volatility being priced in.
    """
    # Always use earnings_date + 1 day, adjusted to trading day
    next_day = earnings_date + timedelta(days=1)
    return adjust_to_trading_day(next_day)


def calculate_expiration_date(
    earnings_date: date,
    timing: EarningsTiming,
    offset_days: Optional[int] = None
) -> date:
    """
    Calculate expiration date for TRADING purposes (liquidity, strategy).

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

    Note:
        For implied move calculation, use calculate_implied_move_expiration()
        instead - it always uses first post-earnings expiration.
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
                            db_date_is_past = earnings_date <= date.today()

                            # If API returns a date 45+ days further AND DB date is past/today,
                            # earnings likely already reported - API is showing next quarter
                            if date_diff_days >= 45 and db_date_is_past:
                                logger.warning(
                                    f"{ticker}: Earnings likely ALREADY REPORTED on {earnings_date}. "
                                    f"API shows next quarter: {av_date} ({date_diff_days}d later)"
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
                                # Return None to skip this ticker - earnings already happened
                                return None

                            logger.warning(f"{ticker}: Date changed! DB={earnings_date} → API={av_date}")
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

        # Calculate the implied move expiration (first post-earnings day)
        # This is different from trading expiration to capture pure IV crush
        implied_move_exp = calculate_implied_move_expiration(earnings_date)

        # Find nearest available expiration for implied move calculation
        nearest_im_exp_result = container.tradier.find_nearest_expiration(ticker, implied_move_exp)
        if nearest_im_exp_result.is_err:
            logger.warning(f"✗ Failed to find implied move expiration for {ticker}: {nearest_im_exp_result.error}")
            return None

        actual_im_expiration = nearest_im_exp_result.value
        if actual_im_expiration != implied_move_exp:
            logger.info(f"  Implied move expiration: {implied_move_exp} → {actual_im_expiration}")

        # Find nearest available expiration for trading/liquidity
        nearest_exp_result = container.tradier.find_nearest_expiration(ticker, expiration_date)
        if nearest_exp_result.is_err:
            logger.warning(f"✗ Failed to find trading expiration for {ticker}: {nearest_exp_result.error}")
            return None

        actual_expiration = nearest_exp_result.value
        if actual_expiration != expiration_date:
            logger.info(f"  Trading expiration: {expiration_date} → {actual_expiration}")
            expiration_date = actual_expiration

        # Get calculators
        implied_move_calc = container.implied_move_calculator
        vrp_calc = container.vrp_calculator
        prices_repo = container.prices_repository

        # Step 1: Calculate implied move using first post-earnings expiration
        logger.info("\n📊 Calculating Implied Move...")
        implied_result = implied_move_calc.calculate(ticker, actual_im_expiration)

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
                            "scripts/backfill_historical.py",
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
                logger.info("   Run: python scripts/backfill_historical.py " + ticker)
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

        # CRITICAL: Check liquidity tier using HYBRID approach (C-then-B with dynamic thresholds)
        # This evaluates liquidity at strikes outside implied move (where we'll actually trade)
        # instead of mid-chain ATM strikes which can give false results
        implied_move_pct = float(str(implied_move.implied_move_pct).rstrip('%'))
        has_liquidity, liquidity_tier, hybrid_details = check_liquidity_hybrid(
            ticker=ticker,
            expiration=expiration_date,
            implied_move_pct=implied_move_pct,
            container=container,
            max_loss_budget=20000.0,  # $20k max loss position sizing
            use_dynamic_thresholds=True,
        )

        # Log hybrid liquidity details
        if hybrid_details and hybrid_details.get('method') not in ('NO_CHAIN', 'ERROR', 'FAILED'):
            thresholds = hybrid_details.get('thresholds', {})
            oi_ratio = hybrid_details.get('oi_ratio')
            oi_tier = hybrid_details.get('oi_tier', 'N/A')
            spread_tier = hybrid_details.get('spread_tier', 'N/A')
            price_tier = thresholds.get('price_tier', 'N/A')
            spread_width = thresholds.get('spread_width', 'N/A')
            contracts = thresholds.get('contracts', 'N/A')
            max_spread = max(hybrid_details.get('call_spread_pct', 0), hybrid_details.get('put_spread_pct', 0))
            logger.info(f"  Liquidity Tier: {liquidity_tier} (Hybrid {hybrid_details['method']})")
            logger.info(f"    Call ${hybrid_details['call_strike']:.0f} OI={hybrid_details['call_oi']:,}, "
                       f"Put ${hybrid_details['put_strike']:.0f} OI={hybrid_details['put_oi']:,}")
            logger.info(f"    Position: {contracts} contracts × ${spread_width} spread ({price_tier} tier)")
            # Show tier breakdown
            oi_icon = {'EXCELLENT': '✓', 'GOOD': '✓', 'WARNING': '⚠️', 'REJECT': '❌'}.get(oi_tier, '?')
            spread_icon = {'EXCELLENT': '✓', 'GOOD': '✓', 'WARNING': '⚠️', 'REJECT': '❌'}.get(spread_tier, '?')
            logger.info(f"    OI: {oi_ratio:.1f}x → {oi_tier} {oi_icon} | Spread: {max_spread:.0f}% → {spread_tier} {spread_icon}")
        else:
            logger.info(f"  Liquidity Tier: {liquidity_tier}")

        # 4-Tier Warning Messages:
        # OI:     REJECT (<1x), WARNING (1-2x), GOOD (2-5x), EXCELLENT (>=5x)
        # Spread: REJECT (>15%), WARNING (>12%), GOOD (>8%), EXCELLENT (<=8%)
        tier_clean = liquidity_tier.replace('*', '')
        if tier_clean == "GOOD":
            # GOOD tier - tradeable but not excellent
            logger.info(f"\n✓ GOOD liquidity for {ticker}")
            oi_tier = hybrid_details.get('oi_tier', 'N/A')
            spread_tier = hybrid_details.get('spread_tier', 'N/A')
            if oi_tier == "GOOD":
                oi_ratio = hybrid_details.get('oi_ratio', 0)
                logger.info(f"   OI/Position ratio {oi_ratio:.1f}x (2-5x) - adequate for full size")
            if spread_tier == "GOOD":
                max_spread = max(hybrid_details.get('call_spread_pct', 0), hybrid_details.get('put_spread_pct', 0))
                logger.info(f"   Bid/ask spread {max_spread:.0f}% (8-12%) - acceptable slippage")
        elif tier_clean == "WARNING":
            logger.warning(f"\n⚠️  WARNING: Low liquidity detected for {ticker}")
            oi_tier = hybrid_details.get('oi_tier', 'N/A')
            spread_tier = hybrid_details.get('spread_tier', 'N/A')
            if oi_tier == "WARNING":
                oi_ratio = hybrid_details.get('oi_ratio', 0)
                logger.warning(f"   OI/Position ratio {oi_ratio:.1f}x (1-2x) - consider reducing size")
            if spread_tier == "WARNING":
                max_spread = max(hybrid_details.get('call_spread_pct', 0), hybrid_details.get('put_spread_pct', 0))
                logger.warning(f"   Bid/ask spread {max_spread:.0f}% (>12%) - expect slippage")
        elif tier_clean == "REJECT":
            logger.warning(f"\n❌ CRITICAL: Very low liquidity for {ticker}")
            oi_tier = hybrid_details.get('oi_tier', 'N/A')
            spread_tier = hybrid_details.get('spread_tier', 'N/A')
            if oi_tier == "REJECT":
                oi_ratio = hybrid_details.get('oi_ratio', 0)
                logger.warning(f"   OI/Position ratio {oi_ratio:.1f}x (<1x) - DO NOT TRADE at full size")
            if spread_tier == "REJECT":
                max_spread = max(hybrid_details.get('call_spread_pct', 0), hybrid_details.get('put_spread_pct', 0))
                logger.warning(f"   Bid/ask spread {max_spread:.0f}% (>15%) - DO NOT TRADE")

        if vrp.is_tradeable:
            logger.info("\n✅ TRADEABLE OPPORTUNITY")
        else:
            logger.info("\n⏭️  SKIP - Insufficient edge")

        # Get directional bias from skew analysis
        directional_bias = "NEUTRAL"  # Default if skew analysis unavailable
        skew_analyzer = container.skew_analyzer
        if skew_analyzer:
            skew_result = skew_analyzer.analyze_skew_curve(ticker, expiration_date)
            if skew_result.is_ok:
                # Format: "STRONG BEARISH" instead of "strong_bearish"
                directional_bias = skew_result.value.directional_bias.value.replace('_', ' ').upper()
                logger.info(f"  Directional Bias: {directional_bias}")

        # Build hybrid liquidity info for result
        oi_ratio = None
        if hybrid_details and hybrid_details.get('oi_ratio'):
            oi_ratio = hybrid_details['oi_ratio']

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
            'liquidity_oi_ratio': oi_ratio,  # NEW: OI/Position ratio from hybrid check
            'directional_bias': directional_bias,  # NEW: Directional bias from skew
            'status': 'SUCCESS'
        }

    except Exception as e:
        logger.error(f"✗ Error analyzing {ticker}: {e}", exc_info=True)
        return None


def analyze_ticker_concurrent(
    container: Container,
    ticker: str,
    earnings_date: date,
    expiration_date: date
) -> Optional[dict]:
    """
    Wrapper for analyze_ticker() compatible with ConcurrentScanner.

    Used by ConcurrentScanner.scan_ticker() as the analyze_func parameter.
    Disables auto-backfill for concurrent mode to avoid blocking.

    Args:
        container: DI container
        ticker: Stock ticker symbol
        earnings_date: Earnings announcement date
        expiration_date: Options expiration date

    Returns:
        Analysis result dict or None
    """
    return analyze_ticker(
        container=container,
        ticker=ticker,
        earnings_date=earnings_date,
        expiration_date=expiration_date,
        auto_backfill=False  # Disable backfill in concurrent mode
    )


def filter_ticker_concurrent(
    ticker: str,
    expiration_date: date,
    container: Container
) -> Tuple[bool, Optional[str]]:
    """
    Filter function compatible with ConcurrentScanner.

    Args:
        ticker: Stock ticker symbol
        expiration_date: Options expiration date
        container: DI container (passed via closure)

    Returns:
        (should_filter, reason) tuple
    """
    should_filter, reason, _, _ = should_filter_ticker(
        ticker, expiration_date, container,
        check_market_cap=True,
        check_liquidity=True
    )
    return (should_filter, reason)


def calculate_scan_quality_score(result: dict) -> float:
    """
    Calculate composite quality score for scan ranking.

    Multi-factor scoring that weighs risk-adjusted returns, not just VRP.
    This prevents high-VRP but risky trades from outranking safer opportunities.

    POST-PERPLEXITY ANALYSIS (Dec 2025):
    After comparing with Perplexity's multi-factor approach, added composite
    scoring to better align with risk-adjusted quality metrics.

    DIRECTIONAL BIAS REMOVED (Dec 2025):
    Directional alignment is handled at strategy selection stage (strategy_scorer.py),
    not at scan stage. This allows all opportunities to surface, then strategies
    get matched appropriately (e.g., STRONG BEARISH + Bear Call Spread = aligned).

    Scoring Factors (100+ points, continuous scaling):
    - VRP Edge (45 base): Continuous scaling from VRP ratio (no hard cap)
    - Edge Score (DISABLED): Removed - 85% correlated with VRP, was double-counting
    - Liquidity Quality (20 max): EXCELLENT=20, WARNING=12, REJECT=4
    - Implied Move (35 max): Linear interpolation (0%=35pts, 20%=0pts)

    OPTIMIZED via A/B Testing (Dec 2025):
    - Tested 6 configurations over 100 Monte Carlo iterations
    - This config won 52% of iterations (next best: 20%)
    - Improvements vs original: +38% score separation, +12% correlation

    Default Score Philosophy:
    When data is missing, defaults are CONSERVATIVE (assume worst-case or middle):
    - Missing VRP: 0.0 (no edge = no points)
    - Missing liquidity: WARNING tier (12/20 pts)
    - Missing implied move: 17.5/35 pts (middle difficulty)

    This philosophy prioritizes safety: only reward what we can verify.

    Args:
        result: Analysis result dictionary with metrics. Expected keys:
            - vrp_ratio (float): Volatility risk premium ratio
            - edge_score (float): Combined VRP + historical edge
            - liquidity_tier (str): 'EXCELLENT', 'WARNING', 'REJECT', or 'UNKNOWN'
            - implied_move_pct (str|Percentage|None): Expected move percentage

    Returns:
        Composite quality score (0-100)

    Raises:
        TypeError: If result is not a dictionary

    Notes:
        Invalid field types (e.g., string for vrp_ratio) are logged as warnings
        and fall back to conservative defaults (0.0 for numeric fields, WARNING
        for liquidity). This ensures graceful degradation rather than hard failures.

    Examples:
        >>> result = {'vrp_ratio': 8.27, 'edge_score': 4.67,
        ...           'implied_move_pct': '12.10%', 'liquidity_tier': 'WARNING'}
        >>> calculate_scan_quality_score(result)
        81.0

        >>> result = {'vrp_ratio': 4.00, 'edge_score': 2.79,
        ...           'implied_move_pct': '11.69%', 'liquidity_tier': 'WARNING'}
        >>> calculate_scan_quality_score(result)
        75.9
    """
    # Input validation - defensive programming
    if not isinstance(result, dict):
        logger.error(f"calculate_scan_quality_score requires dict, got {type(result)}")
        raise TypeError(f"result must be dict, not {type(result).__name__}")

    # Factor 1: VRP Edge (max: SCORE_VRP_MAX_POINTS = 45)
    # Primary edge signal - continuous scaling for better discrimination
    vrp_ratio = result.get('vrp_ratio', 0.0)
    try:
        vrp_ratio = float(vrp_ratio) if vrp_ratio is not None else 0.0
    except (TypeError, ValueError) as e:
        logger.warning(f"Invalid vrp_ratio '{vrp_ratio}': {e}. Using 0.0")
        vrp_ratio = 0.0

    if SCORE_VRP_USE_LINEAR:
        # Continuous scaling: VRP 4.0 = 45pts, VRP 5.0 = 56pts, VRP 6.0 = 67pts
        # No hard cap - allows high VRP to differentiate from medium VRP
        vrp_normalized = vrp_ratio / SCORE_VRP_TARGET
        vrp_score = max(0.0, vrp_normalized) * SCORE_VRP_MAX_POINTS
    else:
        # Capped at target (legacy behavior)
        vrp_score = max(0.0, min(vrp_ratio / SCORE_VRP_TARGET, 1.0)) * SCORE_VRP_MAX_POINTS

    # Factor 2: Edge Score (DISABLED - redundant with VRP)
    # edge_score ≈ 0.85 * vrp_ratio, so having both double-counts VRP
    # A/B testing showed removing Edge improves overall performance
    edge_points = 0.0  # Disabled - SCORE_EDGE_MAX_POINTS = 0

    # Factor 3: Liquidity Quality (max: SCORE_LIQUIDITY_MAX_POINTS = 20)
    # 4-Tier System: EXCELLENT (20pts), GOOD (16pts), WARNING (12pts), REJECT (4pts)
    # Strip market-closed indicator (*) to get base tier for scoring
    liquidity_tier_raw = result.get('liquidity_tier', 'UNKNOWN')
    base_tier, _ = parse_liquidity_tier(liquidity_tier_raw)
    if base_tier == 'EXCELLENT':
        liquidity_score = SCORE_LIQUIDITY_EXCELLENT_POINTS  # 20 (>=5x OI, <=8% spread)
    elif base_tier == 'GOOD':
        liquidity_score = SCORE_LIQUIDITY_GOOD_POINTS       # 16 (2-5x OI, 8-12% spread)
    elif base_tier == 'WARNING':
        liquidity_score = SCORE_LIQUIDITY_WARNING_POINTS    # 12 (1-2x OI, 12-15% spread)
    elif base_tier == 'REJECT':
        liquidity_score = SCORE_LIQUIDITY_REJECT_POINTS     # 4 (<1x OI, >15% spread)
    else:
        # Unknown = assume WARNING (conservative default)
        liquidity_score = SCORE_LIQUIDITY_WARNING_POINTS

    # Factor 4: Implied Move Difficulty (max: SCORE_MOVE_MAX_POINTS = 35)
    # Lower implied move = easier to stay profitable = higher score
    # Historical data shows strong correlation between low IV and win rate
    implied_move_pct = result.get('implied_move_pct')
    if implied_move_pct is None:
        move_score = SCORE_DEFAULT_MOVE_POINTS  # Default middle score (17.5)
    else:
        try:
            # Extract percentage value (handles both Percentage objects and strings)
            if hasattr(implied_move_pct, 'value'):
                implied_pct = implied_move_pct.value
            else:
                # Parse string like "11.69%"
                implied_str = str(implied_move_pct).rstrip('%')
                implied_pct = float(implied_str)

            if SCORE_MOVE_USE_CONTINUOUS:
                # Continuous linear interpolation: 0% = 35pts, 20% = 0pts
                # Eliminates cliff effects (7.99% vs 8.01% no longer 5pt difference)
                move_normalized = max(0.0, 1.0 - (implied_pct / SCORE_MOVE_BASELINE_PCT))
                move_score = move_normalized * SCORE_MOVE_MAX_POINTS
            else:
                # Discrete buckets (legacy fallback)
                if implied_pct <= SCORE_MOVE_EASY_THRESHOLD:
                    move_score = SCORE_MOVE_MAX_POINTS
                elif implied_pct <= SCORE_MOVE_MODERATE_THRESHOLD:
                    move_score = SCORE_MOVE_MODERATE_POINTS
                elif implied_pct <= SCORE_MOVE_CHALLENGING_THRESHOLD:
                    move_score = SCORE_MOVE_CHALLENGING_POINTS
                else:
                    move_score = SCORE_MOVE_EXTREME_POINTS
        except (TypeError, ValueError, AttributeError) as e:
            logger.warning(
                f"Failed to parse implied_move_pct '{implied_move_pct}': {e}. "
                f"Using default {SCORE_DEFAULT_MOVE_POINTS}"
            )
            move_score = SCORE_DEFAULT_MOVE_POINTS

    # Calculate total score (no directional penalty - handled at strategy stage)
    total = vrp_score + edge_points + liquidity_score + move_score

    return round(total, 1)


def _precalculate_quality_scores(tradeable_results: List[dict]) -> None:
    """
    Pre-calculate quality scores for all tradeable results.

    Performance optimization (Dec 2025): Calculates scores once and caches
    in '_quality_score' field, avoiding O(n log n) recalculations during
    sorting and n recalculations during display (~82% savings).

    Modifies results in-place by adding '_quality_score' field. The leading
    underscore indicates this is an internal/temporary field used only for
    sorting and display within the scan module.

    Args:
        tradeable_results: List of result dictionaries to score

    Returns:
        None (modifies input list in-place)
    """
    for result in tradeable_results:
        result['_quality_score'] = calculate_scan_quality_score(result)


def _display_scan_results(
    results: List[dict],
    success_count: int,
    error_count: int,
    skip_count: int,
    filtered_count: int,
    mode_name: str,
    scan_date: Optional[date] = None,
    total_events: int = 0,
    tickers: Optional[List[str]] = None,
    week_range: Optional[Tuple[date, date]] = None
) -> int:
    """
    Display scan results in a formatted table (shared by all modes).

    This is a helper function to avoid duplicating display logic across
    scanning_mode, ticker_mode, and whisper_mode.

    Args:
        results: List of analysis result dictionaries
        success_count: Number of successful analyses
        error_count: Number of errors
        skip_count: Number of skipped tickers
        filtered_count: Number of filtered tickers
        mode_name: Display name for the mode (e.g., "SCAN MODE")
        scan_date: Target date for scan mode
        total_events: Total earnings events found
        tickers: List of tickers for ticker mode
        week_range: (start, end) dates for whisper mode

    Returns:
        Exit code (0 for success)
    """
    # Summary header
    logger.info("\n" + "=" * 80)
    logger.info(f"{mode_name} - SUMMARY")
    logger.info("=" * 80)

    # Mode-specific details
    if scan_date:
        logger.info(f"\n📅 Scan Details:")
        logger.info(f"   Mode: Earnings Date Scan")
        logger.info(f"   Date: {scan_date}")
        logger.info(f"   Total Earnings Found: {total_events}")
    elif week_range:
        logger.info(f"\n🔊 Most Anticipated Earnings Analysis:")
        logger.info(f"   Mode: Earnings Whispers")
        logger.info(f"   Week: {week_range[0]} to {week_range[1]}")
    elif tickers:
        logger.info(f"\n📋 Ticker List Analysis:")
        logger.info(f"   Mode: Multiple Ticker Analysis")
        logger.info(f"   Tickers Requested: {len(tickers)}")

    logger.info(f"\n📊 Analysis Results:")
    logger.info(f"   🔍 Filtered (Market Cap Only): {filtered_count}")
    logger.info(f"   ✓ Successfully Analyzed: {success_count}")
    logger.info(f"   ⏭️  Skipped (No Data): {skip_count}")
    logger.info(f"   ✗ Errors: {error_count}")

    # Tradeable opportunities
    tradeable = [r for r in results if r.get('is_tradeable', False)]
    if tradeable:
        # Pre-calculate quality scores once
        _precalculate_quality_scores(tradeable)

        logger.info(f"\n" + "=" * 80)
        logger.info(f"✅ RESULT: {len(tradeable)} TRADEABLE OPPORTUNITIES FOUND")
        logger.info("=" * 80)
        logger.info(f"\n🎯 Sorted by Quality Score (Risk-Adjusted):")

        # Table header
        logger.info(f"   {'#':<3} {'Ticker':<8} {'Name':<20} {'Score':<7} {'VRP':<8} {'Implied':<9} {'Edge':<7} {'Recommendation':<15} {'Liquidity':<12}")
        logger.info(f"   {'-'*3} {'-'*8} {'-'*20} {'-'*7} {'-'*8} {'-'*9} {'-'*7} {'-'*15} {'-'*12}")

        # Sort by quality score (strip asterisk for sorting)
        def sort_key(x):
            tier_raw = x.get('liquidity_tier', 'UNKNOWN')
            base_tier, _ = parse_liquidity_tier(tier_raw)
            return (-x['_quality_score'], LIQUIDITY_PRIORITY_ORDER.get(base_tier, 3))

        # Check if any result has OI-only indicator (market closed)
        has_oi_only = any(r.get('liquidity_tier', '').endswith('*') for r in tradeable)

        for i, r in enumerate(sorted(tradeable, key=sort_key), 1):
            ticker = r['ticker']
            full_name = r.get('ticker_name', '') or ''
            name = full_name[:20] if len(full_name) <= 20 else full_name[:full_name[:20].rfind(' ') or 20]

            score_display = f"{r['_quality_score']:.1f}"
            vrp = f"{r['vrp_ratio']:.2f}x"
            implied = str(r['implied_move_pct'])
            edge = f"{r['edge_score']:.2f}"
            rec = r['recommendation'].upper()

            # Use helper function for consistent liquidity display
            liquidity_tier = r.get('liquidity_tier', 'UNKNOWN')
            liq_display = format_liquidity_display(liquidity_tier)

            logger.info(
                f"   {i:<3} {ticker:<8} {name:<20} {score_display:<7} {vrp:<8} {implied:<9} {edge:<7} {rec:<15} {liq_display:<12}"
            )

        # Add footer note if market closed (OI-only scoring)
        if has_oi_only:
            logger.info(f"\n   * Liquidity based on OI only (market closed, volume unavailable)")

        logger.info(f"\n💡 Run './trade.sh TICKER YYYY-MM-DD' for detailed strategy recommendations")
    else:
        logger.info(f"\n" + "=" * 80)
        logger.info("⏭️  RESULT: NO TRADEABLE OPPORTUNITIES")
        logger.info("=" * 80)
        logger.info(f"\n❌ No opportunities found")
        if skip_count > 0:
            logger.info(f"   Note: {skip_count} ticker(s) skipped due to missing historical data")

    return 0


def scanning_mode_parallel(
    container: Container,
    scan_date: date,
    expiration_offset: Optional[int] = None
) -> int:
    """
    Parallel scanning mode: Scan earnings using ConcurrentScanner.

    Uses thread pool for ~5x speedup on multi-ticker scans.
    Returns exit code (0 for success, 1 for error)
    """
    logger.info("=" * 80)
    logger.info("SCANNING MODE: Earnings Date Scan (PARALLEL)")
    logger.info("=" * 80)
    logger.info(f"Scan Date: {scan_date}")
    logger.info("")

    # Fetch earnings for the date
    earnings_events = fetch_earnings_for_date(container, scan_date)

    if not earnings_events:
        logger.warning("No earnings found for this date")
        return 0

    # Build earnings lookup for ConcurrentScanner
    # Format: ticker -> (earnings_date, timing_str)
    earnings_lookup: Dict[str, Tuple[date, str]] = {}
    for ticker, earnings_date, timing in earnings_events:
        earnings_lookup[ticker] = (earnings_date, timing.value)

    tickers = list(earnings_lookup.keys())

    logger.info(f"Starting parallel scan of {len(tickers)} tickers...")

    # Create filter function with container closure
    def filter_func(ticker: str, expiration: date) -> Tuple[bool, Optional[str]]:
        return filter_ticker_concurrent(ticker, expiration, container)

    # Progress callback for logging
    def progress_callback(ticker: str, completed: int, total: int):
        if completed % 5 == 0 or completed == total:
            logger.info(f"Progress: {completed}/{total} ({completed*100//total}%)")

    # Run concurrent scan
    scanner = container.concurrent_scanner
    batch_result = scanner.scan_tickers(
        tickers=tickers,
        earnings_lookup=earnings_lookup,
        analyze_func=analyze_ticker_concurrent,
        filter_func=filter_func,
        expiration_offset=expiration_offset or 0,
        progress_callback=progress_callback,
    )

    # Extract results
    results = []
    for scan_result in batch_result.results:
        if scan_result.data:
            results.append(scan_result.data)

    # Log statistics
    logger.info(f"\n📊 Parallel Scan Complete:")
    logger.info(f"   Total time: {batch_result.total_duration_ms:.0f}ms")
    logger.info(f"   Avg per ticker: {batch_result.avg_duration_ms:.0f}ms")
    logger.info(f"   Success: {batch_result.success_count}")
    logger.info(f"   Filtered: {batch_result.filtered_count}")
    logger.info(f"   Skipped: {batch_result.skip_count}")
    logger.info(f"   Errors: {batch_result.error_count}")

    # Display results using existing logic
    return _display_scan_results(
        results=results,
        success_count=batch_result.success_count,
        error_count=batch_result.error_count,
        skip_count=batch_result.skip_count,
        filtered_count=batch_result.filtered_count,
        mode_name="SCAN MODE",
        scan_date=scan_date,
        total_events=len(earnings_events)
    )


def scanning_mode(
    container: Container,
    scan_date: date,
    expiration_offset: Optional[int] = None,
    parallel: bool = False
) -> int:
    """
    Scanning mode: Scan earnings for a specific date.

    Args:
        container: DI container
        scan_date: Target earnings date
        expiration_offset: Custom expiration offset in days
        parallel: If True, use parallel processing (5x speedup)

    Returns exit code (0 for success, 1 for error)
    """
    # Use parallel mode if requested
    if parallel:
        return scanning_mode_parallel(container, scan_date, expiration_offset)

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
        should_filter, filter_reason, _, _ = should_filter_ticker(
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
        # Pre-calculate quality scores once (avoids ~82% duplicate calculations)
        _precalculate_quality_scores(tradeable)

        logger.info(f"\n" + "=" * 80)
        logger.info(f"✅ RESULT: {len(tradeable)} TRADEABLE OPPORTUNITIES FOUND")
        logger.info("=" * 80)
        logger.info(f"\n🎯 Sorted by Quality Score (Risk-Adjusted):")

        # Table header (UPDATED Dec 2025 - Added Quality Score for risk-adjusted ranking)
        logger.info(f"   {'#':<3} {'Ticker':<8} {'Name':<20} {'Score':<7} {'VRP':<8} {'Implied':<9} {'Edge':<7} {'Recommendation':<15} {'Liquidity':<12}")
        logger.info(f"   {'-'*3} {'-'*8} {'-'*20} {'-'*7} {'-'*8} {'-'*9} {'-'*7} {'-'*15} {'-'*12}")

        # Sort by: 1) Quality Score (descending), 2) Liquidity (EXCELLENT, WARNING, REJECT)
        def sort_key_scan(x):
            tier_raw = x.get('liquidity_tier', 'UNKNOWN')
            base_tier, _ = parse_liquidity_tier(tier_raw)
            return (-x['_quality_score'], LIQUIDITY_PRIORITY_ORDER.get(base_tier, 3))

        # Check if any result has OI-only indicator (market closed)
        has_oi_only = any(r.get('liquidity_tier', '').endswith('*') for r in tradeable)

        # Table rows
        for i, r in enumerate(sorted(tradeable, key=sort_key_scan), 1):
            ticker = r['ticker']
            # Truncate ticker name to 20 chars at word boundary (don't split words)
            full_name = r.get('ticker_name', '') if r.get('ticker_name') else ''
            if len(full_name) <= 20:
                name = full_name
            else:
                truncated = full_name[:20]
                last_space = truncated.rfind(' ')
                name = truncated[:last_space] if last_space > 0 else truncated

            # Use pre-calculated quality score
            score_display = f"{r['_quality_score']:.1f}"

            vrp = f"{r['vrp_ratio']:.2f}x"
            implied = str(r['implied_move_pct'])
            edge = f"{r['edge_score']:.2f}"
            rec = r['recommendation'].upper()

            # Use helper function for consistent liquidity display
            liquidity_tier = r.get('liquidity_tier', 'UNKNOWN')
            liq_display = format_liquidity_display(liquidity_tier)

            logger.info(
                f"   {i:<3} {ticker:<8} {name:<20} {score_display:<7} {vrp:<8} {implied:<9} {edge:<7} {rec:<15} {liq_display:<12}"
            )

        # Add footer note if market closed (OI-only scoring)
        if has_oi_only:
            logger.info(f"\n   * Liquidity based on OI only (market closed, volume unavailable)")

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


def ticker_mode_parallel(
    container: Container,
    tickers: List[str],
    expiration_offset: Optional[int] = None
) -> int:
    """
    Parallel ticker mode: Analyze tickers using ConcurrentScanner.

    Uses thread pool for ~5x speedup on multi-ticker analysis.
    Returns exit code (0 for success, 1 for error)
    """
    logger.info("=" * 80)
    logger.info("TICKER MODE: Command Line Tickers (PARALLEL)")
    logger.info("=" * 80)
    logger.info(f"Tickers: {', '.join(tickers)}")
    logger.info("")

    # Build earnings lookup for each ticker
    earnings_lookup: Dict[str, Tuple[date, str]] = {}

    logger.info("Fetching earnings dates...")
    for ticker in tickers:
        earnings_info = fetch_earnings_for_ticker(container, ticker)
        if earnings_info:
            earnings_date, timing = earnings_info
            earnings_lookup[ticker] = (earnings_date, timing.value)
        else:
            logger.info(f"⏭️  {ticker}: No upcoming earnings found")

    if not earnings_lookup:
        logger.warning("No earnings found for any requested tickers")
        return 0

    logger.info(f"Starting parallel analysis of {len(earnings_lookup)} tickers...")

    # Create filter function with container closure
    def filter_func(ticker: str, expiration: date) -> Tuple[bool, Optional[str]]:
        return filter_ticker_concurrent(ticker, expiration, container)

    # Progress callback for logging
    def progress_callback(ticker: str, completed: int, total: int):
        logger.info(f"Progress: {completed}/{total} - {ticker}")

    # Run concurrent scan
    scanner = container.concurrent_scanner
    batch_result = scanner.scan_tickers(
        tickers=list(earnings_lookup.keys()),
        earnings_lookup=earnings_lookup,
        analyze_func=analyze_ticker_concurrent,
        filter_func=filter_func,
        expiration_offset=expiration_offset or 0,
        progress_callback=progress_callback,
    )

    # Extract results
    results = []
    for scan_result in batch_result.results:
        if scan_result.data:
            results.append(scan_result.data)

    # Log statistics
    logger.info(f"\n📊 Parallel Analysis Complete:")
    logger.info(f"   Total time: {batch_result.total_duration_ms:.0f}ms")
    logger.info(f"   Avg per ticker: {batch_result.avg_duration_ms:.0f}ms")
    logger.info(f"   Success: {batch_result.success_count}")
    logger.info(f"   Filtered: {batch_result.filtered_count}")
    logger.info(f"   Skipped: {batch_result.skip_count}")
    logger.info(f"   Errors: {batch_result.error_count}")

    # Display results using shared helper
    return _display_scan_results(
        results=results,
        success_count=batch_result.success_count,
        error_count=batch_result.error_count,
        skip_count=batch_result.skip_count + (len(tickers) - len(earnings_lookup)),
        filtered_count=batch_result.filtered_count,
        mode_name="TICKER MODE",
        tickers=tickers
    )


def ticker_mode(
    container: Container,
    tickers: List[str],
    expiration_offset: Optional[int] = None,
    parallel: bool = False
) -> int:
    """
    Ticker mode: Analyze specific tickers from command line.

    Args:
        container: DI container
        tickers: List of ticker symbols
        expiration_offset: Custom expiration offset in days
        parallel: If True, use parallel processing (5x speedup)

    Returns exit code (0 for success, 1 for error)
    """
    # Use parallel mode if requested and we have multiple tickers
    if parallel and len(tickers) > 1:
        return ticker_mode_parallel(container, tickers, expiration_offset)

    logger.info("=" * 80)
    logger.info("TICKER MODE: Command Line Tickers")
    logger.info("=" * 80)
    logger.info(f"Tickers: {', '.join(tickers)}")
    logger.info("")

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

        # Fetch earnings date for ticker (DB first, API fallback)
        earnings_info = fetch_earnings_for_ticker(container, ticker)

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
        should_filter, filter_reason, _, _ = should_filter_ticker(
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
        # Pre-calculate quality scores once (avoids ~82% duplicate calculations)
        _precalculate_quality_scores(tradeable)

        logger.info(f"\n" + "=" * 80)
        logger.info(f"✅ RESULT: {len(tradeable)} TRADEABLE OPPORTUNITIES FOUND")
        logger.info("=" * 80)
        logger.info(f"\n🎯 Sorted by Earnings Date, Quality Score (Risk-Adjusted):")

        # Table header (UPDATED Dec 2025 - Added Quality Score for risk-adjusted ranking)
        logger.info(f"   {'#':<3} {'Ticker':<8} {'Name':<20} {'Score':<7} {'VRP':<8} {'Implied':<9} {'Edge':<7} {'Recommendation':<15} {'Bias':<15} {'Earnings':<12} {'Liquidity':<12}")
        logger.info(f"   {'-'*3} {'-'*8} {'-'*20} {'-'*7} {'-'*8} {'-'*9} {'-'*7} {'-'*15} {'-'*15} {'-'*12} {'-'*12}")

        # Sort by: 1) Earnings date (ascending), 2) Quality Score (descending), 3) Liquidity (EXCELLENT, WARNING, REJECT)
        def sort_key_ticker(x):
            tier_raw = x.get('liquidity_tier', 'UNKNOWN')
            base_tier, _ = parse_liquidity_tier(tier_raw)
            return (x['earnings_date'], -x['_quality_score'], LIQUIDITY_PRIORITY_ORDER.get(base_tier, 3))

        # Check if any result has OI-only indicator (market closed)
        has_oi_only = any(r.get('liquidity_tier', '').endswith('*') for r in tradeable)

        # Table rows
        for i, r in enumerate(sorted(tradeable, key=sort_key_ticker), 1):
            ticker = r['ticker']
            # Truncate ticker name to 20 chars at word boundary (don't split words)
            full_name = r.get('ticker_name', '') if r.get('ticker_name') else ''
            if len(full_name) <= 20:
                name = full_name
            else:
                truncated = full_name[:20]
                last_space = truncated.rfind(' ')
                name = truncated[:last_space] if last_space > 0 else truncated

            # Use pre-calculated quality score
            score_display = f"{r['_quality_score']:.1f}"

            vrp = f"{r['vrp_ratio']:.2f}x"
            implied = str(r['implied_move_pct'])
            edge = f"{r['edge_score']:.2f}"
            rec = r['recommendation'].upper()
            bias = r.get('directional_bias', 'NEUTRAL')  # NEW: Display directional bias
            earnings = r['earnings_date']

            # Use helper function for consistent liquidity display
            liquidity_tier = r.get('liquidity_tier', 'UNKNOWN')
            liq_display = format_liquidity_display(liquidity_tier)

            logger.info(
                f"   {i:<3} {ticker:<8} {name:<20} {score_display:<7} {vrp:<8} {implied:<9} {edge:<7} {rec:<15} {bias:<15} {earnings:<12} {liq_display:<12}"
            )

        # Add footer note if market closed (OI-only scoring)
        if has_oi_only:
            logger.info(f"\n   * Liquidity based on OI only (market closed, volume unavailable)")

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

    logger.info(f"\n🔍 Validating earnings dates for {len(tickers_to_validate)} tradeable tickers...")

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
                logger.debug(f"⚠️  {ticker}: Conflict detected - {validation.conflict_details}")
        else:
            logger.debug(f"✗ {ticker}: Validation failed - {result.error}")

    logger.info(f"✓ Validated {success_count}/{len(tickers_to_validate)} tickers" +
                (f" (⚠️  {conflict_count} conflicts)" if conflict_count > 0 else ""))
    logger.info("")


def ensure_tickers_in_db(tickers: list[str], container: Container) -> None:
    """
    Ensure all tickers are in the database. Auto-add and sync missing tickers.

    This eliminates the manual workflow:
    - OLD: fetch tickers → manually add to DB → manually sync → re-run whisper
    - NEW: fetch tickers → auto-add → auto-sync → continue analysis

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
        logger.info(f"✓ All {len(tickers)} tickers already in database")
        return

    # Add missing tickers
    logger.info(f"📝 Adding {len(missing_tickers)} new tickers to database...")
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
    logger.info(f"✓ Added {len(missing_tickers)} tickers: {', '.join(missing_tickers[:10])}" +
                ("..." if len(missing_tickers) > 10 else ""))

    # Sync to fetch correct earnings dates
    logger.info("🔄 Syncing earnings dates from Alpha Vantage + Yahoo Finance...")
    logger.info(f"   Note: This may take ~{len(missing_tickers) * 12 // 60} minutes due to rate limiting (5 calls/min)")

    # Call the existing sync script
    script_path = Path(__file__).parent / "sync_earnings_calendar.py"
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            timeout=len(missing_tickers) * 15  # 15 seconds per ticker timeout
        )

        if result.returncode == 0:
            logger.info("✓ Earnings calendar synced successfully")

            # Clean up orphaned placeholders (confirmed=0) after successful sync
            logger.info("🧹 Cleaning up placeholder entries...")
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
                logger.info(f"✓ Removed {deleted} orphaned placeholder entries")
        else:
            logger.warning(f"⚠️  Sync completed with warnings:\n{result.stderr}")
    except subprocess.TimeoutExpired:
        logger.warning("⚠️  Sync timed out, but may have partially completed")
    except Exception as e:
        logger.error(f"✗ Sync failed: {e}")
        logger.info("   Continuing with API fallback...")


def whisper_mode_parallel(
    container: Container,
    tickers: List[str],
    monday: date,
    week_end: date,
    expiration_offset: Optional[int] = None
) -> int:
    """
    Parallel whisper mode: Analyze anticipated earnings using ConcurrentScanner.

    Uses thread pool for ~5x speedup on multi-ticker analysis.

    Args:
        container: DI container
        tickers: List of ticker symbols to analyze
        monday: Start of week (Monday)
        week_end: End of week (Sunday)
        expiration_offset: Custom expiration offset in days

    Returns:
        Exit code (0 = success, 1 = error)
    """
    logger.info("")
    logger.info("🚀 Using PARALLEL processing for faster analysis...")

    # Build earnings lookup for each ticker
    earnings_lookup: Dict[str, Tuple[date, str]] = {}

    logger.info("Fetching earnings dates...")
    for ticker in tickers:
        earnings_info = fetch_earnings_for_ticker(container, ticker)
        if earnings_info:
            earnings_date, timing = earnings_info
            earnings_lookup[ticker] = (earnings_date, timing.value)
        else:
            logger.info(f"⏭️  {ticker}: No upcoming earnings found")

    if not earnings_lookup:
        logger.warning("No earnings found for any anticipated tickers")
        return 0

    logger.info(f"Starting parallel analysis of {len(earnings_lookup)} tickers...")

    # Create filter function with container closure
    def filter_func(ticker: str, expiration: date) -> Tuple[bool, Optional[str]]:
        return filter_ticker_concurrent(ticker, expiration, container)

    # Progress callback for logging
    def progress_callback(ticker: str, completed: int, total: int):
        if completed % 5 == 0 or completed == total:
            logger.info(f"Progress: {completed}/{total} ({completed*100//total}%)")

    # Run concurrent scan
    scanner = container.concurrent_scanner
    batch_result = scanner.scan_tickers(
        tickers=list(earnings_lookup.keys()),
        earnings_lookup=earnings_lookup,
        analyze_func=analyze_ticker_concurrent,
        filter_func=filter_func,
        expiration_offset=expiration_offset or 0,
        progress_callback=progress_callback,
    )

    # Extract results
    results = []
    for scan_result in batch_result.results:
        if scan_result.data:
            results.append(scan_result.data)

    # Validate earnings dates for tradeable results
    tradeable = [r for r in results if r.get('is_tradeable', False)]
    if tradeable:
        validate_tradeable_earnings_dates(tradeable, container)

    # Log statistics
    logger.info(f"\n📊 Parallel Analysis Complete:")
    logger.info(f"   Total time: {batch_result.total_duration_ms:.0f}ms")
    logger.info(f"   Avg per ticker: {batch_result.avg_duration_ms:.0f}ms")
    logger.info(f"   Success: {batch_result.success_count}")
    logger.info(f"   Filtered: {batch_result.filtered_count}")
    logger.info(f"   Skipped: {batch_result.skip_count}")
    logger.info(f"   Errors: {batch_result.error_count}")

    # Display results using shared helper
    return _display_scan_results(
        results=results,
        success_count=batch_result.success_count,
        error_count=batch_result.error_count,
        skip_count=batch_result.skip_count + (len(tickers) - len(earnings_lookup)),
        filtered_count=batch_result.filtered_count,
        mode_name="WHISPER MODE",
        week_range=(monday, week_end)
    )


def whisper_mode(
    container: Container,
    week_monday: Optional[str] = None,
    fallback_image: Optional[str] = None,
    expiration_offset: Optional[int] = None,
    parallel: bool = False
) -> int:
    """
    Whisper mode: Analyze most anticipated earnings.

    Fetches tickers from Reddit and analyzes each with auto-backfill.

    Args:
        container: DI container
        week_monday: Monday in YYYY-MM-DD (defaults to current week)
        fallback_image: Path to earnings screenshot (PNG/JPG)
        expiration_offset: Custom expiration offset in days
        parallel: If True, use parallel processing (5x speedup)

    Returns:
        Exit code (0 = success, 1 = error)
    """
    logger.info("=" * 80)
    logger.info("WHISPER MODE: Most Anticipated Earnings")
    logger.info("=" * 80)

    # Validate week_monday format if provided
    if week_monday:
        try:
            target_date = datetime.strptime(week_monday, "%Y-%m-%d")
            monday = get_week_monday(target_date)
        except ValueError:
            logger.error(f"Invalid date: {week_monday}. Use YYYY-MM-DD")
            return 1
        week_str = monday.strftime("%Y-%m-%d")
    else:
        # Let scraper auto-detect (tries next week first, then current)
        monday = None
        week_str = None

    if fallback_image:
        logger.info(f"Fallback: {fallback_image}")

    logger.info("Fetching ticker list...")
    scraper = EarningsWhisperScraper()
    result = scraper.get_most_anticipated_earnings(
        week_monday=week_str,
        fallback_image=fallback_image
    )

    if result.is_err:
        logger.error(f"Failed to fetch ticker list: {result.error}")
        return 1

    # Unpack result - scraper returns (tickers, actual_week_monday)
    tickers, monday = result.value

    # Calculate week range (Monday to Sunday)
    week_end = monday + timedelta(days=6)
    logger.info(f"Week: {monday.strftime('%Y-%m-%d')} to {week_end.strftime('%Y-%m-%d')}")

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

    # Ensure all tickers are in database (auto-add + sync if needed)
    ensure_tickers_in_db(tickers, container)

    # Use parallel mode if requested
    if parallel:
        return whisper_mode_parallel(
            container, tickers, monday, week_end, expiration_offset
        )

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

        # Fetch earnings date for ticker (DB first, API fallback)
        earnings_info = fetch_earnings_for_ticker(container, ticker)

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
        should_filter, filter_reason, _, _ = should_filter_ticker(
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
        # Validate earnings dates for tradeable tickers only (optimization)
        validate_tradeable_earnings_dates(tradeable, container)

        # Pre-calculate quality scores once (avoids ~82% duplicate calculations)
        _precalculate_quality_scores(tradeable)

        logger.info(f"\n" + "=" * 80)
        logger.info(f"✅ RESULT: {len(tradeable)} TRADEABLE OPPORTUNITIES FOUND")
        logger.info("=" * 80)
        logger.info(f"\n🎯 Most Anticipated + High VRP (Sorted by Earnings Date, Quality Score):")

        # Table header (UPDATED Dec 2025 - Added Quality Score for risk-adjusted ranking)
        logger.info(f"   {'#':<3} {'Ticker':<8} {'Name':<20} {'Score':<7} {'VRP':<8} {'Implied':<9} {'Edge':<7} {'Recommendation':<15} {'Bias':<15} {'Earnings':<12} {'Liquidity':<12}")
        logger.info(f"   {'-'*3} {'-'*8} {'-'*20} {'-'*7} {'-'*8} {'-'*9} {'-'*7} {'-'*15} {'-'*15} {'-'*12} {'-'*12}")

        # Sort by: 1) Earnings date (ascending), 2) Quality Score (descending), 3) Liquidity (EXCELLENT, WARNING, REJECT)
        def sort_key(x):
            tier_raw = x.get('liquidity_tier', 'UNKNOWN')
            base_tier, _ = parse_liquidity_tier(tier_raw)
            return (
                x['earnings_date'],          # Sort by date (ascending - soonest first)
                -x['_quality_score'],        # Then by Quality Score (descending - highest first)
                LIQUIDITY_PRIORITY_ORDER.get(base_tier, 3)  # Then by liquidity (EXCELLENT first, REJECT last)
            )

        # Check if any result has OI-only indicator (market closed)
        has_oi_only = any(r.get('liquidity_tier', '').endswith('*') for r in tradeable)

        # Table rows with day separators
        prev_earnings_date = None
        for i, r in enumerate(sorted(tradeable, key=sort_key), 1):
            ticker = r['ticker']
            # Truncate ticker name to 20 chars at word boundary (don't split words)
            full_name = r.get('ticker_name', '') if r.get('ticker_name') else ''
            if len(full_name) <= 20:
                name = full_name
            else:
                # Find last space before position 20
                truncated = full_name[:20]
                last_space = truncated.rfind(' ')
                if last_space > 0:
                    # Truncate at last whole word
                    name = truncated[:last_space]
                else:
                    # No space found, just truncate (single long word)
                    name = truncated

            # Use pre-calculated quality score
            score_display = f"{r['_quality_score']:.1f}"

            vrp = f"{r['vrp_ratio']:.2f}x"
            implied = str(r['implied_move_pct'])
            edge = f"{r['edge_score']:.2f}"
            rec = r['recommendation'].upper()
            bias = r.get('directional_bias', 'NEUTRAL')  # NEW: Display directional bias
            earnings = r['earnings_date']

            # Add separator between different earnings dates
            if prev_earnings_date is not None and earnings != prev_earnings_date:
                logger.info(f"   {'-'*3} {'-'*8} {'-'*20} {'-'*7} {'-'*8} {'-'*9} {'-'*7} {'-'*15} {'-'*15} {'-'*12} {'-'*12}")
            prev_earnings_date = earnings

            # Use helper function for consistent liquidity display
            liquidity_tier = r.get('liquidity_tier', 'UNKNOWN')
            liq_display = format_liquidity_display(liquidity_tier)

            logger.info(
                f"   {i:<3} {ticker:<8} {name:<20} {score_display:<7} {vrp:<8} {implied:<9} {edge:<7} {rec:<15} {bias:<15} {earnings:<12} {liq_display:<12}"
            )

        # Add footer note if market closed (OI-only scoring)
        if has_oi_only:
            logger.info(f"\n   * Liquidity based on OI only (market closed, volume unavailable)")

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
        "--parallel",
        action="store_true",
        help="Enable parallel processing for ~5x speedup (uses thread pool)"
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
            return scanning_mode(
                container, scan_date, args.expiration_offset,
                parallel=args.parallel
            )
        elif args.whisper_week is not None:
            # whisper_week can be '' (empty string) for current week or a date string
            week_monday = args.whisper_week if args.whisper_week else None
            return whisper_mode(
                container,
                week_monday=week_monday,
                fallback_image=args.fallback_image,
                expiration_offset=args.expiration_offset,
                parallel=args.parallel
            )
        else:
            tickers = [t.strip().upper() for t in args.tickers.split(',')]
            return ticker_mode(
                container, tickers, args.expiration_offset,
                parallel=args.parallel
            )

    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
        return 130

    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
