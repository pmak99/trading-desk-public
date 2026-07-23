"""Pure factor-scoring functions for strategy scoring."""
import logging

from src.config.config import ScoringWeights
from src.domain.types import Strategy, VRPResult

logger = logging.getLogger(__name__)


def calculate_greeks_score(strategy: Strategy, weights: ScoringWeights) -> float:
    """
    Calculate Greeks quality score.

    For credit spreads:
    - Positive theta is excellent (we earn from time decay)
    - Negative vega is excellent (we benefit from IV crush)

    Returns:
        Greeks score (0 to weights.greeks_weight)
    """
    theta_score = 0.0
    vega_score = 0.0

    if strategy.position_theta is not None:
        # Theta: Positive is good (we earn from time decay)
        if strategy.position_theta > 0:
            theta_score = min(strategy.position_theta / weights.target_theta, 1.0) * (
                weights.greeks_weight / 2
            )
        else:
            # Penalize negative theta (paying time decay) - score 0
            theta_score = 0.0

    if strategy.position_vega is not None:
        # Vega: Negative is good for credit spreads (we benefit from IV crush)
        if strategy.position_vega < 0:
            vega_score = min(abs(strategy.position_vega) / weights.target_vega, 1.0) * (
                weights.greeks_weight / 2
            )
        elif strategy.position_vega <= 10:
            # Near-zero positive vega (0 to +10): partial credit
            near_zero_fraction = 1.0 - (strategy.position_vega / 10.0)
            vega_score = 0.5 * near_zero_fraction * (weights.greeks_weight / 2)
        else:
            # Positive vega > 10: hurt by IV decrease - score 0
            vega_score = 0.0

    return theta_score + vega_score


def calculate_liquidity_score(strategy: Strategy, weights: ScoringWeights) -> float:
    """
    Calculate liquidity quality score (added after a liquidity-driven loss
    made clear that poor fills can erase a real VRP edge).

    Scoring logic (4-tier system):
    - EXCELLENT tier: 100% of liquidity_weight
    - GOOD tier: 100% of liquidity_weight (full size per CLAUDE.md)
    - WARNING tier: 50% of liquidity_weight
    - REJECT tier: 0% (allowed but penalized)
    - No tier info: 100% (backward compatibility)

    Returns:
        Liquidity score (0 to weights.liquidity_weight)
    """
    if strategy.liquidity_tier is None:
        return weights.liquidity_weight

    tier = strategy.liquidity_tier.upper()

    if tier in ("EXCELLENT", "GOOD"):
        return weights.liquidity_weight
    elif tier == "WARNING":
        return weights.liquidity_weight * 0.5
    elif tier == "REJECT":
        return 0.0
    else:
        return weights.liquidity_weight


def calculate_kelly_edge_score(pop: float, rr: float, weight: float) -> float:
    """
    Calculate Kelly edge score (KELLY EDGE FIX - Added Dec 2025).

    Replaces raw R/R scoring to prevent negative EV trades from outscoring
    positive EV trades.

    Kelly Edge Formula:
        edge = (p × b) - q
        where:
            p = probability of profit (POP)
            b = reward/risk ratio (R/R)
            q = 1 - p

    Args:
        pop: Probability of profit (0.0 to 1.0)
        rr: Reward/risk ratio
        weight: Weight to apply (e.g. weights.reward_risk_weight)

    Returns:
        Kelly edge score (0 to weight points)
    """
    q = 1.0 - pop
    edge = pop * rr - q

    # Negative edge scores 0 (reject negative EV trades)
    if edge <= 0:
        return 0.0

    # Positive edge: Score proportional to edge
    # Target 10% edge for full points (aggressive but achievable with VRP)
    target_edge = 0.10
    normalized_edge = min(edge / target_edge, 1.0)

    return normalized_edge * weight


def calculate_profit_zone_multiplier(strategy: Strategy, vrp: VRPResult) -> float:
    """
    Calculate profit zone multiplier (PROFIT ZONE FIX - Added Dec 2025).

    Penalizes strategies with narrow profit zones when implied move is large.
    This prevents Iron Butterflies and tight Iron Condors from being recommended
    when the stock is expected to move far beyond their profit range.

    Multiplier ranges (Jan 2026: raised floor from 0.3→0.6 for Iron Condors):
    - 1.0 (no penalty): Profit zone >= implied move
    - 0.9-1.0: Profit zone is 70-100% of implied move (slight penalty)
    - 0.8-0.9: Profit zone is 40-70% of implied move (moderate penalty)
    - 0.7-0.8: Profit zone is 20-40% of implied move (heavy penalty)
    - 0.6: Profit zone < 20% of implied move (severe penalty)

    Returns:
        Multiplier between 0.6 and 1.0 to apply to overall score
    """
    if not strategy.breakeven or len(strategy.breakeven) == 0:
        return 1.0

    if len(strategy.breakeven) >= 2:
        breakevens_sorted = sorted([float(be.amount) for be in strategy.breakeven])
        lower_be = breakevens_sorted[0]
        upper_be = breakevens_sorted[-1]
        profit_zone_width = upper_be - lower_be

        # Use actual stock price from strategy (FIX: was using breakeven midpoint)
        stock_price_estimate = float(strategy.stock_price.amount)
    else:
        # Single breakeven (credit spread)
        # Credit spreads have one-sided risk — give full score
        return 1.0

    if stock_price_estimate <= 0:
        return 1.0
    profit_zone_pct = (profit_zone_width / stock_price_estimate) * 100

    # Get implied move percentage (one-sided: up OR down)
    implied_move_pct = vrp.implied_move_pct.value

    # CRITICAL FIX: Implied move is one-sided, but stock can move ±X%
    # Total expected range = 2 × implied_move_pct (e.g., ±15% = 30% total range)
    total_expected_range_pct = 2 * implied_move_pct

    if total_expected_range_pct <= 0:
        return 1.0
    zone_to_move_ratio = profit_zone_pct / total_expected_range_pct

    # Apply penalty based on ratio
    if zone_to_move_ratio >= 1.0:
        # Profit zone covers full implied move - no penalty
        return 1.0
    elif zone_to_move_ratio >= 0.70:
        # Profit zone is 70-100% of implied move - slight penalty
        # Linear interpolation: 0.70 → 0.9, 1.0 → 1.0
        multiplier = 0.9 + (zone_to_move_ratio - 0.70) * (0.1 / 0.30)
        logger.debug(
            f"{strategy.strategy_type.value}: Slight profit zone penalty "
            f"(zone {profit_zone_pct:.1f}% vs move ±{implied_move_pct:.1f}%, "
            f"multiplier={multiplier:.2f})"
        )
        return multiplier
    elif zone_to_move_ratio >= 0.40:
        # Profit zone is 40-70% of implied move - moderate penalty
        # Linear interpolation: 0.40 → 0.8, 0.70 → 0.9 (Jan 2026: raised from 0.7-0.9)
        multiplier = 0.8 + (zone_to_move_ratio - 0.40) * (0.1 / 0.30)
        logger.info(
            f"{strategy.strategy_type.value}: Moderate profit zone penalty "
            f"(zone {profit_zone_pct:.1f}% vs move ±{implied_move_pct:.1f}%, "
            f"multiplier={multiplier:.2f})"
        )
        return multiplier
    elif zone_to_move_ratio >= 0.20:
        # Profit zone is 20-40% of implied move - heavy penalty
        # Linear interpolation: 0.20 → 0.7, 0.40 → 0.8 (Jan 2026: raised from 0.5-0.7)
        multiplier = 0.7 + (zone_to_move_ratio - 0.20) * (0.1 / 0.20)
        logger.warning(
            f"{strategy.strategy_type.value}: Heavy profit zone penalty - "
            f"profit zone ({profit_zone_pct:.1f}%) much smaller than implied move "
            f"(±{implied_move_pct:.1f}%), multiplier={multiplier:.2f}"
        )
        return multiplier
    else:
        # Profit zone < 20% of implied move - severe penalty (Jan 2026: raised from 0.3)
        logger.warning(
            f"{strategy.strategy_type.value}: SEVERE profit zone penalty - "
            f"profit zone ({profit_zone_pct:.1f}%) is <20% of implied move "
            f"(±{implied_move_pct:.1f}%), multiplier=0.60. Consider rejecting."
        )
        return 0.6
