"""Strike selection: delta-based, distance-based, and implied-move verification."""
import logging
from typing import List, Optional, Tuple

from src.domain.types import OptionChain, Strike, VRPResult
from src.domain.enums import DirectionalBias, OptionType
from src.config.config import StrategyConfig

logger = logging.getLogger(__name__)

# Delta adjustment magnitudes for asymmetric positioning
DELTA_ADJUSTMENT_STRONG = 0.10    # ±10Δ for STRONG bias
DELTA_ADJUSTMENT_MODERATE = 0.05  # ±5Δ for MODERATE bias
DELTA_ADJUSTMENT_WEAK = 0.02      # ±2Δ for WEAK bias

# Delta bounds and spread requirements
MIN_DELTA = 0.10        # Minimum delta (10Δ, far OTM)
MAX_DELTA = 0.40        # Maximum delta (40Δ, closer to ATM)
MIN_SPREAD = 0.05       # Minimum delta spread between short and long


def get_asymmetric_deltas(
    config: StrategyConfig,
    option_type: OptionType,
    bias: DirectionalBias,
) -> Tuple[float, float]:
    """
    Calculate asymmetric delta targets based on directional bias.

    Strategy:
    - BULLISH bias → Put spread safer (lower delta), Call spread riskier (higher delta)
    - BEARISH bias → Call spread safer (lower delta), Put spread riskier (higher delta)
    - NEUTRAL → Balanced deltas
    - Strength determines magnitude of adjustment

    Returns:
        Tuple of (target_delta_short, target_delta_long)
    """
    base_short = config.target_delta_short  # 0.25
    base_long = config.target_delta_long    # 0.20

    adjustment = 0.0

    if bias in {DirectionalBias.STRONG_BULLISH, DirectionalBias.STRONG_BEARISH}:
        adjustment = DELTA_ADJUSTMENT_STRONG
    elif bias in {DirectionalBias.BULLISH, DirectionalBias.BEARISH}:
        adjustment = DELTA_ADJUSTMENT_MODERATE
    elif bias in {DirectionalBias.WEAK_BULLISH, DirectionalBias.WEAK_BEARISH}:
        adjustment = DELTA_ADJUSTMENT_WEAK
    else:  # NEUTRAL
        adjustment = 0.0

    is_bullish = bias in {DirectionalBias.WEAK_BULLISH, DirectionalBias.BULLISH, DirectionalBias.STRONG_BULLISH}
    is_bearish = bias in {DirectionalBias.WEAK_BEARISH, DirectionalBias.BEARISH, DirectionalBias.STRONG_BEARISH}

    if option_type == OptionType.PUT:
        # Put spread
        if is_bullish:
            # Bullish bias → put spread safer (lower delta = further OTM)
            delta_short = base_short - adjustment
            delta_long = base_long - adjustment
        elif is_bearish:
            # Bearish bias → put spread riskier (higher delta = closer to ATM)
            delta_short = base_short + adjustment
            delta_long = base_long + adjustment
        else:
            delta_short = base_short
            delta_long = base_long
    else:  # CALL
        # Call spread
        if is_bearish:
            # Bearish bias → call spread safer (lower delta = further OTM)
            delta_short = base_short - adjustment
            delta_long = base_long - adjustment
        elif is_bullish:
            # Bullish bias → call spread riskier (higher delta = closer to ATM)
            delta_short = base_short + adjustment
            delta_long = base_long + adjustment
        else:
            delta_short = base_short
            delta_long = base_long

    # Enforce spread BEFORE clamping (critical order!)
    # Ensure long is always lower delta than short (further OTM)
    if delta_long >= delta_short:
        delta_long = delta_short - MIN_SPREAD

    # NOW clamp to valid ranges (after spread is enforced)
    delta_short = max(MIN_DELTA, min(MAX_DELTA, delta_short))
    delta_long = max(MIN_DELTA, min(MAX_DELTA, delta_long))

    # Final safety check: clamping might have violated spread
    if delta_long >= delta_short:
        logger.warning(
            f"Delta conflict after clamping: short={delta_short:.2f}, "
            f"long={delta_long:.2f}. Using fallback deltas."
        )
        # Fallback to safe defaults
        delta_short = 0.25
        delta_long = 0.20

    logger.debug(
        f"Asymmetric deltas: {option_type.value} {bias.value} → "
        f"short={delta_short:.2f}Δ, long={delta_long:.2f}Δ "
        f"(adjustment={adjustment:+.2f})"
    )

    return delta_short, delta_long


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
    target_delta_short: Optional[float] = None,
    target_delta_long: Optional[float] = None,
) -> Optional[Tuple[Strike, Strike]]:
    """
    Select strikes based on delta (probability-based selection).

    More precise than distance-based when Greeks are available.

    Returns:
        Tuple of (short_strike, long_strike) or None
    """
    # Use config values if not specified
    if target_delta_short is None:
        target_delta_short = config.target_delta_short
    if target_delta_long is None:
        target_delta_long = config.target_delta_long

    chain = option_chain.puts if option_type == OptionType.PUT else option_chain.calls

    # Prefer whole-dollar strikes; fall back to full chain if none have deltas
    whole_dollar_chain = {
        s: q for s, q in chain.items() if float(s.price) % 1.0 == 0.0
    }
    candidate_chain = whole_dollar_chain if whole_dollar_chain else chain

    # Find strikes with deltas closest to targets
    short_strike = None
    long_strike = None
    min_short_diff = float('inf')
    min_long_diff = float('inf')

    for strike, quote in candidate_chain.items():
        if not quote.delta:
            continue  # Skip if no delta available

        # For puts: delta is negative, we want around -0.30 (short) and -0.20 (long)
        # For calls: delta is positive, we want around +0.30 (short) and +0.20 (long)
        delta_abs = abs(quote.delta)

        # Find short strike (higher delta, closer to ATM)
        short_diff = abs(delta_abs - target_delta_short)
        if short_diff < min_short_diff:
            min_short_diff = short_diff
            short_strike = strike

        # Find long strike (lower delta, further OTM)
        long_diff = abs(delta_abs - target_delta_long)
        if long_diff < min_long_diff:
            min_long_diff = long_diff
            long_strike = strike

    # If whole-dollar candidates had no deltas, retry on full chain
    if (not short_strike or not long_strike) and whole_dollar_chain:
        for strike, quote in chain.items():
            if not quote.delta:
                continue
            delta_abs = abs(quote.delta)
            short_diff = abs(delta_abs - target_delta_short)
            if short_diff < min_short_diff:
                min_short_diff = short_diff
                short_strike = strike
            long_diff = abs(delta_abs - target_delta_long)
            if long_diff < min_long_diff:
                min_long_diff = long_diff
                long_strike = strike

    if not short_strike or not long_strike:
        return None

    # Ensure proper ordering
    if option_type == OptionType.PUT:
        # Puts: short should be higher strike than long
        if short_strike < long_strike:
            short_strike, long_strike = long_strike, short_strike
    else:
        # Calls: short should be lower strike than long
        if short_strike > long_strike:
            short_strike, long_strike = long_strike, short_strike

    return short_strike, long_strike


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
    long_strike = find_nearest_strike(available_strikes, long_strike_price)

    if not short_strike or not long_strike:
        return None

    # Ensure proper ordering (short closer to price, long further)
    if below and short_strike < long_strike:
        return None
    if not below and short_strike > long_strike:
        return None

    return short_strike, long_strike


def select_strikes_for_spread(
    config: StrategyConfig,
    ticker: str,
    option_chain: OptionChain,
    vrp: VRPResult,
    option_type: OptionType,
    bias: DirectionalBias,
    below: bool,
) -> Optional[Tuple[Strike, Strike]]:
    """
    Select (short_strike, long_strike) for a vertical spread.

    Tries delta-based selection first (more precise when Greeks are available),
    verifies the short strike lies outside the implied move zone, then falls
    back to distance-based selection if needed.
    """
    target_delta_short, target_delta_long = get_asymmetric_deltas(config, option_type, bias)

    strikes = select_strikes_delta_based(
        config, option_chain, option_type, target_delta_short, target_delta_long
    )

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
