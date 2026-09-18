#!/usr/bin/env python3
"""
IV Crush Scanner - Entry Point.

Thin wrapper that delegates to the scan package modules:
- scan/cli.py: Argument parsing
- scan/workflows/: Scanning, ticker, and whisper mode orchestration
- scan/constants.py: All scoring thresholds and configuration
- scan/quality_scorer.py: Composite quality scoring
- scan/date_utils.py: Date parsing and the expiration sanity check
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

    # Ticker mode for one earnings date, with a custom expiration offset
    python scripts/scan.py --tickers AAPL --earnings-date 2025-01-30 --expiration-offset 7
"""

import sys
from pathlib import Path

# Run by path: 2.0/ for src and the `scripts.*` namespace the scan
# package imports lazily (track_iv_weekly, store_bias_prediction).
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import everything from the scan package for backward compatibility.
# Tests and other scripts import from scripts.scan, so all public symbols
# must be accessible via this module.
from scan.cli import parse_args
from scan.date_utils import (
    parse_date,
)
from scan.workflows import (
    harvest_mode,
    scanning_mode,
    ticker_mode,
    whisper_mode,
)

from src.config.config import Config
from src.container import Container
from src.infrastructure.database.migrations import apply_migrations
from src.infrastructure.database.pg_pool import close_pool
from src.utils.logging import setup_logging
from src.utils.shutdown import register_shutdown_callback


def main():
    """Main entry point."""
    args = parse_args()

    # Setup logging
    setup_logging(level=args.log_level)

    try:
        # Load configuration; migrate once, before any database work (scan,
        # whisper and harvest write analysis_log and can auto-backfill).
        config = Config.from_env()
        apply_migrations()
        container = Container(config)

        # Register graceful shutdown callbacks
        def shutdown_cleanup():
            """Cleanup on shutdown."""
            close_pool()

        register_shutdown_callback(shutdown_cleanup)

        # Execute appropriate mode
        # Parallel is the default; use --sequential to disable
        use_parallel = not args.sequential

        if args.harvest:
            return harvest_mode(container=container)
        elif args.scan_date:
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
                expiration_offset=args.expiration_offset,
                parallel=use_parallel,
                skip_weekly_filter=args.skip_weekly_filter
            )
        else:
            tickers = [t.strip().upper() for t in args.tickers.split(',')]
            return ticker_mode(
                container, tickers, args.expiration_offset,
                parallel=use_parallel,
                skip_weekly_filter=args.skip_weekly_filter,
                earnings_date=args.earnings_date,
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
