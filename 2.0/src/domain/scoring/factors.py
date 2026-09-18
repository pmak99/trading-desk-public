"""Pure factor-scoring functions for strategy scoring."""
from src.domain.scoring.weights import ScoringWeights
from src.domain.types import Strategy


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
    Calculate liquidity quality score (POST-LOSS ANALYSIS - Added Nov 2025).

    After a liquidity-driven loss, liquidity became critical.

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

