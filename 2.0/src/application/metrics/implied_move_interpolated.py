"""
Interpolated Implied Move Calculator

Implied move for a stock that sits between two strikes, priced as the at-the-money straddle
at spot rather than the straddle at the nearest strike.

The two bracketing straddles are not averaged. Each carries intrinsic value |spot - strike|,
and a straddle is convex in strike, so their chord sits above the ATM straddle -- +20.8% at
$52.50 between $5 strikes at 80% IV and 3 DTE -- which inflated implied move, VRP and size.
Instead each bracket's volatility is read back out of its own straddle quote, interpolated to
spot by strike, and the ATM straddle is priced from it.
"""

import bisect
import logging
import math
from datetime import date
from typing import Optional, Tuple

from common.timezone import now_et
from src.domain.errors import AppError, Err, ErrorCode, Ok, Result
from src.domain.protocols import OptionsDataProvider
from src.domain.types import (
    ImpliedMove,
    Money,
    OptionChain,
    Percentage,
    Strike,
)

logger = logging.getLogger(__name__)

_SQRT2 = math.sqrt(2.0)

# Total volatility (sigma * sqrt(T)) beyond which a straddle is worth spot + strike to
# double precision. A quote that needs more is not a price.
_MAX_TOTAL_VOL = 64.0
_BISECTION_STEPS = 200

# The straddle-cost sanity bounds calculate_from_atm_chain applies (implied_move_common.py),
# as a percentage of spot. A test holds the two paths to the same warn/no-warn decision.
_STRADDLE_PCT_TOO_LOW = 0.5
_STRADDLE_PCT_TOO_HIGH = 30.0


def _straddle_value(spot: float, strike: float, total_vol: float) -> float:
    """Black-Scholes straddle at zero rates, as a function of total volatility v = sigma*sqrt(T).

    call + put = S*erf(d1/sqrt2) - K*erf(d2/sqrt2), with d1 = ln(S/K)/v + v/2 and d2 = d1 - v.
    At S = K it is 2S*erf(v / 2sqrt2), which is S*v*sqrt(2/pi) to first order.

    The price depends on sigma and T only through v, so neither the inversion below nor the
    re-pricing at spot needs a time-to-expiry convention: reading v out of a quote and pricing
    with the same v cancels whatever convention the quote was made on.
    """
    if total_vol <= 0.0:
        return abs(spot - strike)
    d1 = math.log(spot / strike) / total_vol + total_vol / 2.0
    d2 = d1 - total_vol
    return spot * math.erf(d1 / _SQRT2) - strike * math.erf(d2 / _SQRT2)


def _implied_total_vol(spot: float, strike: float, straddle: float) -> Optional[float]:
    """Total volatility at which the Black-Scholes straddle at ``strike`` is worth ``straddle``.

    None when the quote is outside the no-arbitrage range (intrinsic, spot + strike): at or
    below intrinsic there is no time value to read a volatility from, and a straddle cannot
    be worth more than spot + strike.
    """
    if spot <= 0.0 or strike <= 0.0:
        return None
    if not abs(spot - strike) < straddle < spot + strike:
        return None

    # The straddle is strictly increasing in v, so bracket the root and bisect.
    lo, hi = 0.0, 1.0
    while _straddle_value(spot, strike, hi) < straddle:
        lo, hi = hi, hi * 2.0
        if hi > _MAX_TOTAL_VOL:
            return None
    for _ in range(_BISECTION_STEPS):
        mid = 0.5 * (lo + hi)
        if mid <= lo or mid >= hi:
            break
        if _straddle_value(spot, strike, mid) < straddle:
            lo = mid
        else:
            hi = mid
    total_vol = 0.5 * (lo + hi)
    return total_vol if total_vol > 0.0 else None


class ImpliedMoveCalculatorInterpolated:
    """
    Implied move calculator that prices the ATM straddle at spot.

    Standard calculator uses the straddle at the closest strike. If the stock is at $152.50
    with $150 and $155 strikes, that straddle is struck $2.50 away from spot.

    This calculator:
    - Stock within STRIKE_MATCH_TOLERANCE of a strike: that strike's straddle, as the
      standard calculator does.
    - Otherwise: implied total volatility from each bracketing straddle quote, interpolated
      linearly in strike to spot, priced as the Black-Scholes ATM straddle. One unusable
      bracket (missing, illiquid, or quoted outside its no-arbitrage range) leaves the other
      bracket's volatility; two is NODATA.

    Both paths are priced from the option quotes, so the result is continuous as spot crosses
    a strike. The vendor IVs are reported as avg_iv but never priced: they may be absent, can
    lag the quotes, and are computed on a time convention this engine does not share.
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
        Calculate implied move from the ATM straddle at spot.

        Args:
            ticker: Stock symbol
            expiration: Option expiration date

        Returns:
            Result with ImpliedMove or AppError
        """
        logger.info(f"Calculating interpolated implied move: {ticker} exp {expiration}")

        # Validate expiration is not in the past
        today = now_et().date()
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
            # Stock at a strike (or outside the strike range): price that strike's straddle
            return self._calculate_exact_atm(chain, ticker, expiration)

        lower_strike, upper_strike, weight = bracket_result

        try:
            straddle_cost = self._interpolate_straddle(
                chain,
                lower_strike,
                upper_strike,
                weight
            )
        except ValueError as e:
            return Err(AppError(ErrorCode.NODATA, str(e)))

        if straddle_cost <= 0:
            return Err(
                AppError(
                    ErrorCode.INVALID,
                    f"Invalid straddle cost <= 0 between strikes {lower_strike} and {upper_strike}",
                )
            )

        straddle_pct = (straddle_cost / stock_price) * 100
        implied_move_pct = Percentage(straddle_pct)

        upper_bound = Money(stock_price + straddle_cost)
        lower_bound = Money(stock_price - straddle_cost)

        self._warn_if_implausible(ticker, straddle_pct, lower_bound)

        # Interpolate the vendor IVs for reporting
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
            f"(${straddle_cost:.2f} ATM straddle at spot, "
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
        Price the ATM straddle at spot from the bracketing straddle quotes.

        Each usable bracket's total volatility is implied from its straddle mid; the two are
        interpolated linearly by ``weight`` and the Black-Scholes straddle struck at spot is
        priced from the result. When only one bracket is usable (e.g. the upper strike has
        no bids at the open), its volatility is priced at spot alone.

        Args:
            chain: Option chain
            lower_strike: Strike below stock price
            upper_strike: Strike above stock price
            weight: Interpolation weight (0-1)

        Returns:
            ATM straddle cost at spot

        Raises:
            ValueError: If neither bracket yields a volatility
        """
        spot = float(chain.stock_price.amount)

        lower_vol, lower_reason = self._bracket_total_vol(chain, lower_strike, spot)
        upper_vol, upper_reason = self._bracket_total_vol(chain, upper_strike, spot)

        if lower_vol is not None and upper_vol is not None:
            total_vol = lower_vol * (1 - weight) + upper_vol * weight
        elif lower_vol is not None:
            logger.warning(
                f"Upper strike {upper_strike} unusable ({upper_reason}), "
                f"using lower bracket volatility only"
            )
            total_vol = lower_vol
        elif upper_vol is not None:
            logger.warning(
                f"Lower strike {lower_strike} unusable ({lower_reason}), "
                f"using upper bracket volatility only"
            )
            total_vol = upper_vol
        else:
            raise ValueError(
                f"No usable bracket straddle: lower strike {lower_strike}: {lower_reason}; "
                f"upper strike {upper_strike}: {upper_reason}"
            )

        return _straddle_value(spot, spot, total_vol)

    def _bracket_total_vol(
        self,
        chain: OptionChain,
        strike: Strike,
        spot: float
    ) -> Tuple[Optional[float], str]:
        """
        Total volatility implied by the straddle quote at one bracketing strike.

        Returns:
            (total_vol, "") when usable, else (None, reason)
        """
        call = chain.calls.get(strike)
        put = chain.puts.get(strike)

        if call is None or put is None:
            return None, "missing call or put"
        if not (call.is_liquid and put.is_liquid):
            return None, "illiquid options"

        mid = float(call.mid.amount + put.mid.amount)
        strike_price = float(strike.price)
        total_vol = _implied_total_vol(spot, strike_price, mid)
        if total_vol is None:
            return None, (
                f"straddle mid ${mid:.2f} outside "
                f"(intrinsic ${abs(spot - strike_price):.2f}, ${spot + strike_price:.2f})"
            )
        return total_vol, ""

    def _warn_if_implausible(self, ticker: str, straddle_pct: float, lower_bound: Money) -> None:
        """The straddle-cost checks calculate_from_atm_chain runs on the exact-strike path."""
        if straddle_pct < _STRADDLE_PCT_TOO_LOW:
            logger.warning(
                f"{ticker}: Straddle cost {straddle_pct:.2f}% of stock price seems too low - validate data"
            )
        elif straddle_pct > _STRADDLE_PCT_TOO_HIGH:
            logger.warning(
                f"{ticker}: Straddle cost {straddle_pct:.2f}% of stock price seems too high - validate data"
            )

        if lower_bound.amount <= 0:
            logger.warning(
                f"{ticker}: Lower bound negative (${lower_bound.amount:.2f})"
            )

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

        if lower_call is None or lower_put is None or upper_call is None or upper_put is None:
            return None

        # Check if all have IVs
        lower_call_iv, lower_put_iv = lower_call.implied_volatility, lower_put.implied_volatility
        upper_call_iv, upper_put_iv = upper_call.implied_volatility, upper_put.implied_volatility
        if lower_call_iv is None or lower_put_iv is None or upper_call_iv is None or upper_put_iv is None:
            return None

        # Calculate average IVs at each strike
        lower_avg = (lower_call_iv.value + lower_put_iv.value) / 2.0
        upper_avg = (upper_call_iv.value + upper_put_iv.value) / 2.0

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

        Uses shared calculation logic from implied_move_common, with the same straddle-cost
        validation the standard calculator runs.

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
        return calculate_from_atm_chain(chain, ticker, expiration, validate_straddle_cost=True)
