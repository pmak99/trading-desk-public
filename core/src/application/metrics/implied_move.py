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
from src.application.metrics.implied_move_common import calculate_from_atm_chain

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

        # Use shared calculation logic
        return calculate_from_atm_chain(chain, ticker, expiration, validate_straddle_cost=True)
