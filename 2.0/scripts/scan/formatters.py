"""
Result display and formatting - tables, colors, summaries.

Provides display formatting for liquidity tiers and scan result tables.
"""

import logging
from datetime import date
from typing import Dict, List, Optional, Tuple

from .constants import (
    LIQUIDITY_PRIORITY_ORDER,
    MARKET_CLOSED_INDICATOR,
)

logger = logging.getLogger(__name__)


def parse_liquidity_tier(tier_display: str) -> Tuple[str, bool]:
    """
    Parse liquidity tier display string into base tier and market status.

    Args:
        tier_display: Tier string like "EXCELLENT", "WARNING*", "REJECT*"

    Returns:
        Tuple of (base_tier, is_oi_only)
        - base_tier: "EXCELLENT", "WARNING", or "REJECT"
        - is_oi_only: True if asterisk present (market closed, OI-only scoring)
    """
    is_oi_only = tier_display.endswith(MARKET_CLOSED_INDICATOR)
    base_tier = tier_display.rstrip(MARKET_CLOSED_INDICATOR)
    return (base_tier, is_oi_only)


def format_liquidity_display(tier_display: str) -> str:
    """
    Format liquidity tier for display with appropriate indicator.

    4-Tier System:
    - EXCELLENT: checkmark High (>=5x OI, <=8% spread)
    - GOOD: checkmark Good (2-5x OI, 8-12% spread)
    - WARNING: warning Low (1-2x OI, 12-15% spread)
    - REJECT: x REJECT (<1x OI, >15% spread)

    Args:
        tier_display: Tier string from check_liquidity_with_tier

    Returns:
        Formatted display string
    """
    base_tier, is_oi_only = parse_liquidity_tier(tier_display)
    suffix = "*" if is_oi_only else ""

    if base_tier == "EXCELLENT":
        return f"\u2713 High{suffix}"
    elif base_tier == "GOOD":
        return f"\u2713 Good{suffix}"
    elif base_tier == "WARNING":
        return f"\u26a0\ufe0f  Low{suffix}"
    else:
        return f"\u274c REJECT{suffix}"


def _display_scan_results(
    results: List[dict],
    success_count: int,
    error_count: int,
    skip_count: int,
    filtered_count: int,
    mode_name: str,
    scan_date: Optional[date] = None,
    total_events: int = 0,
    tickers: Optional[List[str]] = None,
    week_range: Optional[Tuple[date, date]] = None
) -> int:
    """
    Display scan results in a formatted table (shared by all modes).

    This is a helper function to avoid duplicating display logic across
    scanning_mode, ticker_mode, and whisper_mode.

    Args:
        results: List of analysis result dictionaries
        success_count: Number of successful analyses
        error_count: Number of errors
        skip_count: Number of skipped tickers
        filtered_count: Number of filtered tickers
        mode_name: Display name for the mode (e.g., "SCAN MODE")
        scan_date: Target date for scan mode
        total_events: Total earnings events found
        tickers: List of tickers for ticker mode
        week_range: (start, end) dates for whisper mode

    Returns:
        Exit code (0 for success)
    """
    # Import here to avoid circular dependency
    from .quality_scorer import calculate_scan_quality_score, _precalculate_quality_scores

    # Summary header
    logger.info("\n" + "=" * 80)
    logger.info(f"{mode_name} - SUMMARY")
    logger.info("=" * 80)

    # Mode-specific details
    if scan_date:
        logger.info(f"\n\U0001f4c5 Scan Details:")
        logger.info(f"   Mode: Earnings Date Scan")
        logger.info(f"   Date: {scan_date}")
        logger.info(f"   Total Earnings Found: {total_events}")
    elif week_range:
        logger.info(f"\n\U0001f50a Most Anticipated Earnings Analysis:")
        logger.info(f"   Mode: Earnings Whispers")
        logger.info(f"   Week: {week_range[0]} to {week_range[1]}")
    elif tickers:
        logger.info(f"\n\U0001f4cb Ticker List Analysis:")
        logger.info(f"   Mode: Multiple Ticker Analysis")
        logger.info(f"   Tickers Requested: {len(tickers)}")

    logger.info(f"\n\U0001f4ca Analysis Results:")
    logger.info(f"   \U0001f50d Filtered (Market Cap Only): {filtered_count}")
    logger.info(f"   \u2713 Successfully Analyzed: {success_count}")
    logger.info(f"   \u23ed\ufe0f  Skipped (No Data): {skip_count}")
    logger.info(f"   \u2717 Errors: {error_count}")

    # Tradeable opportunities
    tradeable = [r for r in results if r.get('is_tradeable', False)]
    if tradeable:
        # Pre-calculate quality scores once
        _precalculate_quality_scores(tradeable)

        logger.info(f"\n" + "=" * 80)
        logger.info(f"\u2705 RESULT: {len(tradeable)} TRADEABLE OPPORTUNITIES FOUND")
        logger.info("=" * 80)
        logger.info(f"\n\U0001f3af Sorted by Quality Score (Risk-Adjusted):")

        # Table header
        logger.info(f"   {'#':<3} {'Ticker':<8} {'Name':<20} {'Score':<7} {'VRP':<8} {'Implied':<9} {'Edge':<7} {'Recommendation':<15} {'Liquidity':<12}")
        logger.info(f"   {'-'*3} {'-'*8} {'-'*20} {'-'*7} {'-'*8} {'-'*9} {'-'*7} {'-'*15} {'-'*12}")

        # Sort by quality score (strip asterisk for sorting)
        def sort_key(x):
            tier_raw = x.get('liquidity_tier', 'UNKNOWN')
            base_tier, _ = parse_liquidity_tier(tier_raw)
            return (-x['_quality_score'], LIQUIDITY_PRIORITY_ORDER.get(base_tier, 3))

        # Check if any result has OI-only indicator (market closed)
        has_oi_only = any(r.get('liquidity_tier', '').endswith('*') for r in tradeable)

        for i, r in enumerate(sorted(tradeable, key=sort_key), 1):
            ticker = r['ticker']
            full_name = r.get('ticker_name', '') or ''
            name = full_name[:20] if len(full_name) <= 20 else full_name[:full_name[:20].rfind(' ') or 20]

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
        logger.info(f"\n\u274c No opportunities found")
        if skip_count > 0:
            logger.info(f"   Note: {skip_count} ticker(s) skipped due to missing historical data")

    return 0
