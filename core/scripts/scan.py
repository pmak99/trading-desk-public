#!/usr/bin/env python3
"""
IV Crush Scanner - Entry Point.

Thin wrapper that delegates to the scan package modules:
- scan/cli.py: Argument parsing
- scan/workflows.py: Scanning, ticker, and whisper mode orchestration
- scan/constants.py: All scoring thresholds and configuration
- scan/quality_scorer.py: Composite quality scoring
- scan/date_utils.py: Trading day and expiration calculations
- scan/market_data.py: Market cap, company name, liquidity lookups
- scan/filters.py: Ticker filtering logic
- scan/earnings_fetcher.py: Earnings calendar aggregation
- scan/formatters.py: Result display formatting

Usage:
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
"""

import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.utils.shutdown import register_shutdown_callback
from src.container import Container, reset_container
from src.config.config import Config

# Import everything from the scan package for backward compatibility.
# Tests and other scripts import from scripts.scan, so all public symbols
# must be accessible via this module.
from scan.cli import parse_args
from scan.workflows import (
    scanning_mode,
    scanning_mode_parallel,
    ticker_mode,
    ticker_mode_parallel,
    whisper_mode,
    whisper_mode_parallel,
    analyze_ticker,
    analyze_ticker_concurrent,
)
from scan.date_utils import (
    parse_date,
    get_us_market_holidays,
    is_market_holiday,
    adjust_to_trading_day,
    get_next_friday,
    calculate_implied_move_expiration,
    calculate_expiration_date,
    validate_expiration_date,
)
from scan.quality_scorer import (
    calculate_scan_quality_score,
    _precalculate_quality_scores,
)
from scan.formatters import (
    parse_liquidity_tier,
    format_liquidity_display,
    _display_scan_results,
)
from scan.market_data import (
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
from scan.filters import (
    should_filter_ticker,
    filter_ticker_concurrent,
)
from scan.earnings_fetcher import (
    fetch_earnings_for_date,
    fetch_earnings_for_ticker,
    validate_tradeable_earnings_dates,
    ensure_tickers_in_db,
)
from scan.constants import (
    ALPHA_VANTAGE_CALLS_PER_MINUTE,
    RATE_LIMIT_PAUSE_SECONDS,
    CACHE_L1_TTL_SECONDS,
    CACHE_L2_TTL_SECONDS,
    CACHE_MAX_L1_SIZE,
    BACKFILL_TIMEOUT_SECONDS,
    BACKFILL_YEARS,
    MAX_TRADING_DAY_ITERATIONS,
    API_CALL_DELAY,
    SCORE_VRP_MAX_POINTS,
    SCORE_VRP_TARGET,
    SCORE_VRP_USE_LINEAR,
    SCORE_EDGE_MAX_POINTS,
    SCORE_EDGE_TARGET,
    SCORE_LIQUIDITY_MAX_POINTS,
    SCORE_LIQUIDITY_EXCELLENT_POINTS,
    SCORE_LIQUIDITY_GOOD_POINTS,
    SCORE_LIQUIDITY_WARNING_POINTS,
    SCORE_LIQUIDITY_REJECT_POINTS,
    SCORE_MOVE_MAX_POINTS,
    SCORE_MOVE_USE_CONTINUOUS,
    SCORE_MOVE_BASELINE_PCT,
    MARKET_CLOSED_INDICATOR,
    SCORE_MOVE_EASY_THRESHOLD,
    SCORE_MOVE_MODERATE_THRESHOLD,
    SCORE_MOVE_MODERATE_POINTS,
    SCORE_MOVE_CHALLENGING_THRESHOLD,
    SCORE_MOVE_CHALLENGING_POINTS,
    SCORE_MOVE_EXTREME_POINTS,
    SCORE_DEFAULT_MOVE_POINTS,
    LIQUIDITY_PRIORITY_ORDER,
)


def main():
    """Main entry point."""
    args = parse_args()

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
        # Parallel is the default; use --sequential to disable
        use_parallel = not args.sequential

        if args.scan_date:
            scan_date = parse_date(args.scan_date)
            return scanning_mode(
                container, scan_date, args.expiration_offset,
                parallel=use_parallel,
                skip_weekly_filter=args.skip_weekly_filter
            )
        elif args.whisper_week is not None:
            # whisper_week can be '' (empty string) for current week or a date string
            week_monday = args.whisper_week if args.whisper_week else None
            return whisper_mode(
                container,
                week_monday=week_monday,
                fallback_image=args.fallback_image,
                expiration_offset=args.expiration_offset,
                parallel=use_parallel,
                skip_weekly_filter=args.skip_weekly_filter
            )
        else:
            tickers = [t.strip().upper() for t in args.tickers.split(',')]
            return ticker_mode(
                container, tickers, args.expiration_offset,
                parallel=use_parallel,
                skip_weekly_filter=args.skip_weekly_filter
            )

    except KeyboardInterrupt:
        import logging
        logger = logging.getLogger(__name__)
        logger.info("\nInterrupted by user")
        return 130

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Unexpected error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
