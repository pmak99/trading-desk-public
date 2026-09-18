"""
IV Crush Scanner Package.

Decomposed from the monolithic scan.py into focused modules:
- cli: Argument parsing
- constants: All hardcoded thresholds, scoring weights, configuration
- date_utils: Date parsing and the expiration sanity check (the trading
  expiration and market calendar are src/domain's)
- market_data: Market cap lookups, stock price fetching, liquidity checks
- filters: Ticker filtering logic
- quality_scorer: Composite quality scoring (VRP + liquidity + difficulty)
- formatters: Result display/formatting (tables, colors, summaries)
- earnings_fetcher: Earnings source aggregation (Finnhub, Yahoo, DB)
- workflows: Sequential and parallel scan orchestration
"""

# Re-export public API for backward compatibility
# Tests and other scripts import from scripts.scan

# Constants (used by tests)
# CLI
from .cli import parse_args
from .constants import (
    BACKFILL_TIMEOUT_SECONDS,
    BACKFILL_YEARS,
    CACHE_L1_TTL_SECONDS,
    CACHE_L2_TTL_SECONDS,
    CACHE_MAX_L1_SIZE,
    FINNHUB_CALLS_PER_MINUTE,
    LIQUIDITY_PRIORITY_ORDER,
    MARKET_CLOSED_INDICATOR,
    RATE_LIMIT_PAUSE_SECONDS,
    SCORE_DEFAULT_MOVE_POINTS,
    SCORE_EDGE_MAX_POINTS,
    SCORE_EDGE_TARGET,
    SCORE_LIQUIDITY_EXCELLENT_POINTS,
    SCORE_LIQUIDITY_GOOD_POINTS,
    SCORE_LIQUIDITY_MAX_POINTS,
    SCORE_LIQUIDITY_REJECT_POINTS,
    SCORE_LIQUIDITY_WARNING_POINTS,
    SCORE_MOVE_BASELINE_PCT,
    SCORE_MOVE_CHALLENGING_POINTS,
    SCORE_MOVE_CHALLENGING_THRESHOLD,
    SCORE_MOVE_EASY_THRESHOLD,
    SCORE_MOVE_EXTREME_POINTS,
    SCORE_MOVE_MAX_POINTS,
    SCORE_MOVE_MODERATE_POINTS,
    SCORE_MOVE_MODERATE_THRESHOLD,
    SCORE_MOVE_USE_CONTINUOUS,
    SCORE_VRP_MAX_POINTS,
    SCORE_VRP_TARGET,
    SCORE_VRP_USE_LINEAR,
)

# Date utilities (used by tests)
from .date_utils import (
    parse_date,
    validate_expiration_date,
)

# Earnings fetcher
from .earnings_fetcher import (
    ensure_tickers_in_db,
    fetch_earnings_for_date,
    fetch_earnings_for_ticker,
    validate_tradeable_earnings_dates,
)

# Filters
from .filters import (
    filter_ticker_concurrent,
    should_filter_ticker,
)

# Formatters (used by tests and other modules)
from .formatters import (
    _display_scan_results,
    format_liquidity_display,
    parse_liquidity_tier,
)

# Market data
from .market_data import (
    check_basic_liquidity,
    check_liquidity_hybrid,
    check_liquidity_with_tier,
    clean_company_name,
    get_liquidity_tier_for_display,
    get_market_cap_millions,
    get_shared_cache,
    get_ticker_info,
    get_ticker_name,
)

# Quality scoring (used by tests)
from .quality_scorer import (
    _precalculate_quality_scores,
    calculate_scan_quality_score,
)

# Workflows (used by scan_async.py)
from .workflows import (
    # Data seam — lets non-CLI consumers (Telegram digest, A/B diff harness)
    # reach the analysis results instead of just an exit code.
    ScanRun,
    analyze_ticker,
    analyze_ticker_concurrent,
    collect_whisper_results,
    scanning_mode,
    scanning_mode_parallel,
    ticker_mode,
    ticker_mode_parallel,
    whisper_mode,
    whisper_mode_parallel,
)
