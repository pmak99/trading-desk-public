"""
Common utilities for implied move calculations.

Shared logic to avoid duplication between standard and interpolated calculators.
"""

import logging
from datetime import date
from src.domain.types import Money, Percentage, ImpliedMove, OptionChain
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode

logger = logging.getLogger(__name__)


def calculate_from_atm_chain(
    chain: OptionChain,
    ticker: str,
    expiration: date,
    validate_straddle_cost: bool = True
) -> Result[ImpliedMove, AppError]:
    """
    Calculate implied move from an option chain using ATM straddle.

    This is the core calculation logic shared by both standard and interpolated calculators.

    Args:
        chain: Option chain with calls and puts
        ticker: Stock symbol
        expiration: Expiration date
        validate_straddle_cost: If True, log warnings for unusual straddle costs

    Returns:
        Result with ImpliedMove or AppError
    """
    # Find ATM strike
    try:
        atm_strike = chain.atm_strike()
    except ValueError as e:
        return Err(AppError(ErrorCode.NODATA, str(e)))

    # Get straddle (call + put at ATM)
    if atm_strike not in chain.calls:
        return Err(
            AppError(ErrorCode.NODATA, f"ATM strike {atm_strike} not in calls")
        )

    if atm_strike not in chain.puts:
        return Err(
            AppError(ErrorCode.NODATA, f"ATM strike {atm_strike} not in puts")
        )

    call = chain.calls[atm_strike]
    put = chain.puts[atm_strike]

    # Check liquidity
    if not call.is_liquid or not put.is_liquid:
        return Err(
            AppError(
                ErrorCode.INVALID,
                f"Illiquid options at strike {atm_strike}",
            )
        )

    # Calculate straddle cost (call + put mid-prices)
    straddle_cost = call.mid + put.mid

    # Validate stock price
    stock_price = chain.stock_price
    if stock_price.amount <= 0:
        return Err(
            AppError(ErrorCode.INVALID, "Invalid stock price <= 0")
        )

    # Calculate implied move percentage
    straddle_pct = float(straddle_cost.amount / stock_price.amount * 100)

    # Validate straddle cost is reasonable (typically 1-20% for earnings trades)
    if validate_straddle_cost:
        if straddle_pct < 0.5:
            logger.warning(
                f"{ticker}: Straddle cost {straddle_pct:.2f}% of stock price seems too low - validate data"
            )
        elif straddle_pct > 30.0:
            logger.warning(
                f"{ticker}: Straddle cost {straddle_pct:.2f}% of stock price seems too high - validate data"
            )

    implied_move_pct = Percentage(straddle_pct)

    # Calculate bounds
    upper_bound = Money(stock_price.amount + straddle_cost.amount)
    lower_bound = Money(stock_price.amount - straddle_cost.amount)

    # Validate bounds
    if lower_bound.amount <= 0:
        logger.warning(
            f"{ticker}: Lower bound negative (${lower_bound.amount:.2f})"
        )
        # This can happen for very volatile stocks
        # Not necessarily an error, but worth noting

    # Extract IVs if available
    call_iv = call.implied_volatility
    put_iv = put.implied_volatility
    avg_iv = None
    if call_iv and put_iv:
        avg_iv = Percentage((call_iv.value + put_iv.value) / 2)

    result = ImpliedMove(
        ticker=ticker,
        expiration=expiration,
        stock_price=stock_price,
        atm_strike=atm_strike,
        straddle_cost=straddle_cost,
        implied_move_pct=implied_move_pct,
        upper_bound=upper_bound,
        lower_bound=lower_bound,
        call_iv=call_iv,
        put_iv=put_iv,
        avg_iv=avg_iv,
    )

    logger.info(
        f"{ticker}: Implied move {implied_move_pct.value:.2f}% "
        f"(${straddle_cost.amount:.2f} straddle)"
    )

    return Ok(result)
