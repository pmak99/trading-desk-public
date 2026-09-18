"""Strategy rationale text generation."""
from typing import List

from src.domain.enums import Recommendation, StrategyType
from src.domain.scoring.weights import ScoringWeights
from src.domain.types import Strategy, VRPResult

# Below this score gap (0-100 scale), two strategies are considered
# statistically indistinguishable for disclosure purposes -- not a claim that
# the pick is "wrong", just that the margin is too thin to call it a real edge.
COIN_FLIP_SCORE_MARGIN = 5.0


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

    # Liquidity warning (POST-LOSS ANALYSIS - Added to prevent repeating WDAY/ZS mistakes)
    if strategy.liquidity_tier is not None:
        tier = strategy.liquidity_tier.upper()
        if tier == "WARNING":
            parts.append("⚠️ LOW LIQUIDITY")
        elif tier == "REJECT":
            parts.append("❌ VERY LOW LIQUIDITY (REJECT: not tradeable as a spread, CLAUDE.md §2)")
        elif tier == "EXCELLENT":
            parts.append("✓ High liquidity")

    # VRP edge, named by the tier the trade is sized on (CLAUDE.md §1)
    if vrp.recommendation is Recommendation.EXCELLENT:
        parts.append("Excellent VRP edge")
    elif vrp.recommendation is Recommendation.GOOD:
        parts.append("Good VRP")

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

    # Spread exit rule. Evidence: CLAUDE.md §8, printed by scripts/rule_evidence.py.
    if strategy.strategy_type in {StrategyType.BULL_PUT_SPREAD, StrategyType.BEAR_CALL_SPREAD}:
        parts.append("EXIT next trading day (spreads held longer win less often)")

    return ", ".join(parts) if parts else "Defined risk outside expected move"


def generate_recommendation_rationale(
    strategies: List[Strategy],
    vrp: VRPResult,
    weights: ScoringWeights,
) -> str:
    """
    Generate rationale for the top-ranked strategy.

    Args:
        strategies: Scored strategies in the generator's order -- tradeable
            first, then by score (only strategies[0] is described; the rest are
            only consulted to detect a coin-flip pick)

    Returns:
        Human-readable recommendation rationale
    """
    strategy = strategies[0]
    parts = []

    # Strategy type
    if strategy.strategy_type == StrategyType.BULL_PUT_SPREAD:
        parts.append("Bull Put Spread best")
    else:
        parts.append("Bear Call Spread best")

    # Why it's best
    if vrp.recommendation is Recommendation.EXCELLENT:
        parts.append(f"excellent VRP ({vrp.vrp_ratio:.2f}x)")

    if strategy.reward_risk_ratio >= weights.rr_favorable_threshold:
        parts.append(f"strong R/R ({strategy.reward_risk_ratio:.2f})")

    if strategy.probability_of_profit >= weights.pop_high_threshold:
        parts.append(f"high POP ({strategy.probability_of_profit:.0%})")

    # Position sizing. Every offered spread REJECT: none is sized to trade.
    if strategy.liquidity_rejected:
        parts.append(
            "❌ liquidity REJECT: not tradeable as a spread — the ATM single at the "
            "broker decides (bid ≥ $1.50 and OI ≥ 200: a single at half size; else skip)"
        )
    else:
        parts.append(f"{strategy.contracts} contracts")

    # Disclosure: direction scores nothing, so whenever two spreads were scored
    # against each other there is NO market-direction signal behind the pick,
    # whatever the bias. It won on non-directional scoring only (POP/liquidity/
    # credit), and in an equity chain those inputs structurally favour the put
    # side -- put skew makes a 25-delta put richer than a 25-delta call, so
    # BULL_PUT wins by construction, not by any view on direction. Until
    # 2026-09-14 this fired for NEUTRAL bias only, because the scorer gave the
    # bias side up to +8 and the other side -3.
    #
    # Measured against the live scorer (Sep 2026 AVGO/LULU review): on a
    # perfectly symmetric chain both spreads score an identical 81.00 and the
    # winner is decided by list order alone; adding realistic put skew hands
    # BULL_PUT +1.1 pts, and heavy skew +9.3 pts. So the size of the gap is a
    # measure of skew richness, NOT of directional confidence -- which is why
    # this fires for every two-spread pick and not only for close ones.
    if len(strategies) > 1:
        runner_up = strategies[1]
        gap = strategy.overall_score - runner_up.overall_score
        other = (
            "Bear Call Spread" if strategy.strategy_type == StrategyType.BULL_PUT_SPREAD
            else "Bull Put Spread"
        )
        if runner_up.liquidity_rejected and not strategy.liquidity_rejected:
            # Ranked first because the other side cannot be traded as a spread,
            # whatever the scores: the gap says nothing, and may be negative.
            parts.append(
                f"⚠️ no directional edge — {other} is liquidity REJECT, "
                f"not tradeable as a spread (CLAUDE.md §2)"
            )
        elif gap < COIN_FLIP_SCORE_MARGIN:
            parts.append(
                f"⚠️ no directional edge — effectively a coin flip "
                f"({gap:.1f} pts over {other}, non-directional scoring only)"
            )
        else:
            # Put skew can only widen a put spread's lead; a call spread that
            # wins wide won on liquidity or credit.
            why = (
                "reflects richer put skew, not higher confidence"
                if strategy.strategy_type == StrategyType.BULL_PUT_SPREAD
                else "is not higher confidence"
            )
            parts.append(
                f"⚠️ no directional edge — won on premium/liquidity, "
                f"not direction ({gap:.1f} pts over {other}). A wider gap here {why}"
            )

    return "; ".join(parts)
