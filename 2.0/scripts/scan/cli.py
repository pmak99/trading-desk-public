"""
Argument parsing for the IV Crush Scanner CLI.

Provides the argparse setup for scan.py entry point.
"""

import argparse

from .date_utils import parse_date


def parse_args(args=None):
    """
    Parse command line arguments for the IV Crush Scanner.

    Args:
        args: Optional list of arguments (for testing). If None, uses sys.argv.

    Returns:
        Parsed argparse.Namespace object
    """
    parser = argparse.ArgumentParser(
        description="Scan for IV Crush trading opportunities",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Scanning mode - scan earnings for a specific date
    python scripts/scan.py --scan-date 2025-01-31

    # Ticker mode - analyze specific tickers (each ticker's next calendar date)
    python scripts/scan.py --tickers AAPL,MSFT,GOOGL

    # Ticker mode for one earnings date (what ./trade.sh list TICKERS DATE runs)
    python scripts/scan.py --tickers AAPL,MSFT --earnings-date 2025-01-30

    # Whisper mode - analyze most anticipated earnings (current week)
    python scripts/scan.py --whisper-week

    # Whisper mode - specific week (Monday)
    python scripts/scan.py --whisper-week 2025-11-10

    # Ticker mode with a custom expiration offset (days after the earnings date)
    python scripts/scan.py --tickers AAPL --expiration-offset 7

Expiration dates:
    - Trading expiration (liquidity, skew, strategies): calculate_expiration_date() in
      src/domain/trading_expiration.py picks it from the report's weekday, never under the
      minimum DTE (CLAUDE.md §5); the scan uses the first listed expiration on or after it.
    - --expiration-offset N: the earnings date + N days instead, raised to the minimum
      DTE when N is below it and moved to the next trading day.
    - Implied move and VRP: implied_move_expiration() in
      src/application/metrics/implied_move_expiration.py, the first listed expiration
      containing the reaction. The offset does not change it.

Notes:
    - Requires TRADIER_API_KEY and FINNHUB_API_KEY in .env
    - A ticker with no historical moves is backfilled automatically only when tickers
      are analyzed one at a time: --tickers with one ticker, or --tickers and
      --whisper-week with --sequential. Parallel runs and --scan-date report it as
      NO_HISTORICAL_DATA instead.
    - Manual backfill, for a ticker new to the system:
      python scripts/backfill_historical.py <TICKER> --execute
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
    mode_group.add_argument(
        "--harvest",
        action="store_true",
        help="30-45 DTE non-earnings premium harvest candidates (live IV/HV, index IVR > 25, no earnings within 5 days)"
    )

    # Options
    parser.add_argument(
        "--earnings-date",
        type=parse_date,
        help="With --tickers: analyze every ticker for earnings on this date (YYYY-MM-DD), "
             "with its stored BMO/AMC timing (UNKNOWN when the calendar has none), instead "
             "of each ticker's next calendar date"
    )
    parser.add_argument(
        "--expiration-offset",
        type=int,
        help="Trading expiration N days after the earnings date instead of the weekday rule "
             "(raised to the minimum DTE when lower)"
    )
    parser.add_argument(
        "--sequential",
        action="store_true",
        help="Disable parallel processing (use sequential mode for debugging)"
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level"
    )
    parser.add_argument(
        "--skip-weekly-filter",
        action="store_true",
        help="Skip weekly options filter (include tickers with only monthly options)"
    )

    parsed = parser.parse_args(args)
    if parsed.earnings_date is not None and parsed.tickers is None:
        parser.error("--earnings-date applies to --tickers only")
    return parsed
