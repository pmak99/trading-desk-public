"""StrategyGenerator — thin coordinator that delegates to focused strategy modules."""
import logging
from dataclasses import replace
from typing import Optional

from common.timezone import now_et
from src.application.metrics.liquidity_scorer import LiquidityScorer
from src.application.services.strategy.bias import determine_bias
from src.application.services.strategy.metrics import calculate_strategy_liquidity
from src.application.services.strategy.sizing import (
    calculate_contracts,
    calculate_contracts_kelly,
    effective_cap,
)
from src.application.services.strategy.spread_builder import build_vertical_spread
from src.config.config import StrategyConfig
from src.domain.enums import OptionType, StrategyType
from src.domain.scoring import StrategyScorer
from src.domain.types import (
    OptionChain,
    SizingContext,
    SkewResult,
    StrategyRecommendation,
    VRPResult,
)

logger = logging.getLogger(__name__)

# Built and scored for every ticker, whatever the directional bias or VRP tier;
# the score picks (CLAUDE.md §6: skew is hedge demand, not conviction). Until
# 2026-09-14 a STRONG bias built only its own side at every tier, a MODERATE one
# under GOOD and MARGINAL, and TRR HIGH kept the bias side.
OFFERED_SPREADS = (StrategyType.BULL_PUT_SPREAD, StrategyType.BEAR_CALL_SPREAD)


class StrategyGenerator:
    """
    Quantitative options strategy generator.

    Generates credit spreads (bull put, bear call) based on VRP analysis
    and market conditions. Iron condors and iron butterflies are permanently
    banned (CLAUDE.md) and have no StrategyType, so nothing can ask for one.
    """

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
            skew: Optional skew analysis; its bias is displayed, never used to choose
            tail_risk_level: TRR level ('HIGH', 'NORMAL', 'LOW') — HIGH keeps the top-scored spread
            sizing_context: Optional contract cap signals

        Returns:
            StrategyRecommendation with 2-3 ranked strategies
        """
        logger.info(f"{ticker}: Generating strategies (VRP: {vrp.vrp_ratio:.2f}x)...")

        bias = determine_bias(skew)
        logger.debug(f"{ticker}: Directional bias = {bias.value}")

        strategies = []
        for strategy_type in OFFERED_SPREADS:
            try:
                strategy = self._build_strategy(
                    ticker, strategy_type, option_chain, vrp, sizing_context
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
                            ticker, strategy_type, option_chain, vrp, reduced_context
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

        self.scorer.score_strategies(strategies, vrp)
        # A REJECT spread is not traded as a spread (CLAUDE.md §2), so it never
        # outranks one that can be: tradeable first, then by score. Until
        # 2026-09-16 the score alone ordered them, and a REJECT spread could be
        # the recommendation at full size.
        strategies.sort(key=lambda s: (not s.liquidity_rejected, s.overall_score), reverse=True)

        # Written before the TRR HIGH cut, so a single pick still says what it
        # beat and that it carries no directional edge. Until 2026-09-14 the cut
        # came first and TRR HIGH picks never carried that disclosure.
        recommended_idx = 0
        rationale = self.scorer.generate_recommendation_rationale(strategies, vrp)

        # HIGH TRR offers one spread, not both sides of the event (May 2026: "tail
        # risk makes hedging unreliable"). It is the first in the order above, the
        # top-scored tradeable one; until 2026-09-14 a MODERATE or STRONG bias chose it.
        if tail_risk_level == 'HIGH' and len(strategies) > 1:
            strategies = strategies[:1]

        return StrategyRecommendation(
            ticker=ticker,
            expiration=option_chain.expiration,
            analysis_time=now_et(),
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

    def _determine_bias(self, skew):
        return determine_bias(skew)

    def _build_strategy(
        self,
        ticker: str,
        strategy_type: StrategyType,
        option_chain: OptionChain,
        vrp: VRPResult,
        sizing_context: Optional[SizingContext] = None,
    ):
        """Dispatch to the appropriate spread builder."""
        if strategy_type == StrategyType.BULL_PUT_SPREAD:
            return build_vertical_spread(
                self.config, self.liquidity_scorer, ticker, option_chain, vrp,
                OptionType.PUT, StrategyType.BULL_PUT_SPREAD, True, sizing_context,
            )
        elif strategy_type == StrategyType.BEAR_CALL_SPREAD:
            return build_vertical_spread(
                self.config, self.liquidity_scorer, ticker, option_chain, vrp,
                OptionType.CALL, StrategyType.BEAR_CALL_SPREAD, False, sizing_context,
            )
        else:
            # CALENDAR_SPREAD is built by services/calendar_spread.py, never here.
            logger.warning(f"{ticker}: Strategy type {strategy_type} not implemented")
            return None
