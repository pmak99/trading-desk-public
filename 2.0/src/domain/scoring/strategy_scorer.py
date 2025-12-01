"""
Strategy scoring logic extracted from StrategyGenerator.

This module provides clean separation of scoring concerns from strategy generation,
making the scoring algorithm testable and maintainable independently.
"""

from dataclasses import dataclass
from typing import List

from src.config.config import ScoringWeights
from src.domain.types import Strategy, VRPResult
from src.domain.enums import StrategyType, DirectionalBias


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

    POST-LOSS ANALYSIS UPDATE (Nov 2025):
    After -$26,930 loss from WDAY/ZS/SYM, scoring weights were rebalanced
    to prioritize liquidity. TRUE P&L analysis showed position sizing was
    fine but poor liquidity caused expensive exits and amplified losses.

    Scoring factors (when Greeks available):
    - Probability of profit (POP) - default 30% weight (reduced from 45%)
    - Liquidity quality - default 25% weight (NEW - critical addition)
    - VRP edge - default 20% weight (unchanged)
    - Reward/risk ratio (R/R) - default 15% weight (reduced from 20%)
    - Greeks quality (theta/vega) - default 10% weight (unchanged)
    - Position sizing - default 0% weight (removed - handled separately)

    When Greeks are not available, the greeks weight is redistributed
    proportionally to other factors, making liquidity even MORE important
    since we have less visibility into option pricing quality.
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

        POST-LOSS ANALYSIS UPDATE (Nov 2025):
        Added liquidity scoring (25% weight) after -$26,930 loss.
        New weights: POP 30%, Liquidity 25%, VRP 20%, R/R 15%, Greeks 10%

        KELLY EDGE FIX (Dec 2025):
        Replaced R/R scoring with Kelly edge scoring to prevent negative EV trades
        from outscoring positive EV trades. Kelly edge = (p × b) - q where:
        - p = probability of profit
        - b = reward/risk ratio
        - q = 1 - p
        Negative edge trades score 0 points for edge component.

        PROFIT ZONE FIX (Dec 2025):
        Added profit zone vs implied move penalty. Narrow profit zones (Iron Butterfly,
        tight Iron Condor) get penalized when implied move is large, preventing
        recommending strategies that are unlikely to stay profitable.

        Returns:
            Tuple of (overall_score, profitability_score, risk_score)
        """
        # Factor 1: Probability of Profit - Compare against target
        pop_score = min(
            strategy.probability_of_profit / self.weights.target_pop, 1.0
        ) * self.weights.pop_weight

        # Factor 2: Liquidity Quality - NEW (POST-LOSS ANALYSIS)
        liquidity_score = self._calculate_liquidity_score(strategy)

        # Factor 3: VRP Edge - Compare against target
        vrp_score = min(
            vrp.vrp_ratio / self.weights.target_vrp, 1.0
        ) * self.weights.vrp_weight

        # Factor 4: Kelly Edge - Replaces raw R/R scoring (KELLY EDGE FIX Dec 2025)
        # Kelly edge = (p × b) - q combines POP and R/R into expected value
        # This prevents negative EV trades from scoring high due to R/R alone
        edge_score = self._calculate_kelly_edge_score(
            strategy.probability_of_profit,
            strategy.reward_risk_ratio
        )

        # Factor 5: Greeks Quality - Theta and Vega
        greeks_score = self._calculate_greeks_score(strategy)

        # Factor 6: Position Sizing - Graduated scoring up to 10 contracts
        size_score = min(strategy.contracts / 10.0, 1.0) * self.weights.size_weight

        # Calculate base score
        base_score = pop_score + liquidity_score + vrp_score + edge_score + greeks_score + size_score

        # PROFIT ZONE FIX: Apply penalty for narrow profit zones vs implied move
        # This prevents Iron Butterflies from scoring high when implied move is large
        profit_zone_multiplier = self._calculate_profit_zone_multiplier(strategy, vrp)
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
        Added liquidity scoring. Redistributes greeks weight to other factors proportionally.
        Liquidity weight is HIGHER (30% vs 25%) when Greeks unavailable since we have
        less visibility into option pricing quality.

        KELLY EDGE FIX (Dec 2025):
        Replaced R/R scoring with Kelly edge scoring (same as with Greeks version).

        Returns:
            Tuple of (overall_score, profitability_score, risk_score)
        """
        # Redistribute greeks weight to other factors
        # NOTE: liquidity_weight is NOT scaled because it's always important
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

        # Factor 2: Liquidity Quality - NEW (POST-LOSS ANALYSIS)
        # Not scaled - always use full weight since liquidity is critical
        liquidity_score = self._calculate_liquidity_score(strategy)

        # Factor 3: VRP Edge - Scaled
        vrp_score = min(
            vrp.vrp_ratio / self.weights.target_vrp, 1.0
        ) * self.weights.vrp_weight * scale_factor

        # Factor 4: Kelly Edge - Scaled (KELLY EDGE FIX Dec 2025)
        edge_score = self._calculate_kelly_edge_score(
            strategy.probability_of_profit,
            strategy.reward_risk_ratio
        ) * scale_factor

        # Factor 5: Position Sizing - Scaled
        size_score = min(strategy.contracts / 10.0, 1.0) * self.weights.size_weight * scale_factor

        # Calculate base score
        base_score = pop_score + liquidity_score + vrp_score + edge_score + size_score

        # PROFIT ZONE FIX: Apply penalty for narrow profit zones vs implied move
        profit_zone_multiplier = self._calculate_profit_zone_multiplier(strategy, vrp)
        overall = base_score * profit_zone_multiplier

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

    def _calculate_liquidity_score(self, strategy: Strategy) -> float:
        """
        Calculate liquidity quality score (POST-LOSS ANALYSIS - Added Nov 2025).

        After -$26,930 loss from WDAY/ZS/SYM, liquidity became critical.
        Poor liquidity caused:
        - Wide bid-ask spreads on entry/exit
        - Slippage amplified losses by ~20%
        - Expensive fills when trying to close positions

        Scoring logic (3-tier system):
        - EXCELLENT tier: 100% of liquidity_weight (25 points max)
        - WARNING tier: 50% of liquidity_weight (12.5 points max)
        - REJECT tier: 0% (should be filtered before scoring)
        - No tier info: 100% (assume EXCELLENT for backward compatibility)

        Args:
            strategy: Strategy with optional liquidity metrics

        Returns:
            Liquidity score (0-25)
        """
        # If no liquidity tier info, assume EXCELLENT (backward compatibility)
        if strategy.liquidity_tier is None:
            return self.weights.liquidity_weight

        tier = strategy.liquidity_tier.upper()

        if tier == "EXCELLENT":
            # Full score for excellent liquidity
            return self.weights.liquidity_weight
        elif tier == "WARNING":
            # Half score for warning liquidity (risky but tradeable)
            return self.weights.liquidity_weight * 0.5
        elif tier == "REJECT":
            # Zero score for reject tier (should be filtered before this)
            return 0.0
        else:
            # Unknown tier, assume EXCELLENT
            return self.weights.liquidity_weight

    def _calculate_kelly_edge_score(self, pop: float, rr: float) -> float:
        """
        Calculate Kelly edge score (KELLY EDGE FIX - Added Dec 2025).

        Replaces raw R/R scoring to prevent negative EV trades from outscoring
        positive EV trades. The Kelly edge combines POP and R/R into a single
        metric that represents expected value.

        Kelly Edge Formula:
            edge = (p × b) - q
            where:
                p = probability of profit (POP)
                b = reward/risk ratio (R/R)
                q = 1 - p

        Scoring Logic:
        - Negative edge (EV < 0): 0 points (reject)
        - Zero edge (break-even): 0 points
        - Positive edge: Score proportional to edge
        - Target edge: 0.10 (10%) for full reward_risk_weight points

        Example:
        - IC: 59.5% POP, 0.38 R/R → edge = -17.89% → 0 points
        - BPS: 84.6% POP, 0.21 R/R → edge = +2.37% → 3.6 points

        Args:
            pop: Probability of profit (0.0 to 1.0)
            rr: Reward/risk ratio

        Returns:
            Kelly edge score (0 to reward_risk_weight, typically 15 points max)
        """
        # Calculate Kelly edge
        q = 1.0 - pop
        edge = pop * rr - q

        # Negative edge scores 0 (reject negative EV trades)
        if edge <= 0:
            return 0.0

        # Positive edge: Score proportional to edge
        # Target 10% edge for full points (aggressive but achievable with VRP)
        target_edge = 0.10
        normalized_edge = min(edge / target_edge, 1.0)

        return normalized_edge * self.weights.reward_risk_weight

    def _calculate_profit_zone_multiplier(self, strategy: Strategy, vrp: VRPResult) -> float:
        """
        Calculate profit zone multiplier (PROFIT ZONE FIX - Added Dec 2025).

        Penalizes strategies with narrow profit zones when implied move is large.
        This prevents Iron Butterflies and tight Iron Condors from being recommended
        when the stock is expected to move far beyond their profit range.

        Logic:
        - Calculate profit zone width (distance between breakevens)
        - Compare to implied move percentage
        - Apply penalty multiplier when profit zone < implied move

        Multiplier ranges:
        - 1.0 (no penalty): Profit zone >= implied move
        - 0.9-1.0: Profit zone is 70-100% of implied move (slight penalty)
        - 0.7-0.9: Profit zone is 40-70% of implied move (moderate penalty)
        - 0.5-0.7: Profit zone is 20-40% of implied move (heavy penalty)
        - 0.3: Profit zone < 20% of implied move (severe penalty)

        Args:
            strategy: Strategy to evaluate
            vrp: VRP analysis containing implied move

        Returns:
            Multiplier between 0.3 and 1.0 to apply to overall score
        """
        # If no breakevens, no penalty (shouldn't happen, but be safe)
        if not strategy.breakeven or len(strategy.breakeven) == 0:
            return 1.0

        # For strategies with two breakevens (IC, IB), calculate width between them
        if len(strategy.breakeven) >= 2:
            # Sort breakevens to get lower and upper
            breakevens_sorted = sorted([float(be.amount) for be in strategy.breakeven])
            lower_be = breakevens_sorted[0]
            upper_be = breakevens_sorted[-1]
            profit_zone_width = upper_be - lower_be

            # Get stock price from VRP ticker (we need to estimate it)
            # Use middle of breakevens as proxy for stock price
            stock_price_estimate = (lower_be + upper_be) / 2.0

        else:
            # Single breakeven (credit spread)
            # Use distance from breakeven as proxy for safe zone
            # This is more lenient - credit spreads have one-sided risk
            breakeven = float(strategy.breakeven[0].amount)

            # We can't determine stock price from breakeven alone
            # Use implied move percentage to estimate
            # For now, give credit spreads full score (they handle large moves better)
            return 1.0

        # Calculate profit zone as percentage of stock price
        profit_zone_pct = (profit_zone_width / stock_price_estimate) * 100

        # Get implied move percentage
        implied_move_pct = vrp.implied_move_pct

        # Calculate ratio: profit zone / implied move
        # If ratio > 1.0, profit zone is wider than implied move (good)
        # If ratio < 1.0, profit zone is narrower than implied move (bad)
        zone_to_move_ratio = profit_zone_pct / implied_move_pct

        # Apply penalty based on ratio
        if zone_to_move_ratio >= 1.0:
            # Profit zone covers full implied move - no penalty
            return 1.0
        elif zone_to_move_ratio >= 0.70:
            # Profit zone is 70-100% of implied move - slight penalty
            # Linear interpolation: 0.70 → 0.9, 1.0 → 1.0
            return 0.9 + (zone_to_move_ratio - 0.70) * (0.1 / 0.30)
        elif zone_to_move_ratio >= 0.40:
            # Profit zone is 40-70% of implied move - moderate penalty
            # Linear interpolation: 0.40 → 0.7, 0.70 → 0.9
            return 0.7 + (zone_to_move_ratio - 0.40) * (0.2 / 0.30)
        elif zone_to_move_ratio >= 0.20:
            # Profit zone is 20-40% of implied move - heavy penalty
            # Linear interpolation: 0.20 → 0.5, 0.40 → 0.7
            return 0.5 + (zone_to_move_ratio - 0.20) * (0.2 / 0.20)
        else:
            # Profit zone < 20% of implied move - severe penalty
            return 0.3

    def _generate_strategy_rationale(self, strategy: Strategy, vrp: VRPResult) -> str:
        """
        Generate brief rationale for strategy.

        POST-LOSS ANALYSIS UPDATE (Nov 2025):
        Added liquidity warnings to rationale to make liquidity issues visible.

        Args:
            strategy: Strategy to generate rationale for
            vrp: VRP analysis

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
                parts.append("❌ VERY LOW LIQUIDITY")
            elif tier == "EXCELLENT":
                parts.append("✓ High liquidity")

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
