"""Directional bias determination and strategy type selection."""
import logging
from typing import List, Optional

from src.domain.types import VRPResult, SkewResult
from src.domain.enums import StrategyType, DirectionalBias

logger = logging.getLogger(__name__)


def determine_bias(skew: Optional[SkewResult]) -> DirectionalBias:
    """
    Determine directional bias from IV skew with 7-level strength scale.

    Args:
        skew: Skew analysis (optional)

    Returns:
        DirectionalBias enum (7 levels)
    """
    if not skew:
        return DirectionalBias.NEUTRAL

    # Handle SkewAnalysis with DirectionalBias enum (new format)
    if hasattr(skew, 'directional_bias'):
        # Already an enum, return directly
        if isinstance(skew.directional_bias, DirectionalBias):
            return skew.directional_bias

        # Legacy string support (backward compatibility)
        bias_str = skew.directional_bias
        bias_map = {
            'strong_bearish': DirectionalBias.STRONG_BEARISH,
            'bearish': DirectionalBias.BEARISH,
            'weak_bearish': DirectionalBias.WEAK_BEARISH,
            'neutral': DirectionalBias.NEUTRAL,
            'weak_bullish': DirectionalBias.WEAK_BULLISH,
            'bullish': DirectionalBias.BULLISH,
            'strong_bullish': DirectionalBias.STRONG_BULLISH,
            'put_bias': DirectionalBias.BEARISH,
            'call_bias': DirectionalBias.BULLISH,
        }
        return bias_map.get(bias_str, DirectionalBias.NEUTRAL)

    # Fallback to old SkewResult format
    elif hasattr(skew, 'direction'):
        if skew.direction == 'bearish':
            return DirectionalBias.BEARISH
        elif skew.direction == 'bullish':
            return DirectionalBias.BULLISH
        else:
            return DirectionalBias.NEUTRAL

    return DirectionalBias.NEUTRAL


def select_strategy_types(
    vrp: VRPResult,
    bias: DirectionalBias,
    tail_risk_level: Optional[str] = None,
) -> List[StrategyType]:
    """
    Select strategy types based on VRP, bias, and tail risk (7-level scale).

    Iron Condors and Iron Butterflies are excluded from all paths — backtesting
    showed a misleadingly high win rate masking catastrophic loss asymmetry
    on the rare losers.

    HIGH TRR (tail_risk_level='HIGH') restricts to a single directional spread;
    the oversized tail risk makes multi-leg hedging unreliable.

    Args:
        vrp: VRP analysis
        bias: Directional bias (7-level scale)
        tail_risk_level: 'HIGH', 'NORMAL', 'LOW', or None

    Returns:
        List of 1-2 strategy types to generate
    """
    is_bullish = bias.is_bullish()
    is_bearish = bias.is_bearish()
    is_neutral = bias.is_neutral()
    strength = bias.strength()  # 0=NEUTRAL, 1=WEAK, 2=MODERATE, 3=STRONG

    if vrp.vrp_ratio >= 2.0:
        # Excellent VRP
        if is_neutral or strength == 1:
            types = [StrategyType.BULL_PUT_SPREAD, StrategyType.BEAR_CALL_SPREAD]
        elif is_bullish:
            if strength == 3:
                types = [StrategyType.BULL_PUT_SPREAD]
            else:
                types = [StrategyType.BULL_PUT_SPREAD, StrategyType.BEAR_CALL_SPREAD]
        else:  # bearish
            if strength == 3:
                types = [StrategyType.BEAR_CALL_SPREAD]
            else:
                types = [StrategyType.BEAR_CALL_SPREAD, StrategyType.BULL_PUT_SPREAD]

    elif vrp.vrp_ratio >= 1.5:
        # Good VRP
        if is_neutral or strength == 1:
            types = [StrategyType.BULL_PUT_SPREAD, StrategyType.BEAR_CALL_SPREAD]
        elif is_bullish:
            types = [StrategyType.BULL_PUT_SPREAD]
        else:
            types = [StrategyType.BEAR_CALL_SPREAD]

    else:
        # Marginal VRP — single best strategy
        if is_bearish:
            types = [StrategyType.BEAR_CALL_SPREAD]
        else:
            types = [StrategyType.BULL_PUT_SPREAD]

    # HIGH TRR: restrict to one directional spread — tail risk makes hedging unreliable
    if tail_risk_level == 'HIGH':
        types = types[:1]

    logger.debug(
        f"Strategy selection: VRP={vrp.vrp_ratio:.2f}, "
        f"Bias={bias.value} (strength={strength}), "
        f"TRR={tail_risk_level}, "
        f"Selected={[t.value for t in types]}"
    )

    return types
