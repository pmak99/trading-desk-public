"""
Market data lookups - stock price fetching, market cap, company names, and liquidity checks.

Provides yfinance-based ticker info and Tradier-based liquidity tier classification.
"""

import logging
import threading
import time
from datetime import date
from typing import Dict, Optional, Tuple

from src.container import Container
from src.infrastructure.cache.hybrid_cache import HybridCache

from .constants import (
    API_CALL_DELAY,
    CACHE_L1_TTL_SECONDS,
    CACHE_L2_TTL_SECONDS,
    CACHE_MAX_L1_SIZE,
    _COMPANY_SUFFIX_PATTERNS,
    _TRAILING_AMPERSAND_PATTERN,
)

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


# Module-level caches and state
_ticker_info_cache: Dict[str, Tuple[Optional[float], Optional[str]]] = {}  # Combined cache for market cap + name
_liquidity_cache: Dict[Tuple[str, date], Tuple[bool, str]] = {}  # Cache for liquidity checks (ticker, expiration) -> (has_liq, tier)
_hybrid_liquidity_cache: Dict[Tuple[str, date, float], Tuple[bool, str, Dict]] = {}
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


# ---------------------------------------------------------------------------
# Harvest chain validation — checks real bid/OI/spread credit at ~45 DTE
# ---------------------------------------------------------------------------

def _harvest_target_expiry(reference_date: date, min_dte: int = 30, target_dte: int = 45) -> date:
    """
    Compute the nearest standard monthly expiry (3rd Friday of month) that is
    >= min_dte from reference_date and closest to target_dte.
    """
    from datetime import timedelta
    candidates = []
    for months_ahead in range(1, 5):
        year = reference_date.year
        month = reference_date.month + months_ahead
        while month > 12:
            month -= 12
            year += 1
        first_day = date(year, month, 1)
        # 4 = Friday (weekday index)
        first_friday_offset = (4 - first_day.weekday()) % 7
        third_friday = first_day + timedelta(days=first_friday_offset + 14)
        dte = (third_friday - reference_date).days
        if dte >= min_dte:
            candidates.append((abs(dte - target_dte), third_friday))
    if candidates:
        candidates.sort()
        return candidates[0][1]
    # Fallback
    return reference_date + timedelta(days=target_dte)


def _find_nearest_strike(options_dict: dict, target_strike: float, tolerance: float = 7.5) -> object | None:
    """Find the Strike key in an options dict closest to target_strike float."""
    best = None
    best_diff = float('inf')
    for strike in options_dict:
        diff = abs(float(strike.price) - target_strike)
        if diff < best_diff:
            best_diff = diff
            best = strike
    return best if (best is not None and best_diff <= tolerance) else None


def _dynamic_spread_width(stock_price: float) -> float:
    """
    Compute appropriate spread width based on stock price.
    A $5 spread on a $400 stock is only 1.25% — too tight for meaningful credit.
    Scale up for higher-priced stocks to ensure the wing option exists and
    the spread credit is worth the ticket.
    """
    if stock_price <= 60:
        return 2.50
    if stock_price <= 200:
        return 5.0
    if stock_price <= 400:
        return 10.0
    return 25.0


def validate_harvest_chain(
    ticker: str,
    expiry: date,
    container: Container,
    target_delta: float = 0.30,
    min_bid: float = 0.50,
    min_oi: int = 50,
) -> dict:
    """
    Validate actual options chain liquidity and premium for a harvest candidate.

    Fetches the Tradier chain at expiry, finds the OTM put and call closest to
    target_delta. Spread width is computed dynamically based on stock price
    (2.50 for stocks ≤$60, $5 for ≤$200, $10 for ≤$400, $25 for higher).

    Args:
        ticker: Stock ticker symbol.
        expiry: Options expiration date (~45 DTE).
        container: DI container (Tradier client).
        target_delta: Abs delta to target on short leg (default 0.30).
        min_bid: Minimum acceptable bid on the short leg (default $0.50).
        min_oi: Minimum acceptable OI on the short leg (default 50).

    Returns:
        dict with keys:
            put_bid, put_oi, put_credit, call_bid, call_oi, call_credit,
            best_credit (float), best_side ('PUT'/'CALL'/'SKIP'),
            liq_tier ('GOOD'/'WARN'/'THIN'), stock_price (float),
            spread_width (float), short_strike (float | None), error (str | None)
    """
    result = {
        'put_bid': 0.0, 'put_oi': 0, 'put_credit': 0.0,
        'call_bid': 0.0, 'call_oi': 0, 'call_credit': 0.0,
        'best_credit': 0.0, 'best_side': 'SKIP',
        'liq_tier': 'THIN', 'stock_price': 0.0,
        'spread_width': 5.0, 'short_strike': None, 'error': None,
    }
    try:
        tradier = container.tradier
        chain_result = tradier.get_option_chain(ticker, expiry)
        if chain_result.is_err:
            result['error'] = str(chain_result.error)
            return result

        chain = chain_result.value
        stock_price = float(chain.stock_price.amount)
        result['stock_price'] = stock_price
        spread_width = _dynamic_spread_width(stock_price)
        result['spread_width'] = spread_width

        # --- PUT side: find OTM put closest to -target_delta ---
        best_put_strike = None
        best_put_quote = None
        best_diff = float('inf')
        for strike, quote in chain.puts.items():
            if quote.delta is None:
                continue
            diff = abs(abs(quote.delta) - target_delta)
            if diff < best_diff:
                best_diff = diff
                best_put_strike = strike
                best_put_quote = quote

        if best_put_quote is not None:
            result['put_bid'] = float(best_put_quote.bid.amount)
            result['put_oi'] = best_put_quote.open_interest
            wing_target = float(best_put_strike.price) - spread_width
            wing_key = _find_nearest_strike(chain.puts, wing_target, tolerance=spread_width)
            if wing_key is not None:
                wing_quote = chain.puts[wing_key]
                credit = float(best_put_quote.bid.amount) - float(wing_quote.ask.amount)
                result['put_credit'] = max(0.0, round(credit, 2))

        # --- CALL side: find OTM call closest to +target_delta ---
        best_call_strike = None
        best_call_quote = None
        best_diff = float('inf')
        for strike, quote in chain.calls.items():
            if quote.delta is None:
                continue
            diff = abs(quote.delta - target_delta)
            if diff < best_diff:
                best_diff = diff
                best_call_strike = strike
                best_call_quote = quote

        if best_call_quote is not None:
            result['call_bid'] = float(best_call_quote.bid.amount)
            result['call_oi'] = best_call_quote.open_interest
            wing_target = float(best_call_strike.price) + spread_width
            wing_key = _find_nearest_strike(chain.calls, wing_target, tolerance=spread_width)
            if wing_key is not None:
                wing_quote = chain.calls[wing_key]
                credit = float(best_call_quote.bid.amount) - float(wing_quote.ask.amount)
                result['call_credit'] = max(0.0, round(credit, 2))

        # --- Choose best side and tier ---
        put_ok  = result['put_bid']  >= min_bid and result['put_oi']  >= min_oi
        call_ok = result['call_bid'] >= min_bid and result['call_oi'] >= min_oi

        if result['put_credit'] >= result['call_credit'] and put_ok:
            result['best_side']    = 'PUT'
            result['best_credit']  = result['put_credit']
            result['short_strike'] = float(best_put_strike.price) if best_put_strike else None
        elif call_ok:
            result['best_side']    = 'CALL'
            result['best_credit']  = result['call_credit']
            result['short_strike'] = float(best_call_strike.price) if best_call_strike else None
        else:
            result['best_side']   = 'SKIP'
            result['best_credit'] = max(result['put_credit'], result['call_credit'])

        # Liquidity tier
        best_bid = max(result['put_bid'], result['call_bid'])
        best_oi  = max(result['put_oi'],  result['call_oi'])
        if best_bid >= 1.00 and best_oi >= 200:
            result['liq_tier'] = 'GOOD'
        elif best_bid >= min_bid and best_oi >= min_oi:
            result['liq_tier'] = 'WARN'
        else:
            result['liq_tier'] = 'THIN'

    except Exception as e:
        logger.warning(f"{ticker}: harvest chain validation failed: {e}")
        result['error'] = str(e)

    return result
