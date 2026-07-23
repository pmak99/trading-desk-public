"""Directional alignment bonus/penalty scoring."""
from src.domain.types import Strategy
from src.domain.enums import StrategyType, DirectionalBias


def apply_directional_alignment(
    base_score: float,
    strategy: Strategy,
    bias: DirectionalBias,
) -> float:
    """
    Apply directional alignment bonus/penalty to score.

    Rewards strategies that align with the directional bias from skew analysis.
    This prevents recommending neutral strategies when there's a strong
    directional signal.

    Alignment Bonuses:
    - STRONG bias + aligned strategy: +8 points
    - MODERATE bias + aligned strategy: +5 points
    - WEAK bias + aligned strategy: +3 points
    - Neutral strategy with neutral bias: 0 points
    - Counter-trend strategy: -3 points

    Strategy Classifications:
    - Bullish: Bull Put Spread (assumes stock stays above puts)
    - Bearish: Bear Call Spread (assumes stock stays below calls)
    - Neutral: Iron Condor, Iron Butterfly (permanently banned)

    Returns:
        Adjusted score with directional alignment applied (clamped 0–100)
    """
    strategy_type = strategy.strategy_type

    is_bullish_strategy = strategy_type == StrategyType.BULL_PUT_SPREAD
    is_bearish_strategy = strategy_type == StrategyType.BEAR_CALL_SPREAD
    # Neutral strategies permanently banned (Iron condor / butterfly)

    is_strong_bearish = bias == DirectionalBias.STRONG_BEARISH
    is_bearish = bias == DirectionalBias.BEARISH
    is_weak_bearish = bias == DirectionalBias.WEAK_BEARISH

    is_strong_bullish = bias == DirectionalBias.STRONG_BULLISH
    is_bullish = bias == DirectionalBias.BULLISH
    is_weak_bullish = bias == DirectionalBias.WEAK_BULLISH

    adjustment = 0.0

    # BEARISH BIAS
    if is_strong_bearish or is_bearish or is_weak_bearish:
        if is_bearish_strategy:
            # Aligned: Bear Call Spread with bearish bias
            if is_strong_bearish:
                adjustment = 8.0  # Strong alignment bonus
            elif is_bearish:
                adjustment = 5.0  # Moderate alignment bonus
            else:  # weak_bearish
                adjustment = 3.0  # Weak alignment bonus
        elif is_bullish_strategy:
            # Counter-trend: Bull Put Spread with bearish bias
            adjustment = -3.0  # Penalty for fighting the trend
        # Neutral strategies get no adjustment

    # BULLISH BIAS
    elif is_strong_bullish or is_bullish or is_weak_bullish:
        if is_bullish_strategy:
            # Aligned: Bull Put Spread with bullish bias
            if is_strong_bullish:
                adjustment = 8.0  # Strong alignment bonus
            elif is_bullish:
                adjustment = 5.0  # Moderate alignment bonus
            else:  # weak_bullish
                adjustment = 3.0  # Weak alignment bonus
        elif is_bearish_strategy:
            # Counter-trend: Bear Call Spread with bullish bias
            adjustment = -3.0  # Penalty for fighting the trend
        # Neutral strategies get no adjustment

    # NEUTRAL BIAS — no adjustments

    adjusted_score = base_score + adjustment

    # Ensure score stays in valid range
    return max(0.0, min(100.0, adjusted_score))
