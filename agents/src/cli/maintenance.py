#!/usr/bin/env python
"""CLI wrapper for /maintenance command.

System monitoring and data quality operations.

Usage:
    python -m src.cli.maintenance health              # Health check
    python -m src.cli.maintenance data-quality        # Database integrity check (report mode)
    python -m src.cli.maintenance data-quality --dry-run  # Show what would be fixed
    python -m src.cli.maintenance data-quality --fix  # Apply safe fixes automatically
    python -m src.cli.maintenance cache-cleanup       # Clean old cache entries
    python -m src.cli.maintenance sector-sync         # Sync sector metadata for upcoming earnings
"""

import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta

# Configure logging for CLI output
logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',  # Clean format for CLI
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Import 6.0 modules
# Note: Namespace collision with 2.0/src is handled by deferring 2.0 imports
# until inside Container2_0 class (after sys.path is properly configured)
from src.agents.health import HealthCheckAgent
from src.integration.container_2_0 import Container2_0
from src.integration.cache_4_0 import Cache4_0


def main():
    """Execute maintenance operations."""
    # Parse arguments - extract task and flags
    args = sys.argv[1:]

    # Separate task from flags
    task = 'health'  # default
    flags = []
    for arg in args:
        if arg.startswith('--'):
            flags.append(arg)
        elif task == 'health':  # First non-flag argument is the task
            task = arg

    # Validate - only data-quality accepts flags
    if flags and task != 'data-quality':
        logger.error(f"Error: Flags not supported for task '{task}'")
        logger.info("")
        logger.info("Usage: ./agent.sh maintenance [task]")
        logger.info("Available tasks: health, data-quality, cache-cleanup, sector-sync")
        logger.info("")
        logger.info("Flags (data-quality only):")
        logger.info("  --dry-run   Show what would be fixed without applying")
        logger.info("  --fix       Apply safe fixes automatically")
        logger.info("")
        logger.info("Examples:")
        logger.info("  ./agent.sh maintenance health")
        logger.info("  ./agent.sh maintenance data-quality")
        logger.info("  ./agent.sh maintenance data-quality --dry-run")
        logger.info("  ./agent.sh maintenance data-quality --fix")
        logger.info("  ./agent.sh maintenance cache-cleanup")
        logger.info("  ./agent.sh maintenance sector-sync")
        sys.exit(1)

    # Print header
    logger.info("=" * 60)
    logger.info(f"MAINTENANCE: {task.upper()}")
    logger.info("=" * 60)
    logger.info("")

    if task == 'health':
        run_health_check()
    elif task == 'data-quality':
        # Check for flags
        fix_mode = '--fix' in flags
        dry_run = '--dry-run' in flags

        if fix_mode and dry_run:
            logger.error("Cannot use both --fix and --dry-run")
            sys.exit(1)

        mode = "fix" if fix_mode else ("dry-run" if dry_run else "report")
        run_data_quality_v2(mode)
    elif task == 'cache-cleanup':
        run_cache_cleanup()
    elif task == 'sector-sync':
        run_sector_sync()
    else:
        logger.error(f"Unknown maintenance task: {task}")
        logger.info("")
        logger.info("Available tasks: health, data-quality, cache-cleanup, sector-sync")
        logger.info("")
        logger.info("Flags (data-quality only):")
        logger.info("  --dry-run   Show what would be fixed without applying")
        logger.info("  --fix       Apply safe fixes automatically")
        logger.info("")
        logger.info("Examples:")
        logger.info("  ./agent.sh maintenance health")
        logger.info("  ./agent.sh maintenance data-quality")
        logger.info("  ./agent.sh maintenance data-quality --dry-run")
        logger.info("  ./agent.sh maintenance data-quality --fix")
        logger.info("  ./agent.sh maintenance cache-cleanup")
        logger.info("  ./agent.sh maintenance sector-sync")
        sys.exit(1)


def run_health_check():
    """Run system health check."""
    try:
        agent = HealthCheckAgent()
        result = agent.check_health()

        # Print results
        logger.info(f"Overall Status: {result['status'].upper()}")
        logger.info("")

        # APIs
        logger.info("API Health:")
        for api_name, api_status in result['apis'].items():
            status = api_status['status']
            latency = api_status.get('latency_ms')
            error = api_status.get('error')

            status_symbol = '✅' if status == 'ok' else '❌'
            logger.info(f"  {status_symbol} {api_name}: {status}")

            if latency:
                logger.info(f"     Latency: {latency}ms")
            if error:
                logger.error(f"     Error: {error}")

        logger.info("")

        # Database
        logger.info("Database Health:")
        db_status = result['database']
        status = db_status['status']
        status_symbol = '✅' if status == 'ok' else '❌'
        logger.info(f"  {status_symbol} Status: {status}")

        if db_status.get('size_mb'):
            logger.info(f"  Size: {db_status['size_mb']} MB")
        if db_status.get('historical_moves'):
            logger.info(f"  Historical moves: {db_status['historical_moves']}")
        if db_status.get('earnings_calendar'):
            logger.info(f"  Earnings calendar: {db_status['earnings_calendar']}")

        if db_status.get('error'):
            logger.error(f"  Error: {db_status['error']}")

        logger.info("")

        # Budget
        logger.info("Budget Status:")
        budget = result['budget']
        logger.info(f"  Daily: {budget['daily_calls']}/{budget['daily_limit']} calls")
        logger.info(f"  Monthly: ${budget['monthly_cost']:.2f}/${budget['monthly_budget']:.2f}")

        daily_remaining = budget['daily_limit'] - budget['daily_calls']
        monthly_remaining = budget['monthly_budget'] - budget['monthly_cost']

        logger.info(f"  Remaining: {daily_remaining} calls, ${monthly_remaining:.2f}")

        logger.info("")
        logger.info("=" * 60)

        # Exit code based on status
        if result['status'] == 'healthy':
            sys.exit(0)
        else:
            sys.exit(1)

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def run_data_quality():
    """Run database integrity and data quality checks."""
    try:
        container = Container2_0()

        logger.info("[1/5] Checking historical moves data...")

        # Get all tickers with historical data
        all_moves = container.get_all_historical_moves()

        # Group by ticker
        ticker_moves = {}
        for move in all_moves:
            ticker = move.get('ticker')
            if ticker not in ticker_moves:
                ticker_moves[ticker] = []
            ticker_moves[ticker].append(move)

        # Analysis counters
        insufficient_data = []  # <4 quarters
        outliers = []  # moves >50%
        duplicates = []

        logger.info(f"  Total tickers: {len(ticker_moves)}")
        logger.info(f"  Total moves: {len(all_moves)}")
        logger.info("")

        # Check each ticker
        logger.info("[2/5] Analyzing data quality...")
        for ticker, moves in ticker_moves.items():
            # Check 1: Insufficient data (<4 quarters)
            if len(moves) < 4:
                insufficient_data.append({
                    'ticker': ticker,
                    'quarters': len(moves)
                })

            # Check 2: Outliers (moves >50%)
            for move in moves:
                gap_move = abs(move.get('gap_move_pct', 0))
                if gap_move > 50.0:
                    outliers.append({
                        'ticker': ticker,
                        'date': move.get('earnings_date'),
                        'move': gap_move
                    })

        # Check 3: Duplicates (same ticker + date)
        logger.info("[3/5] Checking for duplicates...")
        seen = set()
        for move in all_moves:
            key = (move.get('ticker'), move.get('earnings_date'))
            if key in seen:
                duplicates.append(key)
            seen.add(key)

        logger.info("")

        # Report findings
        logger.info("[4/5] Data Quality Report:")
        logger.info("")

        # Insufficient data
        if insufficient_data:
            logger.warning(f"⚠️  {len(insufficient_data)} tickers with <4 quarters:")
            for item in sorted(insufficient_data, key=lambda x: x['quarters'])[:10]:
                logger.info(f"  - {item['ticker']}: {item['quarters']} quarters")
            if len(insufficient_data) > 10:
                logger.info(f"  ... and {len(insufficient_data) - 10} more")
        else:
            logger.info("✅ All tickers have ≥4 quarters of data")
        logger.info("")

        # Outliers
        if outliers:
            logger.warning(f"⚠️  {len(outliers)} outlier moves (>50%):")
            for item in sorted(outliers, key=lambda x: x['move'], reverse=True)[:10]:
                logger.info(f"  - {item['ticker']} ({item['date']}): {item['move']:.1f}%")
            if len(outliers) > 10:
                logger.info(f"  ... and {len(outliers) - 10} more")
        else:
            logger.info("✅ No extreme outliers detected")
        logger.info("")

        # Duplicates
        if duplicates:
            logger.error(f"❌ {len(duplicates)} duplicate entries:")
            for ticker, date in list(duplicates)[:10]:
                logger.info(f"  - {ticker} on {date}")
            if len(duplicates) > 10:
                logger.info(f"  ... and {len(duplicates) - 10} more")
        else:
            logger.info("✅ No duplicate entries found")
        logger.info("")

        # Recommendations
        logger.info("[5/5] Recommendations:")
        logger.info("")

        if insufficient_data:
            logger.info("- Run backfill for tickers with <4 quarters")
            logger.info("  Command: cd ../2.0 && ./trade.sh backfill <TICKER>")

        if outliers:
            logger.info("- Review outlier moves for data entry errors")
            logger.info("  Manual verification recommended for moves >50%")

        if duplicates:
            logger.info("- Remove duplicate entries from database")
            logger.info("  SQL: DELETE FROM historical_moves WHERE rowid NOT IN")
            logger.info("       (SELECT MIN(rowid) FROM historical_moves")
            logger.info("        GROUP BY ticker, earnings_date)")

        if not (insufficient_data or outliers or duplicates):
            logger.info("✅ No issues found - database is healthy!")

        logger.info("")
        logger.info("=" * 60)

        # Exit code based on findings
        if duplicates:
            sys.exit(1)  # Critical issue
        elif insufficient_data or outliers:
            sys.exit(0)  # Warnings only
        else:
            sys.exit(0)  # All good

    except Exception as e:
        logger.info(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def run_data_quality_v2(mode: str):
    """Run data quality with DataQualityAgent."""
    try:
        from src.agents.data_quality import DataQualityAgent

        agent = DataQualityAgent()

        logger.info(f"[1/3] Running data quality scan (mode: {mode})...")
        result = agent.run(mode=mode)

        logger.info("")
        logger.info("[2/3] Results:")
        logger.info("")

        # Fixable issues
        fixable = result.get('fixable_issues', [])
        if fixable:
            logger.info(f"Fixable issues ({sum(i['count'] for i in fixable)}):")
            for issue in fixable:
                logger.info(f"  - {issue['type']}: {issue['count']} items")
                logger.info(f"    Action: {issue['fix_action']}")
        else:
            logger.info("No fixable issues found")
        logger.info("")

        # Flagged issues
        flagged = result.get('flagged_issues', [])
        if flagged:
            logger.warning(f"Flagged for manual review ({sum(i['count'] for i in flagged)}):")
            for issue in flagged:
                logger.warning(f"  - {issue['type']}: {issue['count']} items")
                logger.info(f"    Reason: {issue['reason']}")
        else:
            logger.info("No issues flagged for review")
        logger.info("")

        # Actions taken
        if mode == "fix":
            fixed = result.get('fixed_issues', [])
            if fixed:
                logger.info("Actions taken:")
                for action in fixed:
                    logger.info(f"  ✓ {action}")
            else:
                logger.info("No fixes applied")
        elif mode == "dry-run":
            would_fix = result.get('would_fix', [])
            if would_fix:
                logger.info("Would fix (dry-run):")
                for issue in would_fix:
                    logger.info(f"  - {issue['type']}: {issue['count']} items")

        logger.info("")
        logger.info("[3/3] Summary:")
        summary = result.get('summary', {})
        logger.info(f"  Fixable: {summary.get('total_fixable', 0)}")
        logger.info(f"  Flagged: {summary.get('total_flagged', 0)}")
        if mode == "fix":
            logger.info(f"  Fixed: {summary.get('total_fixed', 0)}")

        logger.info("")

        if mode == "report" and summary.get('total_fixable', 0) > 0:
            logger.info("Run with --fix to apply fixes, or --dry-run to preview")

        logger.info("")
        logger.info("=" * 60)

        sys.exit(0)

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def run_cache_cleanup():
    """Clean old cache entries and report statistics."""
    try:
        cache = Cache4_0()

        logger.info("[1/4] Analyzing sentiment cache...")

        # Get cache statistics
        cache_stats = cache.get_cache_stats()

        total_entries = cache_stats.get('total_entries', 0)
        stale_entries = cache_stats.get('stale_entries', 0)  # >3 hours
        hit_rate = cache_stats.get('hit_rate')

        logger.info(f"  Total entries: {total_entries}")
        logger.info(f"  Stale entries (>3h): {stale_entries}")
        if hit_rate is not None:
            logger.info(f"  Cache hit rate: {hit_rate:.1f}%")
        else:
            logger.info(f"  Cache hit rate: Not yet tracked")
        logger.info("")

        # Cleanup sentiment cache
        logger.info("[2/4] Cleaning sentiment cache...")
        cleaned_sentiment = cache.cleanup_sentiment_cache(max_age_hours=3)
        logger.info(f"  ✓ Removed {cleaned_sentiment} stale entries")
        logger.info("")

        # Cleanup budget tracker
        logger.info("[3/4] Cleaning budget tracker...")
        cleaned_budget = cache.cleanup_budget_tracker(max_age_days=30)
        logger.info(f"  ✓ Removed {cleaned_budget} old entries (>30 days)")
        logger.info("")

        # Calculate disk space freed (rough estimate)
        # Assume ~1KB per cache entry
        disk_freed_kb = (cleaned_sentiment + cleaned_budget) * 1
        disk_freed_mb = disk_freed_kb / 1024

        logger.info("[4/4] Summary:")
        logger.info("")
        logger.info(f"  Sentiment cache: {cleaned_sentiment} entries removed")
        logger.info(f"  Budget tracker: {cleaned_budget} entries removed")
        logger.info(f"  Disk space freed: ~{disk_freed_mb:.2f} MB")
        logger.info(f"  Note: Run VACUUM on databases to reclaim space on disk")
        logger.info("")

        if hit_rate is not None and total_entries > 0:
            logger.info(f"  Cache efficiency: {hit_rate:.1f}% hit rate")
            if hit_rate < 50:
                logger.info("  ⚠️  Low cache hit rate - consider pre-caching with /prime")
        elif total_entries > 0:
            logger.info(f"  Note: Cache hit rate tracking not yet implemented")

        logger.info("")
        logger.info("=" * 60)

        sys.exit(0)

    except Exception as e:
        logger.info(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def run_sector_sync():
    """Sync sector data from Finnhub for upcoming earnings."""
    try:
        from src.agents.sector_fetch import SectorFetchAgent
        from src.integration.container_2_0 import Container2_0

        logger.info("[1/4] Getting upcoming earnings...")

        container = Container2_0()
        result = container.get_upcoming_earnings(days_ahead=30)

        # Extract tickers
        if hasattr(result, 'value'):
            earnings_list = result.value
        else:
            earnings_list = result

        tickers = [t for t, _ in earnings_list]
        logger.info(f"  Found {len(tickers)} tickers with upcoming earnings")
        logger.info("")

        logger.info("[2/4] Checking existing metadata...")
        agent = SectorFetchAgent()

        # Check which need fetching
        need_fetch = []
        have_cached = []
        for ticker in tickers:
            cached = agent.metadata_repo.get_metadata(ticker)
            if cached:
                have_cached.append(ticker)
            else:
                need_fetch.append(ticker)

        logger.info(f"  Already cached: {len(have_cached)}")
        logger.info(f"  Need to fetch: {len(need_fetch)}")
        logger.info("")

        if not need_fetch:
            logger.info("[3/4] All tickers already have metadata")
            logger.info("")
            logger.info("[4/4] Summary:")
            logger.info(f"  Total tickers: {len(tickers)}")
            logger.info(f"  Cached: {len(have_cached)}")
            logger.info(f"  Fetched: 0")
            logger.info("")
            logger.info("=" * 60)
            sys.exit(0)

        logger.info(f"[3/4] Fetching sector data for {len(need_fetch)} tickers...")
        logger.info("  (Rate limited: 1 request/second for Finnhub)")
        logger.info("")

        # Note: This is a placeholder. Real implementation would use
        # the Finnhub MCP tool. For now, just log what would be fetched.
        logger.info("  Note: Finnhub integration pending.")
        logger.info("  Tickers needing data:")
        for ticker in need_fetch[:10]:
            logger.info(f"    - {ticker}")
        if len(need_fetch) > 10:
            logger.info(f"    ... and {len(need_fetch) - 10} more")

        logger.info("")
        logger.info("[4/4] Summary:")
        logger.info(f"  Total tickers: {len(tickers)}")
        logger.info(f"  Cached: {len(have_cached)}")
        logger.info(f"  Pending Finnhub fetch: {len(need_fetch)}")
        logger.info("")
        logger.info("=" * 60)

        sys.exit(0)

    except Exception as e:
        logger.error(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
