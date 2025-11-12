"""
Implied Move Calculator - Tier 1 Core Metric

Calculates implied price movement from ATM straddle pricing.
This is the market's expectation of price movement.
"""

import logging
from datetime import date
from src.domain.types import Money, Percentage, Strike, ImpliedMove, OptionChain
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode
from src.domain.protocols import OptionsDataProvider

logger = logging.getLogger(__name__)


class ImpliedMoveCalculator:
    """
    Calculate implied move from ATM straddle.

    Formula:
        implied_move = straddle_cost / stock_price
        upper_bound = stock_price + straddle_cost
        lower_bound = stock_price - straddle_cost

    The implied move represents a ~68% probability range
    (1 standard deviation) for price movement.
    """

    def __init__(self, provider: OptionsDataProvider):
        self.provider = provider

    def calculate(
        self, ticker: str, expiration: date
    ) -> Result[ImpliedMove, AppError]:
        """
        Calculate implied move from option chain.

        Args:
            ticker: Stock symbol
            expiration: Option expiration date

        Returns:
            Result with ImpliedMove or AppError
        """
        logger.info(f"Calculating implied move: {ticker} exp {expiration}")

        # Validate expiration is not in the past
        today = date.today()
        if expiration < today:
            return Err(
                AppError(
                    ErrorCode.INVALID,
                    f"Expiration {expiration} is in the past (today: {today})",
                )
            )

        # Get option chain
        chain_result = self.provider.get_option_chain(ticker, expiration)
        if chain_result.is_err:
            return Err(chain_result.error)

        chain = chain_result.value

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
        implied_move_pct = Percentage(
            float(straddle_cost.amount / stock_price.amount * 100)
        )

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
