"""
Strategy scoring logic extracted from StrategyGenerator.

This module provides clean separation of scoring concerns from strategy generation,
making the scoring algorithm testable and maintainable independently.
"""

from dataclasses import dataclass
from typing import List

from src.config.config import ScoringWeights
from src.domain.models import Strategy, VRPResult, StrategyType, DirectionalBias


@dataclass
class ScoringResult:
    """Result of scoring a single strategy."""
    overall_score: float
    profitability_score: float
    risk_score: float
    strategy_rationale: str


class StrategyScorer:
    """
    Scores and ranks trading strategies based on multiple factors.

    Scoring factors (when Greeks available):
    - Probability of profit (POP) - default 45% weight
    - Reward/risk ratio (R/R) - default 20% weight
    - VRP edge - default 20% weight
    - Greeks quality (theta/vega) - default 10% weight
    - Position sizing - default 5% weight

    When Greeks are not available, the greeks weight is redistributed
    proportionally to other factors.
    """

    def __init__(self, weights: ScoringWeights | None = None):
        """
        Initialize scorer with weights.

        Args:
            weights: Scoring weights configuration. If None, uses defaults.
        """
        self.weights = weights if weights is not None else ScoringWeights()

    def score_strategy(self, strategy: Strategy, vrp: VRPResult) -> ScoringResult:
        """
        Score a single strategy.

        Args:
            strategy: Strategy to score
            vrp: VRP analysis for context

        Returns:
            ScoringResult with overall, profitability, and risk scores
        """
        # Check if Greeks are available
        has_greeks = strategy.position_theta is not None and strategy.position_vega is not None

        if has_greeks:
            overall, profitability, risk = self._score_with_greeks(strategy, vrp)
        else:
            overall, profitability, risk = self._score_without_greeks(strategy, vrp)

        # Generate rationale
        rationale = self._generate_strategy_rationale(strategy, vrp)

        return ScoringResult(
            overall_score=overall,
            profitability_score=profitability,
            risk_score=risk,
            strategy_rationale=rationale
        )

    def score_strategies(self, strategies: List[Strategy], vrp: VRPResult) -> None:
        """
        Score and rank strategies in-place.

        Updates each strategy's overall_score, profitability_score, risk_score,
        and rationale fields.

        Args:
            strategies: List of strategies to score
            vrp: VRP analysis for context
        """
        for strategy in strategies:
            result = self.score_strategy(strategy, vrp)
            strategy.overall_score = result.overall_score
            strategy.profitability_score = result.profitability_score
            strategy.risk_score = result.risk_score
            strategy.rationale = result.strategy_rationale

    def _score_with_greeks(
        self, strategy: Strategy, vrp: VRPResult
    ) -> tuple[float, float, float]:
        """
        Score strategy with Greeks available.

        Returns:
            Tuple of (overall_score, profitability_score, risk_score)
        """
        # Factor 1: Probability of Profit - Compare against target
        pop_score = min(
            strategy.probability_of_profit / self.weights.target_pop, 1.0
        ) * self.weights.pop_weight

        # Factor 2: Reward/Risk - Compare against target
        rr_score = min(
            strategy.reward_risk_ratio / self.weights.target_rr, 1.0
        ) * self.weights.reward_risk_weight

        # Factor 3: VRP Edge - Compare against target
        vrp_score = min(
            vrp.vrp_ratio / self.weights.target_vrp, 1.0
        ) * self.weights.vrp_weight

        # Factor 4: Greeks Quality - Theta and Vega
        greeks_score = self._calculate_greeks_score(strategy)

        # Factor 5: Position Sizing - Graduated scoring up to 10 contracts
        size_score = min(strategy.contracts / 10.0, 1.0) * self.weights.size_weight

        # Overall score (0-100)
        overall = rr_score + pop_score + vrp_score + greeks_score + size_score

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

        Redistributes greeks weight to other factors proportionally.

        Returns:
            Tuple of (overall_score, profitability_score, risk_score)
        """
        # Redistribute greeks weight to other factors
        total_base_weight = (
            self.weights.pop_weight
            + self.weights.reward_risk_weight
            + self.weights.vrp_weight
            + self.weights.size_weight
        )
        scale_factor = 100.0 / total_base_weight if total_base_weight > 0 else 1.0

        # Factor 1: Probability of Profit - Scaled
        pop_score = min(
            strategy.probability_of_profit / self.weights.target_pop, 1.0
        ) * self.weights.pop_weight * scale_factor

        # Factor 2: Reward/Risk - Scaled
        rr_score = min(
            strategy.reward_risk_ratio / self.weights.target_rr, 1.0
        ) * self.weights.reward_risk_weight * scale_factor

        # Factor 3: VRP Edge - Scaled
        vrp_score = min(
            vrp.vrp_ratio / self.weights.target_vrp, 1.0
        ) * self.weights.vrp_weight * scale_factor

        # Factor 4: Position Sizing - Scaled
        size_score = min(strategy.contracts / 10.0, 1.0) * self.weights.size_weight * scale_factor

        # Overall score (0-100)
        overall = rr_score + pop_score + vrp_score + size_score

        # Profitability score (focus on reward/risk)
        profitability = min(strategy.reward_risk_ratio / 0.40 * 100, 100)

        # Risk score (lower is safer)
        # Based on POP (higher POP = lower risk)
        risk = (1.0 - strategy.probability_of_profit) * 100

        return overall, profitability, risk

    def _calculate_greeks_score(self, strategy: Strategy) -> float:
        """
        Calculate Greeks quality score.

        For credit spreads:
        - Positive theta is excellent (we earn from time decay)
        - Negative vega is excellent (we benefit from IV crush)

        Args:
            strategy: Strategy with Greeks

        Returns:
            Greeks score (0-10)
        """
        theta_score = 0.0
        vega_score = 0.0

        if strategy.position_theta is not None:
            # Theta: Positive is good (we earn from time decay)
            # Normalize using target_theta from config
            if strategy.position_theta > 0:
                theta_score = min(strategy.position_theta / self.weights.target_theta, 1.0) * (
                    self.weights.greeks_weight / 2
                )
            else:
                # Penalize negative theta (paying time decay) - score 0
                theta_score = 0.0

        if strategy.position_vega is not None:
            # Vega: Negative is good for credit spreads (we benefit from IV crush)
            # Normalize using target_vega from config
            if strategy.position_vega < 0:
                vega_score = min(abs(strategy.position_vega) / self.weights.target_vega, 1.0) * (
                    self.weights.greeks_weight / 2
                )
            else:
                # Penalize positive vega (hurt by IV decrease) - score 0
                vega_score = 0.0

        return theta_score + vega_score

    def _generate_strategy_rationale(self, strategy: Strategy, vrp: VRPResult) -> str:
        """
        Generate brief rationale for strategy.

        Args:
            strategy: Strategy to generate rationale for
            vrp: VRP analysis

        Returns:
            Human-readable rationale string
        """
        parts = []

        # VRP edge
        if vrp.vrp_ratio >= self.weights.vrp_excellent_threshold:
            parts.append("Excellent VRP edge")
        elif vrp.vrp_ratio >= self.weights.vrp_strong_threshold:
            parts.append("Strong VRP")

        # Reward/risk
        if strategy.reward_risk_ratio >= self.weights.rr_favorable_threshold:
            parts.append("favorable R/R")

        # Probability of profit
        if strategy.probability_of_profit >= self.weights.pop_high_threshold:
            parts.append("high POP")

        # Greeks information if available
        if strategy.position_theta is not None and strategy.position_theta > self.weights.theta_positive_threshold:
            parts.append(f"positive theta (${strategy.position_theta:.0f}/day)")

        if strategy.position_vega is not None and strategy.position_vega < self.weights.vega_beneficial_threshold:
            parts.append("benefits from IV crush")

        # Strategy type specific
        if strategy.strategy_type == StrategyType.IRON_CONDOR:
            parts.append("wide profit zone")
        elif strategy.strategy_type == StrategyType.IRON_BUTTERFLY:
            parts.append("max profit at current price")

        return ", ".join(parts) if parts else "Defined risk outside expected move"

    def generate_recommendation_rationale(
        self, strategy: Strategy, vrp: VRPResult, bias: DirectionalBias
    ) -> str:
        """
        Generate rationale for recommended strategy.

        Args:
            strategy: Recommended strategy
            vrp: VRP analysis
            bias: Directional bias

        Returns:
            Human-readable recommendation rationale
        """
        parts = []

        # Strategy type
        if strategy.strategy_type == StrategyType.IRON_CONDOR:
            parts.append("Iron Condor optimal")
        elif strategy.strategy_type == StrategyType.IRON_BUTTERFLY:
            parts.append("Iron Butterfly best")
        elif strategy.strategy_type == StrategyType.BULL_PUT_SPREAD:
            parts.append("Bull Put Spread best")
        else:
            parts.append("Bear Call Spread best")

        # Why it's best
        if vrp.vrp_ratio >= self.weights.vrp_excellent_threshold:
            parts.append(f"excellent VRP (>{self.weights.vrp_excellent_threshold:.1f}x)")

        if strategy.reward_risk_ratio >= self.weights.rr_favorable_threshold:
            parts.append(f"strong R/R ({strategy.reward_risk_ratio:.2f})")

        if strategy.probability_of_profit >= self.weights.pop_high_threshold:
            parts.append(f"high POP ({strategy.probability_of_profit:.0%})")

        # Position sizing
        parts.append(f"{strategy.contracts} contracts")

        return "; ".join(parts)
