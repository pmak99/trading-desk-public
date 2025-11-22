"""
Interpolated Implied Move Calculator - Phase 4 Algorithmic Optimization

Enhanced implied move calculation with linear interpolation between strikes.
Avoids rounding errors when no exact ATM strike exists.
"""

import bisect
import logging
from datetime import date
from typing import Optional, Tuple

from src.domain.types import Money, Percentage, Strike, ImpliedMove, OptionChain, OptionQuote
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode
from src.domain.protocols import OptionsDataProvider

logger = logging.getLogger(__name__)


class ImpliedMoveCalculatorInterpolated:
    """
    Enhanced implied move calculator with strike interpolation.

    Standard calculator uses closest ATM strike, which can have rounding error.
    For example, if stock is at $150.50 and strikes are $145, $150, $155:
    - Standard: Uses $150 or $155 (depends on rounding)
    - Interpolated: Uses weighted average of $150 and $155

    Benefits:
    - Smoother calculations (no discontinuities)
    - More accurate for stocks between strikes
    - Better for high-priced stocks with wide strike spacing
    - Reduces noise in historical comparisons
    """

    # Configuration
    # Tolerance for floating point strike matching (in dollars)
    # If stock price is within $0.01 of a strike, treat as exact match
    STRIKE_MATCH_TOLERANCE = 0.01

    def __init__(self, provider: OptionsDataProvider):
        self.provider = provider

    def calculate(
        self,
        ticker: str,
        expiration: date
    ) -> Result[ImpliedMove, AppError]:
        """
        Calculate implied move using interpolated straddle.

        Args:
            ticker: Stock symbol
            expiration: Option expiration date

        Returns:
            Result with ImpliedMove or AppError
        """
        logger.info(f"Calculating interpolated implied move: {ticker} exp {expiration}")

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
        stock_price = float(chain.stock_price.amount)

        # Find bracketing strikes
        bracket_result = self._find_bracketing_strikes(chain, stock_price)
        if bracket_result is None:
            # Fall back to exact ATM if stock is exactly at a strike
            return self._calculate_exact_atm(chain, ticker, expiration)

        lower_strike, upper_strike, weight = bracket_result

        # Interpolate straddle cost
        try:
            straddle_cost = self._interpolate_straddle(
                chain,
                lower_strike,
                upper_strike,
                weight
            )
        except ValueError as e:
            return Err(AppError(ErrorCode.NODATA, str(e)))

        # Calculate implied move
        implied_move_pct = Percentage((straddle_cost / stock_price) * 100)

        # Calculate bounds
        upper_bound = Money(stock_price + straddle_cost)
        lower_bound = Money(stock_price - straddle_cost)

        # Interpolate IVs
        avg_iv = self._interpolate_iv(
            chain,
            lower_strike,
            upper_strike,
            weight
        )

        # Use lower strike as reference for display
        # Note: call_iv and put_iv are None for interpolated results since we're
        # blending multiple strikes. The avg_iv field contains the interpolated IV.
        # Downstream consumers should check for None and use avg_iv instead.
        result = ImpliedMove(
            ticker=ticker,
            expiration=expiration,
            stock_price=chain.stock_price,
            atm_strike=lower_strike,  # Reference strike
            straddle_cost=Money(straddle_cost),
            implied_move_pct=implied_move_pct,
            upper_bound=upper_bound,
            lower_bound=lower_bound,
            call_iv=None,  # Not applicable - using interpolated avg_iv instead
            put_iv=None,   # Not applicable - using interpolated avg_iv instead
            avg_iv=avg_iv,
        )

        logger.info(
            f"{ticker}: Interpolated implied move {implied_move_pct.value:.2f}% "
            f"(${straddle_cost:.2f} straddle, "
            f"strikes ${lower_strike.price:.2f}-${upper_strike.price:.2f})"
        )

        return Ok(result)

    def _find_bracketing_strikes(
        self,
        chain: OptionChain,
        stock_price: float
    ) -> Optional[Tuple[Strike, Strike, float]]:
        """
        Find the two strikes that bracket the stock price.

        Returns:
            (lower_strike, upper_strike, weight) where weight is the
            interpolation weight (0 = all lower, 1 = all upper).
            None if stock is exactly at a strike or no brackets found.
        """
        strikes_sorted = sorted(chain.strikes, key=lambda s: float(s.price))
        prices = [float(s.price) for s in strikes_sorted]

        # Find where stock price would be inserted
        idx = bisect.bisect_left(prices, stock_price)

        # Check if stock is exactly at a strike
        if idx < len(prices) and abs(prices[idx] - stock_price) < self.STRIKE_MATCH_TOLERANCE:
            return None  # Exact match, use standard calculation

        if idx > 0 and abs(prices[idx - 1] - stock_price) < self.STRIKE_MATCH_TOLERANCE:
            return None  # Exact match at previous strike

        # Check if we have brackets
        if idx == 0 or idx >= len(strikes_sorted):
            return None  # Stock outside strike range

        lower_strike = strikes_sorted[idx - 1]
        upper_strike = strikes_sorted[idx]

        # Calculate interpolation weight
        lower_price = float(lower_strike.price)
        upper_price = float(upper_strike.price)
        weight = (stock_price - lower_price) / (upper_price - lower_price)

        return (lower_strike, upper_strike, weight)

    def _interpolate_straddle(
        self,
        chain: OptionChain,
        lower_strike: Strike,
        upper_strike: Strike,
        weight: float
    ) -> float:
        """
        Interpolate straddle cost between two strikes.

        Args:
            chain: Option chain
            lower_strike: Strike below stock price
            upper_strike: Strike above stock price
            weight: Interpolation weight (0-1)

        Returns:
            Interpolated straddle cost

        Raises:
            ValueError: If options are missing or illiquid
        """
        # Get options at lower strike
        lower_call = chain.calls.get(lower_strike)
        lower_put = chain.puts.get(lower_strike)

        if not lower_call or not lower_put:
            raise ValueError(f"Missing options at lower strike {lower_strike}")

        if not lower_call.is_liquid or not lower_put.is_liquid:
            raise ValueError(f"Illiquid options at lower strike {lower_strike}")

        # Get options at upper strike
        upper_call = chain.calls.get(upper_strike)
        upper_put = chain.puts.get(upper_strike)

        if not upper_call or not upper_put:
            raise ValueError(f"Missing options at upper strike {upper_strike}")

        if not upper_call.is_liquid or not upper_put.is_liquid:
            raise ValueError(f"Illiquid options at upper strike {upper_strike}")

        # Calculate straddles at each strike
        lower_straddle = float(lower_call.mid.amount + lower_put.mid.amount)
        upper_straddle = float(upper_call.mid.amount + upper_put.mid.amount)

        # Linear interpolation
        interpolated = lower_straddle * (1 - weight) + upper_straddle * weight

        return interpolated

    def _interpolate_iv(
        self,
        chain: OptionChain,
        lower_strike: Strike,
        upper_strike: Strike,
        weight: float
    ) -> Optional[Percentage]:
        """
        Interpolate average IV between two strikes.

        Args:
            chain: Option chain
            lower_strike: Strike below stock price
            upper_strike: Strike above stock price
            weight: Interpolation weight (0-1)

        Returns:
            Interpolated average IV, or None if IVs not available
        """
        # Get options
        lower_call = chain.calls.get(lower_strike)
        lower_put = chain.puts.get(lower_strike)
        upper_call = chain.calls.get(upper_strike)
        upper_put = chain.puts.get(upper_strike)

        if not all([lower_call, lower_put, upper_call, upper_put]):
            return None

        # Check if all have IVs
        if not all([
            lower_call.implied_volatility,
            lower_put.implied_volatility,
            upper_call.implied_volatility,
            upper_put.implied_volatility
        ]):
            return None

        # Calculate average IVs at each strike
        lower_avg = (
            lower_call.implied_volatility.value +
            lower_put.implied_volatility.value
        ) / 2.0

        upper_avg = (
            upper_call.implied_volatility.value +
            upper_put.implied_volatility.value
        ) / 2.0

        # Interpolate
        interpolated = lower_avg * (1 - weight) + upper_avg * weight

        return Percentage(interpolated)

    def _calculate_exact_atm(
        self,
        chain: OptionChain,
        ticker: str,
        expiration: date
    ) -> Result[ImpliedMove, AppError]:
        """
        Fall back to exact ATM calculation when stock is at a strike.

        Uses shared calculation logic from implied_move_common.

        Args:
            chain: Option chain
            ticker: Stock ticker
            expiration: Expiration date

        Returns:
            Result with ImpliedMove
        """
        logger.debug(f"{ticker}: Using exact ATM (stock at strike)")

        # Use shared calculation logic (no circular import since it's in common module)
        from src.application.metrics.implied_move_common import calculate_from_atm_chain
        return calculate_from_atm_chain(chain, ticker, expiration, validate_straddle_cost=False)
