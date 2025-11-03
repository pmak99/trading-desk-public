"""
Option selector for finding ATM (at-the-money) options.

Pure selection logic with no external dependencies.
"""

import logging
from typing import Optional, Tuple, List

logger = logging.getLogger(__name__)


class OptionSelector:
    """Selects ATM options from options chain."""

    @staticmethod
    def find_atm_options(
        options: List[dict],
        current_price: float
    ) -> Tuple[Optional[dict], Optional[dict]]:
        """
        Find ATM (at-the-money) call and put options.

        Selects the options with strikes closest to the current stock price.

        Args:
            options: List of option contracts, each dict should have:
                - 'strike': Strike price
                - 'option_type': 'call' or 'put'
            current_price: Current stock price

        Returns:
            Tuple of (atm_call, atm_put)
            - atm_call: ATM call option dict or None
            - atm_put: ATM put option dict or None

        Example:
            >>> options = [
            ...     {'strike': 100, 'option_type': 'call', 'bid': 5.0},
            ...     {'strike': 100, 'option_type': 'put', 'bid': 4.8},
            ...     {'strike': 105, 'option_type': 'call', 'bid': 3.2},
            ... ]
            >>> call, put = OptionSelector.find_atm_options(options, 102.5)
            >>> call['strike']
            100
            >>> put['strike']
            100
        """
        atm_call = None
        atm_put = None
        min_distance = float('inf')

        for opt in options:
            strike = opt.get('strike', 0)
            distance = abs(strike - current_price)

            # Track minimum distance for logging
            if distance < min_distance:
                min_distance = distance

            # Find closest call and put separately
            if opt.get('option_type') == 'call':
                if not atm_call or distance < abs(atm_call.get('strike', 0) - current_price):
                    atm_call = opt
            elif opt.get('option_type') == 'put':
                if not atm_put or distance < abs(atm_put.get('strike', 0) - current_price):
                    atm_put = opt

        if atm_call and atm_put:
            logger.debug(
                f"Found ATM options: Call strike={atm_call.get('strike')}, "
                f"Put strike={atm_put.get('strike')}, "
                f"Price={current_price}, Distance={min_distance}"
            )

        return atm_call, atm_put

    @staticmethod
    def find_strike_by_delta(
        options: List[dict],
        target_delta: float,
        option_type: str = 'call'
    ) -> Optional[dict]:
        """
        Find option closest to target delta.

        Args:
            options: List of option contracts with 'greeks' data
            target_delta: Target delta value (e.g., 0.30 for 30 delta)
            option_type: 'call' or 'put'

        Returns:
            Option dict closest to target delta, or None if not found
        """
        best_option = None
        min_delta_diff = float('inf')

        for opt in options:
            if opt.get('option_type') != option_type:
                continue

            greeks = opt.get('greeks', {})
            delta = abs(greeks.get('delta', 0))

            delta_diff = abs(delta - abs(target_delta))

            if delta_diff < min_delta_diff:
                min_delta_diff = delta_diff
                best_option = opt

        return best_option
