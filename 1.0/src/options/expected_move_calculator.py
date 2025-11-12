"""
Expected move calculator for earnings options trading.

Calculates the market's expected move based on ATM straddle pricing.
This is a pure calculation module with no external dependencies.
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ExpectedMoveCalculator:
    """Calculates expected move from options pricing."""

    @staticmethod
    def calculate_from_straddle(
        call_bid: float,
        call_ask: float,
        put_bid: float,
        put_ask: float,
        current_price: float
    ) -> float:
        """
        Calculate expected move % from ATM straddle price.

        Formula: Expected Move = (Call Mid + Put Mid) / Stock Price * 100

        Args:
            call_bid: ATM call bid price
            call_ask: ATM call ask price
            put_bid: ATM put bid price
            put_ask: ATM put ask price
            current_price: Current stock price

        Returns:
            Expected move as percentage (e.g., 8.5 means 8.5%)
        """
        try:
            # Calculate mid prices
            call_mid = (call_bid + call_ask) / 2 if call_ask > 0 else call_bid
            put_mid = (put_bid + put_ask) / 2 if put_ask > 0 else put_bid

            # Straddle price = call + put
            straddle_price = call_mid + put_mid

            # Expected move as percentage
            expected_move_pct = (straddle_price / current_price) * 100

            return round(expected_move_pct, 2)

        except (ZeroDivisionError, TypeError) as e:
            logger.error(f"Failed to calculate expected move: {e}")
            return 0.0

    @staticmethod
    def calculate_from_options(
        atm_call: Optional[dict],
        atm_put: Optional[dict],
        current_price: float
    ) -> float:
        """
        Calculate expected move from ATM options dictionaries.

        Args:
            atm_call: ATM call option dict with 'bid' and 'ask' keys
            atm_put: ATM put option dict with 'bid' and 'ask' keys
            current_price: Current stock price

        Returns:
            Expected move as percentage
        """
        if not atm_call or not atm_put:
            return 0.0

        call_bid = atm_call.get('bid', 0) or 0
        call_ask = atm_call.get('ask', 0) or 0
        put_bid = atm_put.get('bid', 0) or 0
        put_ask = atm_put.get('ask', 0) or 0

        return ExpectedMoveCalculator.calculate_from_straddle(
            call_bid, call_ask, put_bid, put_ask, current_price
        )
