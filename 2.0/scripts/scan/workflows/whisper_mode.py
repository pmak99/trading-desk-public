"""whisper_mode — most anticipated earnings via EarningsWhisper scraper."""
import functools
import logging
import sys
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from tqdm import tqdm

from src.container import Container
from src.infrastructure.data_sources.earnings_whisper_scraper import (
    EarningsWhisperScraper,
    get_week_monday,
)

from ..constants import LIQUIDITY_PRIORITY_ORDER
from ..date_utils import calculate_expiration_date
from ..filters import should_filter_ticker, filter_ticker_concurrent
from ..quality_scorer import _precalculate_quality_scores
from ..formatters import parse_liquidity_tier, format_liquidity_display, _display_scan_results
from ..earnings_fetcher import (
    fetch_earnings_for_ticker,
    validate_tradeable_earnings_dates,
    ensure_tickers_in_db,
)
from ..market_data import get_shared_cache

from .vix import _log_vix_context
from .ticker_analysis import analyze_ticker, analyze_ticker_concurrent

logger = logging.getLogger(__name__)


def whisper_mode_parallel(
    container: Container,
    tickers: List[str],
    monday: date,
    week_end: date,
    expiration_offset: Optional[int] = None,
    skip_weekly_filter: bool = False
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
        skip_weekly_filter: If True, skip weekly options filter

    Returns:
        Exit code (0 = success, 1 = error)
    """
    logger.info("")
    logger.info("\U0001f680 Using PARALLEL processing for faster analysis...")
    _log_vix_context(container)

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
    # Bind skip_weekly_filter to analyze function for weekly options filter
    analyze_func = functools.partial(analyze_ticker_concurrent, skip_weekly_filter=skip_weekly_filter)
    scanner = container.concurrent_scanner
    batch_result = scanner.scan_tickers(
        tickers=list(earnings_lookup.keys()),
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

    # Validate earnings dates for tradeable results
    tradeable = [r for r in results if r.get('is_tradeable', False)]
    if tradeable:
        validate_tradeable_earnings_dates(tradeable, container)

    # Log statistics
    logger.info(f"\n\U0001f4ca Parallel Analysis Complete:")
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
    parallel: bool = False,
    skip_weekly_filter: bool = False
) -> int:
    """
    Whisper mode: Analyze most anticipated earnings.

    Fetches tickers from Earnings Whispers and analyzes each with auto-backfill.

    Args:
        container: DI container
        week_monday: Monday in YYYY-MM-DD (defaults to current week)
        fallback_image: Path to earnings screenshot (PNG/JPG)
        expiration_offset: Custom expiration offset in days
        parallel: If True, use parallel processing (5x speedup)
        skip_weekly_filter: If True, skip weekly options filter

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
    # Shared 6-day persistent cache (scan_cache.db) keeps repeated /whisper runs
    # within a week off earningswhispers.com's undocumented internal API — that
    # endpoint soft-rate-limits after a handful of rapid requests.
    scraper = EarningsWhisperScraper(cache=get_shared_cache(container))
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
        logger.info("   - Earnings Whispers scraper unavailable")
        logger.info("   - No anticipated earnings for this week")
        logger.info("   - Network connectivity issues")
        logger.info("")
        logger.info("\U0001f4dd Try:")
        logger.info("   - Use a different week: ./trade.sh whisper 2025-11-17")
        logger.info("   - Use scan mode: ./trade.sh scan 2025-11-20")
        return 1

    logger.info(f"✓ Retrieved {len(tickers)} most anticipated tickers")
    logger.info(f"Tickers: {', '.join(tickers)}")
    _log_vix_context(container)

    # Ensure all tickers are in database (auto-add + sync if needed)
    ensure_tickers_in_db(tickers, container)

    # Use parallel mode if requested
    if parallel:
        return whisper_mode_parallel(
            container, tickers, monday, week_end, expiration_offset, skip_weekly_filter
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

    min_dte = container.config.thresholds.min_dte

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
            earnings_date, timing, expiration_offset, min_dte=min_dte
        )

        # Apply filters (market cap + liquidity) for whisper mode
        filter_result, filter_reason, _, _ = should_filter_ticker(
            ticker, expiration_date, container,
            check_market_cap=True,
            check_liquidity=True
        )

        if filter_result:
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
            auto_backfill=True,
            skip_weekly_filter=skip_weekly_filter,
            earnings_timing=timing,
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
    logger.info(f"\n\U0001f50a Most Anticipated Earnings Analysis:")
    logger.info(f"   Mode: Earnings Whispers")
    logger.info(f"   Week: {monday.strftime('%Y-%m-%d')} to {week_end.strftime('%Y-%m-%d')}")
    logger.info(f"   Total Tickers: {len(tickers)}")
    logger.info(f"\n\U0001f4ca Analysis Results:")
    logger.info(f"   \U0001f50d Filtered (Market Cap Only): {filtered_count}")
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
        logger.info(f"\n\U0001f3af Most Anticipated + High VRP (Sorted by Earnings Date, Quality Score):")

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

        logger.info(f"\n\U0001f4a1 Run './trade.sh TICKER YYYY-MM-DD' for detailed strategy recommendations")
    else:
        logger.info(f"\n" + "=" * 80)
        logger.info("⏭️  RESULT: NO TRADEABLE OPPORTUNITIES")
        logger.info("=" * 80)
        logger.info(f"\n❌ No opportunities found among most anticipated earnings")
        if skip_count > 0:
            logger.info(f"   Note: {skip_count} ticker(s) had no upcoming earnings or insufficient data")
        logger.info(f"\n\U0001f4dd Recommendation:")
        logger.info(f"   High market attention doesn't always mean high VRP")
        logger.info(f"   Try: ./trade.sh scan YYYY-MM-DD for broader earnings scan")

    # Return 0 if we successfully completed the scan (even if some tickers had errors)
    # Only return 1 for fatal errors (calendar fetch failure, etc.)
    return 0
