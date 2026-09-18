"""Strike selection: delta-based, distance-based, and implied-move verification."""
import logging
from typing import List, Optional, Tuple

from src.config.config import StrategyConfig
from src.domain.enums import OptionType
from src.domain.types import OptionChain, Strike, VRPResult

logger = logging.getLogger(__name__)


def find_nearest_strike(
    strikes: List[Strike], target_price: float
) -> Optional[Strike]:
    """Find the strike nearest to target price, preferring whole-dollar strikes."""
    if not strikes:
        return None

    whole_dollar = [s for s in strikes if float(s.price) % 1.0 == 0.0]
    candidates = whole_dollar if whole_dollar else strikes
    return min(candidates, key=lambda s: abs(float(s.price) - target_price))


def verify_strikes_outside_implied_move(
    ticker: str,
    strikes: Tuple[Strike, Strike],
    option_chain: OptionChain,
    vrp: VRPResult,
    below: bool,
) -> Optional[Tuple[Strike, Strike]]:
    """
    Verify that selected strikes are outside the implied move zone.

    Returns:
        Original strikes if valid, None if inside implied move zone
    """
    stock_price = float(option_chain.stock_price.amount)
    implied_move_pct = vrp.implied_move_pct.value / 100
    implied_move_dollars = stock_price * implied_move_pct

    short_strike, long_strike = strikes
    short_price = float(short_strike.price)

    if below:
        # Put spread: short strike must be below lower bound
        lower_bound = stock_price - implied_move_dollars
        if short_price >= lower_bound:
            logger.debug(
                f"{ticker}: Short strike ${short_price:.2f} is inside implied move "
                f"(lower bound: ${lower_bound:.2f})"
            )
            return None
    else:
        # Call spread: short strike must be above upper bound
        upper_bound = stock_price + implied_move_dollars
        if short_price <= upper_bound:
            logger.debug(
                f"{ticker}: Short strike ${short_price:.2f} is inside implied move "
                f"(upper bound: ${upper_bound:.2f})"
            )
            return None

    return strikes


def select_strikes_delta_based(
    config: StrategyConfig,
    option_chain: OptionChain,
    option_type: OptionType,
) -> Optional[Tuple[Strike, Strike]]:
    """
    Select strikes nearest the configured short and long deltas.

    More precise than distance-based when Greeks are available. The short is
    the strike nearest the short delta; the long is the strike nearest the long
    delta among those strictly further OTM than the short. Choosing the two
    independently returned one strike for both legs whenever 25Δ and 20Δ map to
    the same listed strike (a sparse chain, wide spacing -- FDX): a zero-width
    spread that passed the implied-move check, so no fallback ran, and the
    builder then refused it for zero credit. None when nothing further OTM is
    listed, which sends select_strikes_for_spread to distance-based selection.

    Returns:
        Tuple of (short_strike, long_strike) or None
    """
    chain = option_chain.puts if option_type == OptionType.PUT else option_chain.calls

    # Prefer whole-dollar strikes; use the full chain when they cannot form a pair
    whole_dollar_chain = {
        s: q for s, q in chain.items() if float(s.price) % 1.0 == 0.0
    }
    for candidate_chain in (whole_dollar_chain, chain):
        # Put deltas are negative, call deltas positive: match on magnitude
        deltas = {s: abs(q.delta) for s, q in candidate_chain.items() if q.delta}
        if not deltas:
            continue

        short_strike = min(deltas, key=lambda s: abs(deltas[s] - config.target_delta_short))
        further_otm = [
            s for s in deltas
            if (s < short_strike if option_type == OptionType.PUT else short_strike < s)
        ]
        if further_otm:
            long_strike = min(further_otm, key=lambda s: abs(deltas[s] - config.target_delta_long))
            return short_strike, long_strike

    return None


def select_strikes_distance_based(
    config: StrategyConfig,
    option_chain: OptionChain,
    vrp: VRPResult,
    option_type: OptionType,
    below: bool,
) -> Optional[Tuple[Strike, Strike]]:
    """
    Select strikes based on distance from current price.

    Position strikes OUTSIDE implied move range with buffer.

    Returns:
        Tuple of (short_strike, long_strike) or None
    """
    stock_price = float(option_chain.stock_price.amount)
    implied_move_pct = vrp.implied_move_pct.value / 100
    implied_move_dollars = stock_price * implied_move_pct

    # Add 10% buffer beyond implied move
    buffer = implied_move_dollars * 0.10

    # Spread width - Fixed dollar amounts based on stock price (user strategy)
    # >= $20: Use $5 spread width
    # < $20:  Use $3 spread width
    if stock_price >= config.spread_width_threshold:
        spread_width = config.spread_width_high_price
    else:
        spread_width = config.spread_width_low_price

    if below:
        # Put spread: Position below lower bound
        short_strike_price = stock_price - implied_move_dollars - buffer
        long_strike_price = short_strike_price - spread_width
    else:
        # Call spread: Position above upper bound
        short_strike_price = stock_price + implied_move_dollars + buffer
        long_strike_price = short_strike_price + spread_width

    # FIX: Use strikes from the specific chain (puts or calls), not all strikes!
    chain = option_chain.puts if option_type == OptionType.PUT else option_chain.calls
    available_strikes = sorted(chain.keys(), key=lambda s: float(s.price))

    short_strike = find_nearest_strike(available_strikes, short_strike_price)
    if not short_strike:
        return None

    # The long leg only from strikes strictly further OTM than the short: on a
    # chain spaced wider than the spread width the long target rounds back to
    # the short strike, a zero-width spread.
    further_otm = [
        s for s in available_strikes
        if (s < short_strike if below else short_strike < s)
    ]
    long_strike = find_nearest_strike(further_otm, long_strike_price)
    if not long_strike:
        return None

    return short_strike, long_strike


def select_strikes_for_spread(
    config: StrategyConfig,
    ticker: str,
    option_chain: OptionChain,
    vrp: VRPResult,
    option_type: OptionType,
    below: bool,
) -> Optional[Tuple[Strike, Strike]]:
    """
    Select (short_strike, long_strike) for a vertical spread.

    Tries delta-based selection at the configured deltas first (more precise
    when Greeks are available), verifies the short strike lies outside the
    implied move zone, then falls back to distance-based selection if needed.

    Directional bias never moves the deltas. Until 2026-09-14 a bearish bias
    sold the put spread at 30Δ (35Δ strong) and a bullish bias the call spread
    -- closer to the money on the side that loses if the bias is right -- and
    direction has no validated edge (skew measures hedge demand, not conviction).
    """
    strikes = select_strikes_delta_based(config, option_chain, option_type)

    if strikes:
        logger.debug(
            f"{ticker}: Delta-based selection: {option_type.value} "
            f"short=${float(strikes[0].price):.2f}, long=${float(strikes[1].price):.2f}"
        )
        strikes = verify_strikes_outside_implied_move(
            ticker, strikes, option_chain, vrp, below=below
        )

    if not strikes:
        logger.debug(f"{ticker}: Using distance-based selection (outside implied move)")
        strikes = select_strikes_distance_based(
            config, option_chain, vrp, option_type, below=below
        )
        if strikes:
            logger.debug(
                f"{ticker}: Distance-based selection: {option_type.value} "
                f"short=${float(strikes[0].price):.2f}, long=${float(strikes[1].price):.2f}"
            )
        else:
            logger.warning(f"{ticker}: Distance-based selection failed for {option_type.value}")

    return strikes
