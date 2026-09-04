"""StrategyGenerator — thin coordinator that delegates to focused strategy modules."""
import logging
from dataclasses import replace
from datetime import datetime
from typing import Optional

from src.domain.types import (
    OptionChain, VRPResult, SkewResult, SizingContext, StrategyRecommendation
)
from src.domain.enums import StrategyType, DirectionalBias, OptionType
from src.config.config import StrategyConfig
from src.domain.scoring import StrategyScorer
from src.application.metrics.liquidity_scorer import LiquidityScorer
from src.application.services.strategy.bias import determine_bias, select_strategy_types
from src.application.services.strategy.spread_builder import build_vertical_spread
from src.application.services.strategy.metrics import calculate_strategy_liquidity
from src.application.services.strategy.sizing import calculate_contracts_kelly, calculate_contracts, effective_cap
from src.application.services.strategy.strike_selection import get_asymmetric_deltas, MIN_DELTA, MAX_DELTA

logger = logging.getLogger(__name__)


class StrategyGenerator:
    """
    Quantitative options strategy generator.

    Generates credit spreads (bull put, bear call) based on VRP analysis
    and market conditions. Iron condors and iron butterflies are permanently
    banned (backtesting showed catastrophic loss asymmetry) — _build_strategy
    refuses them.

    Note: _select_strategy_types branches on hardcoded VRP 2.0/1.5 to decide
    how many directional alternatives to offer; this is intentionally separate
    from the configured recommendation tiers (1.8/1.4/1.2).
    """

    # Class-level constants (preserved from monolith for backward compat)
    MIN_DELTA = MIN_DELTA
    MAX_DELTA = MAX_DELTA

    def __init__(self, config: StrategyConfig, liquidity_scorer: LiquidityScorer):
        self.config = config
        self.scorer = StrategyScorer(config.scoring_weights)
        self.liquidity_scorer = liquidity_scorer

    def generate_strategies(
        self,
        ticker: str,
        option_chain: OptionChain,
        vrp: VRPResult,
        skew: Optional[SkewResult] = None,
        tail_risk_level: Optional[str] = None,
        sizing_context: Optional[SizingContext] = None,
    ) -> StrategyRecommendation:
        """
        Generate 2-3 ranked strategy recommendations.

        Args:
            ticker: Ticker symbol
            option_chain: Complete options chain with greeks
            vrp: VRP analysis result
            skew: Optional skew analysis for directional bias
            tail_risk_level: TRR level ('HIGH', 'NORMAL', 'LOW') — HIGH restricts to 1 spread
            sizing_context: Optional contract cap signals

        Returns:
            StrategyRecommendation with 2-3 ranked strategies
        """
        logger.info(f"{ticker}: Generating strategies (VRP: {vrp.vrp_ratio:.2f}x)...")

        bias = determine_bias(skew)
        logger.debug(f"{ticker}: Directional bias = {bias.value}")

        strategy_types = select_strategy_types(vrp, bias, tail_risk_level)
        logger.debug(f"{ticker}: Strategy types = {[s.value for s in strategy_types]}")

        strategies = []
        for strategy_type in strategy_types:
            try:
                strategy = self._build_strategy(
                    ticker, strategy_type, option_chain, vrp, bias, sizing_context
                )
                if strategy:
                    liquidity_metrics = calculate_strategy_liquidity(
                        self.liquidity_scorer, ticker, option_chain, strategy.legs
                    )

                    # Rule 4 (CLAUDE.md sizing): MARGINAL VRP or WARNING liquidity
                    # -> 50% size reduction. VRP is known up front and already
                    # baked into sizing_context before the first build. Liquidity
                    # tier is only known now, after strikes are chosen -- if it
                    # fires WARNING and VRP didn't already trigger the same
                    # (single, non-stacking) reduction, rebuild with it applied
                    # so contracts and every dollar total derived from them
                    # (max_profit/max_loss/capital_required/commission/Greeks)
                    # stay internally consistent, rather than patching .contracts
                    # alone and leaving those stale. Fixed 2026-07-27.
                    if (
                        liquidity_metrics['liquidity_tier'] == 'WARNING'
                        and sizing_context is not None
                        and not sizing_context.vrp_marginal
                    ):
                        reduced_context = replace(sizing_context, liquidity_warning=True)
                        rebuilt = self._build_strategy(
                            ticker, strategy_type, option_chain, vrp, bias, reduced_context
                        )
                        if rebuilt:
                            strategy = rebuilt

                    strategy.liquidity_tier = liquidity_metrics['liquidity_tier']
                    strategy.min_open_interest = liquidity_metrics['min_open_interest']
                    strategy.max_spread_pct = liquidity_metrics['max_spread_pct']
                    strategies.append(strategy)
            except Exception as e:  # noqa: BLE001 — top-level safety net
                logger.warning(f"{ticker}: Failed to build {strategy_type.value}: {e}")

        if not strategies:
            raise ValueError(f"Could not generate any valid strategies for {ticker}")

        self.scorer.score_strategies(strategies, vrp, bias)
        strategies.sort(key=lambda s: s.overall_score, reverse=True)

        recommended_idx = 0
        rationale = self.scorer.generate_recommendation_rationale(strategies[0], vrp, bias)

        return StrategyRecommendation(
            ticker=ticker,
            expiration=option_chain.expiration,
            analysis_time=datetime.now(),
            stock_price=option_chain.stock_price,
            implied_move_pct=vrp.implied_move_pct,
            vrp_ratio=vrp.vrp_ratio,
            directional_bias=bias,
            strategies=strategies[:3],
            recommended_index=recommended_idx,
            recommendation_rationale=rationale,
        )

    # --- Backward-compat shims (old tests called these as instance methods) ---

    def _calculate_contracts_kelly(self, max_profit, max_loss, probability_of_profit, sizing_context=None):
        return calculate_contracts_kelly(self.config, max_profit, max_loss, probability_of_profit, sizing_context)

    def _calculate_contracts(self, max_loss_per_spread, sizing_context=None):
        return calculate_contracts(self.config, max_loss_per_spread, sizing_context)

    def _effective_cap(self, sizing_context=None):
        return effective_cap(self.config, sizing_context)

    def _select_strategy_types(self, vrp, bias, tail_risk_level=None):
        return select_strategy_types(vrp, bias, tail_risk_level)

    def _determine_bias(self, skew):
        return determine_bias(skew)

    def _get_asymmetric_deltas(self, option_type, bias):
        return get_asymmetric_deltas(self.config, option_type, bias)

    def _build_strategy(
        self,
        ticker: str,
        strategy_type: StrategyType,
        option_chain: OptionChain,
        vrp: VRPResult,
        bias: DirectionalBias,
        sizing_context: Optional[SizingContext] = None,
    ):
        """Dispatch to the appropriate spread builder."""
        if strategy_type == StrategyType.BULL_PUT_SPREAD:
            return build_vertical_spread(
                self.config, self.liquidity_scorer, ticker, option_chain, vrp,
                OptionType.PUT, StrategyType.BULL_PUT_SPREAD, True, bias, sizing_context,
            )
        elif strategy_type == StrategyType.BEAR_CALL_SPREAD:
            return build_vertical_spread(
                self.config, self.liquidity_scorer, ticker, option_chain, vrp,
                OptionType.CALL, StrategyType.BEAR_CALL_SPREAD, False, bias, sizing_context,
            )
        else:
            # IRON_CONDOR and IRON_BUTTERFLY are permanently banned (backtested catastrophic loss asymmetry)
            logger.warning(f"{ticker}: Strategy type {strategy_type} not implemented")
            return None
