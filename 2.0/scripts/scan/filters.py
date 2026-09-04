"""
Ticker filtering logic - market cap, earnings date, liquidity checks.

Determines whether tickers should be filtered out of scan results.
"""

import logging
from datetime import date
from typing import Dict, Optional, Tuple

from src.container import Container

from .market_data import (
    get_market_cap_millions,
    check_liquidity_with_tier,
    check_liquidity_hybrid,
)

logger = logging.getLogger(__name__)


def should_filter_ticker(
    ticker: str,
    expiration: date,
    container: Container,
    check_market_cap: bool = True,
    check_liquidity: bool = True,
    implied_move_pct: Optional[float] = None,
    use_hybrid_liquidity: bool = False,
    max_loss_budget: float = 20000.0,
) -> Tuple[bool, Optional[str], Optional[str], Optional[Dict]]:
    """
    Determine if ticker should be filtered out based on market cap (LIQUIDITY NO LONGER FILTERS).

    IMPORTANT: Liquidity tier is checked and returned for display purposes, but does NOT
    cause filtering. All tradeable opportunities are shown regardless of liquidity tier,
    with appropriate warnings in the output.

    When use_hybrid_liquidity=True and implied_move_pct is provided, uses the new
    C-then-B hybrid liquidity check which evaluates strikes at actual trading levels
    (outside implied move) with dynamic thresholds based on position size.

    Args:
        ticker: Stock ticker symbol
        expiration: Options expiration date
        container: DI container
        check_market_cap: Whether to check market cap threshold
        check_liquidity: Whether to check liquidity tier (for display only, doesn't filter)
        implied_move_pct: Implied move percentage (required for hybrid check)
        use_hybrid_liquidity: Use C-then-B hybrid check instead of mid-chain
        max_loss_budget: Maximum loss budget for dynamic thresholds (default $20k)

    Returns:
        (should_filter, reason, liquidity_tier, hybrid_details)
        - should_filter: True if should skip ticker
        - reason: Why filtered
        - liquidity_tier: "EXCELLENT", "WARNING", or "REJECT"
        - hybrid_details: Dict with method, strikes, OI if hybrid check used
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
        if use_hybrid_liquidity and implied_move_pct is not None:
            # Use new hybrid C-then-B approach with dynamic thresholds
            has_liquidity, liquidity_tier, hybrid_details = check_liquidity_hybrid(
                ticker=ticker,
                expiration=expiration,
                implied_move_pct=implied_move_pct,
                container=container,
                max_loss_budget=max_loss_budget,
                use_dynamic_thresholds=True,
            )
        else:
            # Fall back to old mid-chain approach
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
