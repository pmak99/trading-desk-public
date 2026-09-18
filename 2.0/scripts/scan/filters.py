"""
Ticker filtering logic - market cap, earnings date, liquidity checks.

Determines whether tickers should be filtered out of scan results.
"""

import logging
from datetime import date
from typing import Dict, Optional, Tuple

from src.container import Container

from .market_data import (
    check_liquidity_with_tier,
    get_market_cap_millions,
)

logger = logging.getLogger(__name__)


def should_filter_ticker(
    ticker: str,
    expiration: date,
    container: Container,
    check_market_cap: bool = True,
    check_liquidity: bool = True,
) -> Tuple[bool, Optional[str], Optional[str], Optional[Dict]]:
    """
    Determine if ticker should be filtered out based on market cap (LIQUIDITY NO LONGER FILTERS).

    IMPORTANT: Liquidity tier is checked and returned for display purposes, but does NOT
    cause filtering. All tradeable opportunities are shown regardless of liquidity tier,
    with appropriate warnings in the output.

    This is the mid-chain check. The tier every scan row displays and scores is
    the hybrid C-then-B check in ticker_analysis.analyze_ticker, which needs the
    implied move and sizes its open-interest bar from the sizing config. A
    hybrid branch here was never called and was removed 2026-09-14.

    Args:
        ticker: Stock ticker symbol
        expiration: Options expiration date
        container: DI container
        check_market_cap: Whether to check market cap threshold
        check_liquidity: Whether to check liquidity tier (for display only, doesn't filter)

    Returns:
        (should_filter, reason, liquidity_tier, hybrid_details)
        - should_filter: True if should skip ticker
        - reason: Why filtered
        - liquidity_tier: "EXCELLENT", "WARNING", or "REJECT"
        - hybrid_details: always None (kept for the callers' 4-tuple unpacking)
    """
    liquidity_tier = None
    hybrid_details = None

    # Check market cap (still filters)
    if check_market_cap:
        market_cap_millions = get_market_cap_millions(ticker)
        if market_cap_millions is not None:
            min_market_cap = container.config.thresholds.min_market_cap_millions
            if market_cap_millions < min_market_cap:
                return (True, f"Market cap ${market_cap_millions:.0f}M < ${min_market_cap:.0f}M", None, None)

    # Check liquidity tier (for display only - does NOT filter anymore)
    if check_liquidity:
        has_liquidity, liquidity_tier = check_liquidity_with_tier(ticker, expiration, container)
        # NOTE: We no longer filter based on liquidity tier
        # All opportunities are shown with their tier displayed as a warning

    return (False, None, liquidity_tier, hybrid_details)


def filter_ticker_concurrent(
    ticker: str,
    expiration_date: date,
    container: Container
) -> Tuple[bool, Optional[str]]:
    """
    Filter function compatible with ConcurrentScanner.

    Args:
        ticker: Stock ticker symbol
        expiration_date: Options expiration date
        container: DI container (passed via closure)

    Returns:
        (should_filter, reason) tuple
    """
    should_filter, reason, _, _ = should_filter_ticker(
        ticker, expiration_date, container,
        check_market_cap=True,
        check_liquidity=True
    )
    return (should_filter, reason)
