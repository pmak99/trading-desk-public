"""Strategy rationale text generation."""
from src.config.config import ScoringWeights
from src.domain.types import Strategy, VRPResult
from src.domain.enums import StrategyType, DirectionalBias


def generate_strategy_rationale(
    strategy: Strategy,
    vrp: VRPResult,
    weights: ScoringWeights,
) -> str:
    """
    Generate brief rationale for strategy.

    POST-LOSS ANALYSIS UPDATE (Nov 2025):
    Added liquidity warnings to rationale to make liquidity issues visible.

    Returns:
        Human-readable rationale string
    """
    parts = []

    # Liquidity warning (added to prevent repeating a past liquidity-driven loss)
    if strategy.liquidity_tier is not None:
        tier = strategy.liquidity_tier.upper()
        if tier == "WARNING":
            parts.append("⚠️ LOW LIQUIDITY")
        elif tier == "REJECT":
            parts.append("❌ VERY LOW LIQUIDITY")
        elif tier == "EXCELLENT":
            parts.append("✓ High liquidity")

    # VRP edge
    if vrp.vrp_ratio >= weights.vrp_excellent_threshold:
        parts.append("Excellent VRP edge")
    elif vrp.vrp_ratio >= weights.vrp_strong_threshold:
        parts.append("Strong VRP")

    # Reward/risk
    if strategy.reward_risk_ratio >= weights.rr_favorable_threshold:
        parts.append("favorable R/R")

    # Probability of profit
    if strategy.probability_of_profit >= weights.pop_high_threshold:
        parts.append("high POP")

    # Greeks information if available
    if strategy.position_theta is not None and strategy.position_theta > weights.theta_positive_threshold:
        parts.append(f"positive theta (${strategy.position_theta:.0f}/day)")

    if strategy.position_vega is not None and strategy.position_vega < weights.vega_beneficial_threshold:
        parts.append("benefits from IV crush")

    # Spread exit rule (data-driven)
    # Spreads held 2+ days: 31% win rate. Singles held 2+ days: 78% win rate.
    if strategy.strategy_type in {StrategyType.BULL_PUT_SPREAD, StrategyType.BEAR_CALL_SPREAD}:
        parts.append("EXIT next trading day (spreads: 31% win if held 2+ days)")

    return ", ".join(parts) if parts else "Defined risk outside expected move"


def generate_recommendation_rationale(
    strategy: Strategy,
    vrp: VRPResult,
    bias: DirectionalBias,
    weights: ScoringWeights,
) -> str:
    """
    Generate rationale for recommended strategy.

    Returns:
        Human-readable recommendation rationale
    """
    parts = []

    # Strategy type
    if strategy.strategy_type == StrategyType.BULL_PUT_SPREAD:
        parts.append("Bull Put Spread best")
    else:
        parts.append("Bear Call Spread best")

    # Why it's best
    if vrp.vrp_ratio >= weights.vrp_excellent_threshold:
        parts.append(f"excellent VRP (>{weights.vrp_excellent_threshold:.1f}x)")

    if strategy.reward_risk_ratio >= weights.rr_favorable_threshold:
        parts.append(f"strong R/R ({strategy.reward_risk_ratio:.2f})")

    if strategy.probability_of_profit >= weights.pop_high_threshold:
        parts.append(f"high POP ({strategy.probability_of_profit:.0%})")

    # Position sizing
    parts.append(f"{strategy.contracts} contracts")

    return "; ".join(parts)
