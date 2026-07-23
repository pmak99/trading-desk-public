"""
Strategy scoring — thin coordinator.

All factor scoring lives in src.domain.scoring.factors, directional
alignment in src.domain.scoring.alignment, rationale generation in
src.domain.scoring.rationale.
"""

import logging
from typing import List, Optional

from src.config.config import ScoringWeights
from src.domain.types import Strategy, VRPResult
from src.domain.enums import StrategyType, DirectionalBias
from src.domain.scoring.types import ScoringResult
from src.domain.scoring.factors import (
    calculate_greeks_score,
    calculate_liquidity_score,
    calculate_kelly_edge_score,
    calculate_profit_zone_multiplier,
)
from src.domain.scoring.alignment import apply_directional_alignment
from src.domain.scoring.rationale import (
    generate_strategy_rationale,
    generate_recommendation_rationale,
)

logger = logging.getLogger(__name__)


class StrategyScorer:
    """
    Scores and ranks trading strategies based on multiple factors.

    Thin coordinator: computation delegates to scoring.factors,
    scoring.alignment, and scoring.rationale modules.

    POST-LOSS ANALYSIS UPDATE:
    After a run of liquidity-driven losses, scoring weights were rebalanced
    to prioritize liquidity. Trade-outcome analysis showed position sizing
    was fine but poor liquidity caused expensive exits and amplified losses.

    Scoring factors (when Greeks available):
    - Probability of profit (POP) - default 40% weight (increased Dec 2025)
    - Liquidity quality - default 22% weight (added Nov 2025 after loss analysis)
    - VRP edge - default 17% weight (reduced to make room for POP increase)
    - Kelly edge (R/R × POP) - default 13% weight (reduced from 15%)
    - Greeks quality (theta/vega) - default 8% weight (reduced from 10%)
    - Position sizing - default 0% weight (removed - handled separately)

    When Greeks are not available, the greeks weight is redistributed
    proportionally to other factors, making liquidity even MORE important
    since we have less visibility into option pricing quality.
    """

    def __init__(self, weights: ScoringWeights | None = None):
        self.weights = weights if weights is not None else ScoringWeights()

    def score_strategy(
        self,
        strategy: Strategy,
        vrp: VRPResult,
        directional_bias: DirectionalBias | None = None,
    ) -> ScoringResult:
        """
        Score a single strategy.

        Args:
            strategy: Strategy to score
            vrp: VRP analysis for context
            directional_bias: Optional directional bias for alignment scoring

        Returns:
            ScoringResult with overall, profitability, and risk scores
        """
        has_greeks = strategy.position_theta is not None and strategy.position_vega is not None

        if has_greeks:
            overall, profitability, risk = self._score_with_greeks(strategy, vrp)
        else:
            overall, profitability, risk = self._score_without_greeks(strategy, vrp)

        if directional_bias is not None:
            overall = apply_directional_alignment(overall, strategy, directional_bias)

        rationale_text = generate_strategy_rationale(strategy, vrp, self.weights)

        return ScoringResult(
            overall_score=overall,
            profitability_score=profitability,
            risk_score=risk,
            strategy_rationale=rationale_text,
        )

    def score_strategies(
        self,
        strategies: List[Strategy],
        vrp: VRPResult,
        directional_bias: DirectionalBias | None = None,
    ) -> None:
        """
        Score and rank strategies in-place.

        Updates each strategy's overall_score, profitability_score, risk_score,
        and rationale fields.

        Args:
            strategies: List of strategies to score
            vrp: VRP analysis for context
            directional_bias: Optional directional bias for alignment scoring
        """
        for strategy in strategies:
            result = self.score_strategy(strategy, vrp, directional_bias)
            strategy.overall_score = result.overall_score
            strategy.profitability_score = result.profitability_score
            strategy.risk_score = result.risk_score
            strategy.rationale = result.strategy_rationale

    def generate_recommendation_rationale(
        self, strategy: Strategy, vrp: VRPResult, bias: DirectionalBias
    ) -> str:
        return generate_recommendation_rationale(strategy, vrp, bias, self.weights)

    # --- Private orchestration ---

    def _score_with_greeks(
        self, strategy: Strategy, vrp: VRPResult
    ) -> tuple[float, float, float]:
        """
        Score strategy with Greeks available.

        POST-LOSS ANALYSIS UPDATE:
        Added liquidity scoring (25% weight) after a liquidity-driven loss.
        New weights: POP 30%, Liquidity 25%, VRP 20%, R/R 15%, Greeks 10%

        KELLY EDGE FIX (Dec 2025):
        Replaced R/R scoring with Kelly edge scoring to prevent negative EV trades
        from outscoring positive EV trades.

        PROFIT ZONE FIX (Dec 2025):
        Added profit zone vs implied move penalty.

        Returns:
            Tuple of (overall_score, profitability_score, risk_score)
        """
        # Factor 1: Probability of Profit
        pop_score = min(
            strategy.probability_of_profit / self.weights.target_pop, 1.0
        ) * self.weights.pop_weight

        # Factor 2: Liquidity Quality
        liquidity_score = calculate_liquidity_score(strategy, self.weights)

        # Factor 3: VRP Edge
        vrp_score = min(
            vrp.vrp_ratio / self.weights.target_vrp, 1.0
        ) * self.weights.vrp_weight

        # Factor 4: Kelly Edge
        edge_score = calculate_kelly_edge_score(
            strategy.probability_of_profit,
            strategy.reward_risk_ratio,
            self.weights.reward_risk_weight,
        )

        # Factor 5: Greeks Quality
        greeks_score = calculate_greeks_score(strategy, self.weights)

        # Factor 6: Position Sizing
        size_score = min(strategy.contracts / 10.0, 1.0) * self.weights.size_weight

        base_score = pop_score + liquidity_score + vrp_score + edge_score + greeks_score + size_score

        # PROFIT ZONE FIX: Apply penalty for narrow profit zones vs implied move
        profit_zone_multiplier = calculate_profit_zone_multiplier(strategy, vrp)
        overall = base_score * profit_zone_multiplier

        # Profitability score (include theta benefit)
        base_profitability = min(strategy.reward_risk_ratio / 0.40 * 80, 80)
        theta_benefit = (
            min(strategy.position_theta / 50.0 * 20, 20)
            if strategy.position_theta and strategy.position_theta > 0
            else 0
        )
        profitability = base_profitability + theta_benefit

        # Risk score (lower is safer) - Include vega risk
        base_risk = (1.0 - strategy.probability_of_profit) * 70
        vega_risk = (
            min(abs(strategy.position_vega) / 100.0 * 30, 30)
            if strategy.position_vega and strategy.position_vega > 0
            else 0
        )
        risk = base_risk + vega_risk

        return overall, profitability, risk

    def _score_without_greeks(
        self, strategy: Strategy, vrp: VRPResult
    ) -> tuple[float, float, float]:
        """
        Score strategy without Greeks available.

        POST-LOSS ANALYSIS UPDATE (Nov 2025):
        Added liquidity scoring. When Greeks unavailable, uses config-defined
        no-Greeks weights which redistribute the 8% Greeks weight.

        BUG FIX (Dec 2025):
        Fixed scoring inflation bug using config-defined no-Greeks weights
        that properly sum to 100%.

        Returns:
            Tuple of (overall_score, profitability_score, risk_score)
        """
        # Use config-defined no-Greeks weights (sum to 100%)
        # POP: 40%→45%, Liquidity: 22%→26%, VRP: 17%→17%, Edge: 13%→12%

        # Factor 1: Probability of Profit
        pop_score = min(
            strategy.probability_of_profit / self.weights.target_pop, 1.0
        ) * self.weights.pop_weight_no_greeks

        # Factor 2: Liquidity Quality - CRITICAL when Greeks unavailable
        # Uses no-greeks weight (higher than greeks path: 22%→26%)
        if strategy.liquidity_tier is None:
            liquidity_score = self.weights.liquidity_weight_no_greeks
        else:
            tier = strategy.liquidity_tier.upper()
            if tier in ("EXCELLENT", "GOOD"):
                liquidity_score = self.weights.liquidity_weight_no_greeks
            elif tier == "WARNING":
                liquidity_score = self.weights.liquidity_weight_no_greeks * 0.5
            elif tier == "REJECT":
                liquidity_score = 0.0
            else:
                liquidity_score = self.weights.liquidity_weight_no_greeks

        # Factor 3: VRP Edge
        vrp_score = min(
            vrp.vrp_ratio / self.weights.target_vrp, 1.0
        ) * self.weights.vrp_weight_no_greeks

        # Factor 4: Kelly Edge
        edge_score = calculate_kelly_edge_score(
            strategy.probability_of_profit,
            strategy.reward_risk_ratio,
            self.weights.reward_risk_weight_no_greeks,
        )

        # Factor 5: Position Sizing
        size_score = min(strategy.contracts / 10.0, 1.0) * self.weights.size_weight_no_greeks

        base_score = pop_score + liquidity_score + vrp_score + edge_score + size_score

        # PROFIT ZONE FIX
        profit_zone_multiplier = calculate_profit_zone_multiplier(strategy, vrp)
        overall = base_score * profit_zone_multiplier

        # Profitability score (focus on reward/risk)
        profitability = min(strategy.reward_risk_ratio / 0.40 * 100, 100)

        # Risk score (lower is safer)
        risk = (1.0 - strategy.probability_of_profit) * 100

        return overall, profitability, risk

    # --- Backward-compat shims (private methods called directly by existing tests) ---

    def _calculate_greeks_score(self, strategy: Strategy) -> float:
        return calculate_greeks_score(strategy, self.weights)

    def _calculate_liquidity_score(self, strategy: Strategy) -> float:
        return calculate_liquidity_score(strategy, self.weights)

    def _calculate_kelly_edge_score(
        self, pop: float, rr: float, weight: Optional[float] = None
    ) -> float:
        if weight is None:
            weight = self.weights.reward_risk_weight
        return calculate_kelly_edge_score(pop, rr, weight)

    def _calculate_profit_zone_multiplier(self, strategy: Strategy, vrp: VRPResult) -> float:
        return calculate_profit_zone_multiplier(strategy, vrp)

    def _apply_directional_alignment(
        self, base_score: float, strategy: Strategy, bias: DirectionalBias
    ) -> float:
        return apply_directional_alignment(base_score, strategy, bias)

    def _generate_strategy_rationale(self, strategy: Strategy, vrp: VRPResult) -> str:
        return generate_strategy_rationale(strategy, vrp, self.weights)
