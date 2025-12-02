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
        """
        Initialize scorer with weights.

        Args:
            weights: Scoring weights configuration. If None, uses defaults.
        """
        self.weights = weights if weights is not None else ScoringWeights()

    def score_strategy(
        self,
        strategy: Strategy,
        vrp: VRPResult,
        directional_bias: DirectionalBias | None = None
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
        # Check if Greeks are available
        has_greeks = strategy.position_theta is not None and strategy.position_vega is not None

        if has_greeks:
            overall, profitability, risk = self._score_with_greeks(strategy, vrp)
        else:
            overall, profitability, risk = self._score_without_greeks(strategy, vrp)

        # Apply directional alignment bonus/penalty
        if directional_bias is not None:
            overall = self._apply_directional_alignment(
                overall, strategy, directional_bias
            )

        # Generate rationale
        rationale = self._generate_strategy_rationale(strategy, vrp)

        return ScoringResult(
            overall_score=overall,
            profitability_score=profitability,
            risk_score=risk,
            strategy_rationale=rationale
        )

    def score_strategies(
        self,
        strategies: List[Strategy],
        vrp: VRPResult,
        directional_bias: DirectionalBias | None = None
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
        Added liquidity scoring. When Greeks unavailable, use config-defined no-Greeks
        weights which redistribute the 8% Greeks weight to other factors.

        KELLY EDGE FIX (Dec 2025):
        Replaced R/R scoring with Kelly edge scoring (same as with Greeks version).

        BUG FIX (Dec 2025):
        Fixed scoring inflation bug. Previous code used broken scaling that caused
        scores to exceed 100% by 22 points. Now uses config-defined no-Greeks weights
        that properly sum to 100%.

        Returns:
            Tuple of (overall_score, profitability_score, risk_score)
        """
        # Use config-defined no-Greeks weights (sum to 100%)
        # These weights redistribute the 8% Greeks weight to other factors:
        # POP: 40% → 45% (+5%)
        # Liquidity: 22% → 26% (+4%) - Higher when we have less pricing visibility
        # VRP: 17% → 17% (unchanged)
        # Edge: 13% → 12% (-1%) - Slight reduction due to rounding
        # Size: 0% → 0% (position sizing handled separately)

        # Factor 1: Probability of Profit
        pop_score = min(
            strategy.probability_of_profit / self.weights.target_pop, 1.0
        ) * self.weights.pop_weight_no_greeks

        # Factor 2: Liquidity Quality - CRITICAL when Greeks unavailable
        # Use liquidity tier for scoring (EXCELLENT=full, WARNING=half, REJECT=zero)
        if strategy.liquidity_tier is None:
            liquidity_score = self.weights.liquidity_weight_no_greeks
        else:
            tier = strategy.liquidity_tier.upper()
            if tier == "EXCELLENT":
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

        # Factor 4: Kelly Edge (KELLY EDGE FIX Dec 2025)
        # Use refactored method to eliminate code duplication
        edge_score = self._calculate_kelly_edge_score(
            strategy.probability_of_profit,
            strategy.reward_risk_ratio,
            weight=self.weights.reward_risk_weight_no_greeks
        )

        # Factor 5: Position Sizing
        size_score = min(strategy.contracts / 10.0, 1.0) * self.weights.size_weight_no_greeks

        # Calculate base score (should sum to ~100 max)
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

    def _calculate_kelly_edge_score(
        self, pop: float, rr: float, weight: Optional[float] = None
    ) -> float:
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
        - Target edge: 0.10 (10%) for full weight points

        Example:
        - IC: 59.5% POP, 0.38 R/R → edge = -17.89% → 0 points
        - BPS: 84.6% POP, 0.21 R/R → edge = +2.37% → 3.08 points (fixed)

        Args:
            pop: Probability of profit (0.0 to 1.0)
            rr: Reward/risk ratio
            weight: Optional weight to use (defaults to reward_risk_weight)

        Returns:
            Kelly edge score (0 to weight points)
        """
        # Use provided weight or default to standard reward_risk_weight
        if weight is None:
            weight = self.weights.reward_risk_weight

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

        return normalized_edge * weight

    def _apply_directional_alignment(
        self,
        base_score: float,
        strategy: Strategy,
        bias: DirectionalBias
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
        - Neutral: Iron Condor, Iron Butterfly (range-bound)

        Args:
            base_score: Score before directional adjustment
            strategy: Strategy to evaluate
            bias: Directional bias from skew analysis

        Returns:
            Adjusted score with directional alignment applied
        """
        # Classify strategy direction
        strategy_type = strategy.strategy_type

        is_bullish_strategy = strategy_type == StrategyType.BULL_PUT_SPREAD
        is_bearish_strategy = strategy_type == StrategyType.BEAR_CALL_SPREAD
        is_neutral_strategy = strategy_type in {
            StrategyType.IRON_CONDOR,
            StrategyType.IRON_BUTTERFLY
        }

        # Classify bias strength
        is_strong_bearish = bias == DirectionalBias.STRONG_BEARISH
        is_bearish = bias == DirectionalBias.BEARISH
        is_weak_bearish = bias == DirectionalBias.WEAK_BEARISH

        is_strong_bullish = bias == DirectionalBias.STRONG_BULLISH
        is_bullish = bias == DirectionalBias.BULLISH
        is_weak_bullish = bias == DirectionalBias.WEAK_BULLISH

        is_neutral = bias == DirectionalBias.NEUTRAL

        # Calculate alignment bonus/penalty
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

        # NEUTRAL BIAS
        # No adjustments - all strategies treated equally

        adjusted_score = base_score + adjustment

        # Ensure score stays in valid range
        return max(0.0, min(100.0, adjusted_score))

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

            # Use actual stock price from strategy (FIX: was using breakeven midpoint)
            stock_price_estimate = float(strategy.stock_price.amount)

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

        # Get implied move percentage (one-sided: up OR down)
        implied_move_pct = vrp.implied_move_pct.value

        # CRITICAL FIX: Implied move is one-sided, but stock can move ±X%
        # Total expected range = 2 × implied_move_pct (e.g., ±15% = 30% total range)
        total_expected_range_pct = 2 * implied_move_pct

        # Calculate ratio: profit zone / total expected range
        # If ratio > 1.0, profit zone is wider than expected range (good)
        # If ratio < 1.0, profit zone is narrower than expected range (bad)
        zone_to_move_ratio = profit_zone_pct / total_expected_range_pct

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
