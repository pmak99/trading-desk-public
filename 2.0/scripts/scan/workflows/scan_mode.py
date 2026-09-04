"""scanning_mode — scan all earnings for a specific date (sequential and parallel)."""
import functools
import logging
import sys
from datetime import date
from typing import Dict, Optional, Tuple

from tqdm import tqdm

from src.container import Container

from ..constants import LIQUIDITY_PRIORITY_ORDER
from ..date_utils import calculate_expiration_date
from ..filters import should_filter_ticker, filter_ticker_concurrent
from ..quality_scorer import _precalculate_quality_scores
from ..formatters import parse_liquidity_tier, format_liquidity_display, _display_scan_results
from ..earnings_fetcher import fetch_earnings_for_date

from .vix import _log_vix_context
from .ticker_analysis import analyze_ticker, analyze_ticker_concurrent

logger = logging.getLogger(__name__)

def scanning_mode_parallel(
    container: Container,
    scan_date: date,
    expiration_offset: Optional[int] = None,
    skip_weekly_filter: bool = False
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
    _log_vix_context(container)
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
    # Bind skip_weekly_filter to analyze function for weekly options filter
    analyze_func = functools.partial(analyze_ticker_concurrent, skip_weekly_filter=skip_weekly_filter)
    scanner = container.concurrent_scanner
    batch_result = scanner.scan_tickers(
        tickers=tickers,
        earnings_lookup=earnings_lookup,
        analyze_func=analyze_func,
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
    logger.info(f"\n\U0001f4ca Parallel Scan Complete:")
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
    parallel: bool = False,
    skip_weekly_filter: bool = False
) -> int:
    """
    Scanning mode: Scan earnings for a specific date.

    Args:
        container: DI container
        scan_date: Target earnings date
        expiration_offset: Custom expiration offset in days
        parallel: If True, use parallel processing (5x speedup)
        skip_weekly_filter: If True, skip weekly options filter

    Returns exit code (0 for success, 1 for error)
    """
    # Use parallel mode if requested
    if parallel:
        return scanning_mode_parallel(container, scan_date, expiration_offset, skip_weekly_filter)

    logger.info("=" * 80)
    logger.info("SCANNING MODE: Earnings Date Scan")
    logger.info("=" * 80)
    logger.info(f"Scan Date: {scan_date}")
    _log_vix_context(container)
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

    min_dte = container.config.thresholds.min_dte

    for ticker, earnings_date, timing in pbar:
        pbar.set_postfix_str(f"Current: {ticker}")
        sys.stderr.flush()  # Force flush after each update

        # Calculate expiration date
        expiration_date = calculate_expiration_date(
            earnings_date, timing, expiration_offset, min_dte=min_dte
        )

        # Apply filters (market cap + liquidity) for scan mode
        filter_result, filter_reason, _, _ = should_filter_ticker(
            ticker, expiration_date, container,
            check_market_cap=True,
            check_liquidity=True
        )

        if filter_result:
            filtered_count += 1
            logger.info(f"\u23ed\ufe0f  {ticker}: Filtered ({filter_reason})")
            pbar.set_postfix_str(f"{ticker}: Filtered")
            sys.stderr.flush()
            continue

        # Analyze ticker (no auto-backfill in scan mode to avoid excessive delays)
        result = analyze_ticker(
            container,
            ticker,
            earnings_date,
            expiration_date,
            auto_backfill=False,
            skip_weekly_filter=skip_weekly_filter,
            earnings_timing=timing,
        )

        if result:
            results.append(result)
            if result['status'] == 'SUCCESS':
                success_count += 1
                pbar.set_postfix_str(f"{ticker}: \u2713 Complete")
            else:
                skip_count += 1
                pbar.set_postfix_str(f"{ticker}: No data")
        else:
            error_count += 1
            pbar.set_postfix_str(f"{ticker}: \u2717 Error")
        sys.stderr.flush()

    pbar.close()

    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("SCAN MODE - SUMMARY")
    logger.info("=" * 80)
    logger.info(f"\n\U0001f4c5 Scan Details:")
    logger.info(f"   Mode: Earnings Date Scan")
    logger.info(f"   Date: {scan_date}")
    logger.info(f"   Total Earnings Found: {len(earnings_events)}")
    logger.info(f"\n\U0001f4ca Analysis Results:")
    logger.info(f"   \U0001f50d Filtered (Market Cap Only): {filtered_count}")
    logger.info(f"   \u2713 Successfully Analyzed: {success_count}")
    logger.info(f"   \u23ed\ufe0f  Skipped (No Data): {skip_count}")
    logger.info(f"   \u2717 Errors: {error_count}")

    # Tradeable opportunities
    tradeable = [r for r in results if r.get('is_tradeable', False)]
    if tradeable:
        # Pre-calculate quality scores once (avoids ~82% duplicate calculations)
        _precalculate_quality_scores(tradeable)

        logger.info(f"\n" + "=" * 80)
        logger.info(f"\u2705 RESULT: {len(tradeable)} TRADEABLE OPPORTUNITIES FOUND")
        logger.info("=" * 80)
        logger.info(f"\n\U0001f3af Sorted by Quality Score (Risk-Adjusted):")

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

        logger.info(f"\n\U0001f4a1 Run './trade.sh TICKER YYYY-MM-DD' for detailed strategy recommendations")
    else:
        logger.info(f"\n" + "=" * 80)
        logger.info("\u23ed\ufe0f  RESULT: NO TRADEABLE OPPORTUNITIES")
        logger.info("=" * 80)
        logger.info(f"\n\u274c No opportunities found for {scan_date}")
        if skip_count > 0:
            logger.info(f"   Note: {skip_count} ticker(s) skipped due to missing historical data")
            logger.info(f"   Tip: Run individual analysis with auto-backfill using single ticker mode")
        logger.info(f"\n\U0001f4dd Recommendation:")
        logger.info(f"   Try scanning a different earnings date or check whisper mode for anticipated earnings")

    # Return 0 if we successfully completed the scan (even if some tickers had errors)
    # Only return 1 for fatal errors (calendar fetch failure, etc.)
    return 0


