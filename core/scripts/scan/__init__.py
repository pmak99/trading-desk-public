"""
IV Crush Scanner Package.

Decomposed from the monolithic scan.py into focused modules:
- cli: Argument parsing
- constants: All hardcoded thresholds, scoring weights, configuration
- date_utils: Trading day calculations, expiration date logic
- market_data: Market cap lookups, stock price fetching, liquidity checks
- filters: Ticker filtering logic
- quality_scorer: Composite quality scoring (VRP + liquidity + difficulty)
- formatters: Result display/formatting (tables, colors, summaries)
- earnings_fetcher: Earnings source aggregation (AlphaVantage, Yahoo, DB)
- workflows: Sequential and parallel scan orchestration
"""

# Re-export public API for backward compatibility
# Tests and other scripts import from scripts.scan

# Constants (used by tests)
from .constants import (
    SCORE_VRP_MAX_POINTS,
    SCORE_VRP_TARGET,
    SCORE_EDGE_MAX_POINTS,
    SCORE_EDGE_TARGET,
    SCORE_LIQUIDITY_MAX_POINTS,
    SCORE_LIQUIDITY_EXCELLENT_POINTS,
    SCORE_LIQUIDITY_GOOD_POINTS,
    SCORE_LIQUIDITY_WARNING_POINTS,
    SCORE_LIQUIDITY_REJECT_POINTS,
    SCORE_MOVE_MAX_POINTS,
    SCORE_MOVE_EASY_THRESHOLD,
    SCORE_MOVE_MODERATE_THRESHOLD,
    SCORE_MOVE_MODERATE_POINTS,
    SCORE_MOVE_CHALLENGING_THRESHOLD,
    SCORE_MOVE_CHALLENGING_POINTS,
    SCORE_MOVE_EXTREME_POINTS,
    SCORE_DEFAULT_MOVE_POINTS,
    SCORE_MOVE_USE_CONTINUOUS,
    SCORE_MOVE_BASELINE_PCT,
    SCORE_VRP_USE_LINEAR,
    LIQUIDITY_PRIORITY_ORDER,
    MARKET_CLOSED_INDICATOR,
    ALPHA_VANTAGE_CALLS_PER_MINUTE,
    RATE_LIMIT_PAUSE_SECONDS,
    CACHE_L1_TTL_SECONDS,
    CACHE_L2_TTL_SECONDS,
    CACHE_MAX_L1_SIZE,
    BACKFILL_TIMEOUT_SECONDS,
    BACKFILL_YEARS,
    MAX_TRADING_DAY_ITERATIONS,
    API_CALL_DELAY,
)

# Date utilities (used by tests)
from .date_utils import (
    parse_date,
    get_us_market_holidays,
    is_market_holiday,
    adjust_to_trading_day,
    get_next_friday,
    calculate_implied_move_expiration,
    calculate_expiration_date,
    validate_expiration_date,
)

# Quality scoring (used by tests)
from .quality_scorer import (
    calculate_scan_quality_score,
    _precalculate_quality_scores,
)

# Formatters (used by tests and other modules)
from .formatters import (
    parse_liquidity_tier,
    format_liquidity_display,
    _display_scan_results,
)

# Market data
from .market_data import (
    get_ticker_info,
    get_market_cap_millions,
    get_ticker_name,
    clean_company_name,
    check_liquidity_with_tier,
    check_basic_liquidity,
    get_liquidity_tier_for_display,
    check_liquidity_hybrid,
    get_shared_cache,
)

# Filters
from .filters import (
    should_filter_ticker,
    filter_ticker_concurrent,
)

# Earnings fetcher
from .earnings_fetcher import (
    fetch_earnings_for_date,
    fetch_earnings_for_ticker,
    validate_tradeable_earnings_dates,
    ensure_tickers_in_db,
)

# Workflows (used by scan_async.py)
from .workflows import (
    analyze_ticker,
    analyze_ticker_concurrent,
    scanning_mode,
    scanning_mode_parallel,
    ticker_mode,
    ticker_mode_parallel,
    whisper_mode,
    whisper_mode_parallel,
)

# CLI
from .cli import parse_args
