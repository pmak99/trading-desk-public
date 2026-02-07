"""
Sequential and parallel scan orchestration - scanning_mode, ticker_mode, whisper_mode.

Contains the main workflow functions that coordinate analysis across multiple tickers.
"""

import functools
import logging
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from tqdm import tqdm

from src.container import Container
from src.domain.enums import EarningsTiming
from src.infrastructure.data_sources.earnings_whisper_scraper import (
    EarningsWhisperScraper,
    get_week_monday
)
from src.application.filters.weekly_options import has_weekly_options

from .constants import (
    BACKFILL_TIMEOUT_SECONDS,
    BACKFILL_YEARS,
    LIQUIDITY_PRIORITY_ORDER,
)
from .date_utils import (
    calculate_expiration_date,
    calculate_implied_move_expiration,
    validate_expiration_date,
)
from .market_data import (
    get_ticker_name,
    check_liquidity_hybrid,
)
from .filters import (
    should_filter_ticker,
    filter_ticker_concurrent,
)
from .quality_scorer import (
    _precalculate_quality_scores,
)
from .formatters import (
    parse_liquidity_tier,
    format_liquidity_display,
    _display_scan_results,
)
from .earnings_fetcher import (
    fetch_earnings_for_date,
    fetch_earnings_for_ticker,
    validate_tradeable_earnings_dates,
    ensure_tickers_in_db,
)

logger = logging.getLogger(__name__)


def analyze_ticker(
    container: Container,
    ticker: str,
    earnings_date: date,
    expiration_date: date,
    auto_backfill: bool = False,
    skip_weekly_filter: bool = False
) -> Optional[dict]:
    """
    Analyze a single ticker for IV Crush opportunity.

    Args:
        container: Dependency injection container
        ticker: Stock ticker symbol
        earnings_date: Date of earnings announcement
        expiration_date: Options expiration date
        auto_backfill: If True, automatically backfill missing historical data
        skip_weekly_filter: If True, skip weekly options filter (override REQUIRE_WEEKLY_OPTIONS)

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

        # Check for weekly options (opt-in filter via REQUIRE_WEEKLY_OPTIONS)
        has_weeklies = True  # Default: permissive
        weekly_reason = ""
        if container.config.thresholds.require_weekly_options and not skip_weekly_filter:
            # Fetch expirations to check for weekly options
            expirations_result = container.tradier.get_expirations(ticker)
            if expirations_result.is_ok:
                expirations_list = [exp.isoformat() for exp in expirations_result.value]
                has_weeklies, weekly_reason = has_weekly_options(
                    expirations_list,
                    earnings_date.isoformat()
                )
                if not has_weeklies:
                    logger.info(f"\u2717 {ticker}: No weekly options - {weekly_reason}")
                    return None
            else:
                # On API error, be permissive - don't block trading opportunities
                logger.debug(f"{ticker}: Could not check weekly options, skipping filter")

        # Validate expiration date
        validation_error = validate_expiration_date(expiration_date, earnings_date, ticker)
        if validation_error:
            logger.error(f"\u2717 Invalid expiration date: {validation_error}")
            return None

        # Calculate the implied move expiration (first post-earnings day)
        # This is different from trading expiration to capture pure IV crush
        implied_move_exp = calculate_implied_move_expiration(earnings_date)

        # Find nearest available expiration for implied move calculation
        nearest_im_exp_result = container.tradier.find_nearest_expiration(ticker, implied_move_exp)
        if nearest_im_exp_result.is_err:
            logger.warning(f"\u2717 Failed to find implied move expiration for {ticker}: {nearest_im_exp_result.error}")
            return None

        actual_im_expiration = nearest_im_exp_result.value
        if actual_im_expiration != implied_move_exp:
            logger.info(f"  Implied move expiration: {implied_move_exp} \u2192 {actual_im_expiration}")

        # Find nearest available expiration for trading/liquidity
        nearest_exp_result = container.tradier.find_nearest_expiration(ticker, expiration_date)
        if nearest_exp_result.is_err:
            logger.warning(f"\u2717 Failed to find trading expiration for {ticker}: {nearest_exp_result.error}")
            return None

        actual_expiration = nearest_exp_result.value
        if actual_expiration != expiration_date:
            logger.info(f"  Trading expiration: {expiration_date} \u2192 {actual_expiration}")
            expiration_date = actual_expiration

        # Get calculators
        implied_move_calc = container.implied_move_calculator
        vrp_calc = container.vrp_calculator
        prices_repo = container.prices_repository

        # Step 1: Calculate implied move using first post-earnings expiration
        logger.info("\n\U0001f4ca Calculating Implied Move...")
        implied_result = implied_move_calc.calculate(ticker, actual_im_expiration)

        if implied_result.is_err:
            logger.warning(f"\u2717 Failed to calculate implied move: {implied_result.error}")
            return None

        implied_move = implied_result.value
        logger.info(f"\u2713 Implied Move: {implied_move.implied_move_pct}")
        logger.info(f"  Stock Price: {implied_move.stock_price}")
        logger.info(f"  ATM Strike: {implied_move.atm_strike}")
        logger.info(f"  Straddle Cost: {implied_move.straddle_cost}")

        # Step 2: Get historical moves
        logger.info("\n\U0001f4ca Fetching Historical Moves...")
        hist_result = prices_repo.get_historical_moves(ticker, limit=12)

        if hist_result.is_err:
            logger.warning(f"\u2717 No historical data: {hist_result.error}")

            # Auto-backfill if enabled (for ticker mode/list mode)
            if auto_backfill:
                logger.info(f"\U0001f4ca Auto-backfilling historical earnings data for {ticker}...")

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
                        cwd=Path(__file__).parent.parent.parent,
                        capture_output=True,
                        text=True,
                        timeout=BACKFILL_TIMEOUT_SECONDS
                    )

                    if result.returncode == 0:
                        logger.info(f"\u2713 Backfill complete for {ticker}")

                        # Retry fetching historical moves
                        logger.info("\U0001f4ca Retrying historical data fetch...")
                        hist_result = prices_repo.get_historical_moves(ticker, limit=12)

                        if hist_result.is_err:
                            logger.warning(f"\u2717 Still no historical data after backfill: {hist_result.error}")
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
                        logger.warning(f"\u2717 Backfill failed for {ticker}: {result.stderr}")
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
                    logger.warning(f"\u2717 Backfill timeout for {ticker}")
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
                    logger.warning(f"\u2717 Backfill error for {ticker}: {e}")
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
        logger.info(f"\u2713 Found {len(historical_moves)} historical moves")

        # Step 3: Calculate VRP
        logger.info("\n\U0001f4ca Calculating VRP...")
        vrp_result = vrp_calc.calculate(
            ticker=ticker,
            expiration=expiration_date,
            implied_move=implied_move,
            historical_moves=historical_moves,
        )

        if vrp_result.is_err:
            logger.warning(f"\u2717 Failed to calculate VRP: {vrp_result.error}")
            return None

        vrp = vrp_result.value

        logger.info(f"\u2713 VRP Ratio: {vrp.vrp_ratio:.2f}x")
        logger.info(f"  Implied Move: {vrp.implied_move_pct}")
        logger.info(f"  Historical Mean: {vrp.historical_mean_move_pct}")
        logger.info(f"  Edge Score: {vrp.edge_score:.2f}")
        logger.info(f"  Recommendation: {vrp.recommendation.value.upper()}")

        # CRITICAL: Check liquidity tier using HYBRID approach (C-then-B with dynamic thresholds)
        implied_move_pct = float(str(implied_move.implied_move_pct).rstrip('%'))
        has_liquidity, liquidity_tier, hybrid_details = check_liquidity_hybrid(
            ticker=ticker,
            expiration=expiration_date,
            implied_move_pct=implied_move_pct,
            container=container,
            max_loss_budget=20000.0,
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
            logger.info(f"    Position: {contracts} contracts \u00d7 ${spread_width} spread ({price_tier} tier)")
            # Show tier breakdown
            oi_icon = {'EXCELLENT': '\u2713', 'GOOD': '\u2713', 'WARNING': '\u26a0\ufe0f', 'REJECT': '\u274c'}.get(oi_tier, '?')
            spread_icon = {'EXCELLENT': '\u2713', 'GOOD': '\u2713', 'WARNING': '\u26a0\ufe0f', 'REJECT': '\u274c'}.get(spread_tier, '?')
            logger.info(f"    OI: {oi_ratio:.1f}x \u2192 {oi_tier} {oi_icon} | Spread: {max_spread:.0f}% \u2192 {spread_tier} {spread_icon}")
        else:
            logger.info(f"  Liquidity Tier: {liquidity_tier}")

        # 4-Tier Warning Messages
        tier_clean = liquidity_tier.replace('*', '')
        if tier_clean == "GOOD":
            logger.info(f"\n\u2713 GOOD liquidity for {ticker}")
            oi_tier = hybrid_details.get('oi_tier', 'N/A')
            spread_tier = hybrid_details.get('spread_tier', 'N/A')
            if oi_tier == "GOOD":
                oi_ratio = hybrid_details.get('oi_ratio', 0)
                logger.info(f"   OI/Position ratio {oi_ratio:.1f}x (2-5x) - adequate for full size")
            if spread_tier == "GOOD":
                max_spread = max(hybrid_details.get('call_spread_pct', 0), hybrid_details.get('put_spread_pct', 0))
                logger.info(f"   Bid/ask spread {max_spread:.0f}% (8-12%) - acceptable slippage")
        elif tier_clean == "WARNING":
            logger.warning(f"\n\u26a0\ufe0f  WARNING: Low liquidity detected for {ticker}")
            oi_tier = hybrid_details.get('oi_tier', 'N/A')
            spread_tier = hybrid_details.get('spread_tier', 'N/A')
            if oi_tier == "WARNING":
                oi_ratio = hybrid_details.get('oi_ratio', 0)
                logger.warning(f"   OI/Position ratio {oi_ratio:.1f}x (1-2x) - consider reducing size")
            if spread_tier == "WARNING":
                max_spread = max(hybrid_details.get('call_spread_pct', 0), hybrid_details.get('put_spread_pct', 0))
                logger.warning(f"   Bid/ask spread {max_spread:.0f}% (>12%) - expect slippage")
        elif tier_clean == "REJECT":
            logger.warning(f"\n\u274c CRITICAL: Very low liquidity for {ticker}")
            oi_tier = hybrid_details.get('oi_tier', 'N/A')
            spread_tier = hybrid_details.get('spread_tier', 'N/A')
            if oi_tier == "REJECT":
                oi_ratio = hybrid_details.get('oi_ratio', 0)
                logger.warning(f"   OI/Position ratio {oi_ratio:.1f}x (<1x) - DO NOT TRADE at full size")
            if spread_tier == "REJECT":
                max_spread = max(hybrid_details.get('call_spread_pct', 0), hybrid_details.get('put_spread_pct', 0))
                logger.warning(f"   Bid/ask spread {max_spread:.0f}% (>15%) - DO NOT TRADE")

        if vrp.is_tradeable:
            logger.info("\n\u2705 TRADEABLE OPPORTUNITY")
        else:
            logger.info("\n\u23ed\ufe0f  SKIP - Insufficient edge")

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
        logger.error(f"\u2717 Error analyzing {ticker}: {e}", exc_info=True)
        return None


def analyze_ticker_concurrent(
    container: Container,
    ticker: str,
    earnings_date: date,
    expiration_date: date,
    skip_weekly_filter: bool = False
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
        skip_weekly_filter: If True, skip weekly options filter

    Returns:
        Analysis result dict or None
    """
    return analyze_ticker(
        container=container,
        ticker=ticker,
        earnings_date=earnings_date,
        expiration_date=expiration_date,
        auto_backfill=False,  # Disable backfill in concurrent mode
        skip_weekly_filter=skip_weekly_filter
    )


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
            skip_weekly_filter=skip_weekly_filter
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


def ticker_mode_parallel(
    container: Container,
    tickers: List[str],
    expiration_offset: Optional[int] = None,
    skip_weekly_filter: bool = False
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
            logger.info(f"\u23ed\ufe0f  {ticker}: No upcoming earnings found")

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
        mode_name="TICKER MODE",
        tickers=tickers
    )


def ticker_mode(
    container: Container,
    tickers: List[str],
    expiration_offset: Optional[int] = None,
    parallel: bool = False,
    skip_weekly_filter: bool = False
) -> int:
    """
    Ticker mode: Analyze specific tickers from command line.

    Args:
        container: DI container
        tickers: List of ticker symbols
        expiration_offset: Custom expiration offset in days
        parallel: If True, use parallel processing (5x speedup)
        skip_weekly_filter: If True, skip weekly options filter

    Returns exit code (0 for success, 1 for error)
    """
    # Use parallel mode if requested and we have multiple tickers
    if parallel and len(tickers) > 1:
        return ticker_mode_parallel(container, tickers, expiration_offset, skip_weekly_filter)

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

        # Update progress
        pbar.set_postfix_str(f"{ticker}: Analyzing VRP")
        sys.stderr.flush()

        # Analyze ticker (with auto-backfill enabled for ticker mode)
        result = analyze_ticker(
            container,
            ticker,
            earnings_date,
            expiration_date,
            auto_backfill=True,
            skip_weekly_filter=skip_weekly_filter
        )

        if result:
            results.append(result)
            if result['status'] == 'SUCCESS':
                success_count += 1
                pbar.set_postfix_str(f"{ticker}: \u2713 Complete")
            else:
                skip_count += 1
                pbar.set_postfix_str(f"{ticker}: Skipped")
        else:
            error_count += 1
            pbar.set_postfix_str(f"{ticker}: \u2717 Error")
        sys.stderr.flush()

    pbar.close()

    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("LIST MODE - SUMMARY")
    logger.info("=" * 80)
    logger.info(f"\n\U0001f4cb Ticker List Analysis:")
    logger.info(f"   Mode: Multiple Ticker Analysis")
    logger.info(f"   Tickers Requested: {len(tickers)}")
    logger.info(f"   Tickers Analyzed: {', '.join(tickers)}")
    logger.info(f"\n\U0001f4ca Analysis Results:")
    logger.info(f"   \U0001f50d Filtered (Market Cap Only): {filtered_count}")
    logger.info(f"   \u2713 Successfully Analyzed: {success_count}")
    logger.info(f"   \u23ed\ufe0f  Skipped (No Earnings/Data): {skip_count}")
    logger.info(f"   \u2717 Errors: {error_count}")

    # Tradeable opportunities
    tradeable = [r for r in results if r.get('is_tradeable', False)]
    if tradeable:
        # Pre-calculate quality scores once (avoids ~82% duplicate calculations)
        _precalculate_quality_scores(tradeable)

        logger.info(f"\n" + "=" * 80)
        logger.info(f"\u2705 RESULT: {len(tradeable)} TRADEABLE OPPORTUNITIES FOUND")
        logger.info("=" * 80)
        logger.info(f"\n\U0001f3af Sorted by Earnings Date, Quality Score (Risk-Adjusted):")

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

        logger.info(f"\n\U0001f4a1 Run './trade.sh TICKER YYYY-MM-DD' for detailed strategy recommendations")
    else:
        logger.info(f"\n" + "=" * 80)
        logger.info("\u23ed\ufe0f  RESULT: NO TRADEABLE OPPORTUNITIES")
        logger.info("=" * 80)
        logger.info(f"\n\u274c No opportunities found among {len(tickers)} ticker(s)")
        if skip_count > 0:
            logger.info(f"   Note: {skip_count} ticker(s) had no upcoming earnings or insufficient data")
        logger.info(f"\n\U0001f4dd Recommendation:")
        logger.info(f"   Try different tickers or use whisper mode for most anticipated earnings")

    # Return 0 if we successfully completed the scan (even if some tickers had errors)
    # Only return 1 for fatal errors (calendar fetch failure, etc.)
    return 0


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

    # Build earnings lookup for each ticker
    earnings_lookup: Dict[str, Tuple[date, str]] = {}

    logger.info("Fetching earnings dates...")
    for ticker in tickers:
        earnings_info = fetch_earnings_for_ticker(container, ticker)
        if earnings_info:
            earnings_date, timing = earnings_info
            earnings_lookup[ticker] = (earnings_date, timing.value)
        else:
            logger.info(f"\u23ed\ufe0f  {ticker}: No upcoming earnings found")

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

    Fetches tickers from Reddit and analyzes each with auto-backfill.

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
        logger.warning("\u26a0\ufe0f  No tickers retrieved from Earnings Whispers")
        logger.info("   This may indicate:")
        logger.info("   - Reddit API rate limiting")
        logger.info("   - No anticipated earnings for this week")
        logger.info("   - Network connectivity issues")
        logger.info("")
        logger.info("\U0001f4dd Try:")
        logger.info("   - Use a different week: ./trade.sh whisper 2025-11-17")
        logger.info("   - Use scan mode: ./trade.sh scan 2025-11-20")
        return 1

    logger.info(f"\u2713 Retrieved {len(tickers)} most anticipated tickers")
    logger.info(f"Tickers: {', '.join(tickers)}")

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
            logger.info(f"\u23ed\ufe0f  {ticker}: Earnings {earnings_date} outside target week ({monday.date()} to {week_end.date()})")
            pbar.set_postfix_str(f"{ticker}: Outside week")
            sys.stderr.flush()
            continue

        # Calculate expiration date
        expiration_date = calculate_expiration_date(
            earnings_date, timing, expiration_offset
        )

        # Apply filters (market cap + liquidity) for whisper mode
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
            skip_weekly_filter=skip_weekly_filter
        )

        if result:
            results.append(result)
            if result['status'] == 'SUCCESS':
                success_count += 1
                pbar.set_postfix_str(f"{ticker}: \u2713 Complete")
            else:
                skip_count += 1
                pbar.set_postfix_str(f"{ticker}: Skipped")
        else:
            error_count += 1
            pbar.set_postfix_str(f"{ticker}: \u2717 Error")
        sys.stderr.flush()

    pbar.close()

    # Summary
    logger.info("\n" + "=" * 80)
    logger.info("WHISPER MODE - SUMMARY")
    logger.info("=" * 80)
    logger.info(f"\n\U0001f50a Most Anticipated Earnings Analysis:")
    logger.info(f"   Mode: Earnings Whispers (Reddit r/EarningsWhispers)")
    logger.info(f"   Week: {monday.strftime('%Y-%m-%d')} to {week_end.strftime('%Y-%m-%d')}")
    logger.info(f"   Total Tickers: {len(tickers)}")
    logger.info(f"\n\U0001f4ca Analysis Results:")
    logger.info(f"   \U0001f50d Filtered (Market Cap Only): {filtered_count}")
    logger.info(f"   \u2713 Successfully Analyzed: {success_count}")
    logger.info(f"   \u23ed\ufe0f  Skipped (No Earnings/Data): {skip_count}")
    logger.info(f"   \u2717 Errors: {error_count}")

    # Tradeable opportunities
    tradeable = [r for r in results if r.get('is_tradeable', False)]
    if tradeable:
        # Validate earnings dates for tradeable tickers only (optimization)
        validate_tradeable_earnings_dates(tradeable, container)

        # Pre-calculate quality scores once (avoids ~82% duplicate calculations)
        _precalculate_quality_scores(tradeable)

        logger.info(f"\n" + "=" * 80)
        logger.info(f"\u2705 RESULT: {len(tradeable)} TRADEABLE OPPORTUNITIES FOUND")
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
        logger.info("\u23ed\ufe0f  RESULT: NO TRADEABLE OPPORTUNITIES")
        logger.info("=" * 80)
        logger.info(f"\n\u274c No opportunities found among most anticipated earnings")
        if skip_count > 0:
            logger.info(f"   Note: {skip_count} ticker(s) had no upcoming earnings or insufficient data")
        logger.info(f"\n\U0001f4dd Recommendation:")
        logger.info(f"   High market attention doesn't always mean high VRP")
        logger.info(f"   Try: ./trade.sh scan YYYY-MM-DD for broader earnings scan")

    # Return 0 if we successfully completed the scan (even if some tickers had errors)
    # Only return 1 for fatal errors (calendar fetch failure, etc.)
    return 0
