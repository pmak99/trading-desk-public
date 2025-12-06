"""Ticker analyzer service for IV Crush analysis."""

import logging
from datetime import date, datetime
from typing import Optional

from src.domain.types import TickerAnalysis, ImpliedMove, VRPResult
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode
from src.domain.enums import EarningsTiming, Recommendation

logger = logging.getLogger(__name__)


class TickerAnalyzer:
    """Service for analyzing ticker IV Crush opportunities.

    Orchestrates the full analysis flow:
    1. Calculate implied move from options chain
    2. Fetch historical moves from database
    3. Calculate VRP ratio and recommendation

    Args:
        container: Dependency injection container
    """

    def __init__(self, container):
        self.container = container

    def analyze(
        self,
        ticker: str,
        earnings_date: date,
        expiration: date,
        generate_strategies: bool = False
    ) -> Result[TickerAnalysis, AppError]:
        """Analyze a ticker for IV Crush opportunity.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Date of earnings announcement
            expiration: Option expiration date (will be adjusted to nearest available)
            generate_strategies: If True, generate trade strategies (bull put, bear call, iron condor)

        Returns:
            Result containing TickerAnalysis or error
        """
        try:
            # Step 0: Find nearest available expiration if exact date not available
            nearest_exp_result = self.container.tradier.find_nearest_expiration(
                ticker, expiration
            )
            if nearest_exp_result.is_err:
                return Err(nearest_exp_result.error)

            actual_expiration = nearest_exp_result.value
            if actual_expiration != expiration:
                logger.info(
                    f"{ticker}: Using adjusted expiration {actual_expiration} "
                    f"(requested {expiration})"
                )

            # Step 1: Calculate implied move
            implied_result = self.container.implied_move_calculator.calculate(
                ticker, actual_expiration
            )

            if implied_result.is_err:
                return Err(implied_result.error)

            implied_move = implied_result.value

            # Step 2: Get historical moves
            hist_result = self.container.prices_repository.get_historical_moves(
                ticker, limit=12
            )

            if hist_result.is_err:
                return Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"No historical data for {ticker}",
                    )
                )

            historical_moves = hist_result.value

            if len(historical_moves) < 3:
                return Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"Insufficient historical data for {ticker} (need 3+, got {len(historical_moves)})",
                    )
                )

            # Step 3: Calculate VRP
            vrp_result = self.container.vrp_calculator.calculate(
                ticker=ticker,
                expiration=actual_expiration,
                implied_move=implied_move,
                historical_moves=historical_moves,
            )

            if vrp_result.is_err:
                return Err(vrp_result.error)

            vrp = vrp_result.value

            # Step 3.5: Apply adaptive thresholds based on VIX regime
            vrp = self._apply_adaptive_thresholds(ticker, vrp)

            # Step 4: Optionally calculate enhanced skew (Phase 4)
            skew = None
            if self.container.skew_analyzer:
                skew_result = self.container.skew_analyzer.analyze_skew_curve(
                    ticker, actual_expiration
                )
                if skew_result.is_ok:
                    skew = skew_result.value
                    logger.debug(f"{ticker}: Skew analysis complete")
                else:
                    logger.warning(f"{ticker}: Skew analysis failed: {skew_result.error}")

            # Step 5: Optionally calculate enhanced consistency (Phase 4)
            consistency = None
            if self.container.consistency_analyzer and len(historical_moves) >= 4:
                consistency_result = self.container.consistency_analyzer.analyze_consistency(
                    ticker, historical_moves
                )
                if consistency_result.is_ok:
                    consistency = consistency_result.value
                    logger.debug(f"{ticker}: Consistency analysis complete")
                else:
                    logger.warning(f"{ticker}: Consistency analysis failed: {consistency_result.error}")

            # Step 6: Optionally generate trade strategies
            strategies = None
            if generate_strategies and vrp.is_tradeable:
                try:
                    # Get the full option chain for strategy generation
                    chain_result = self.container.cached_options_provider.get_option_chain(
                        ticker, actual_expiration
                    )
                    if chain_result.is_ok:
                        option_chain = chain_result.value
                        strategies = self.container.strategy_generator.generate_strategies(
                            ticker=ticker,
                            option_chain=option_chain,
                            vrp=vrp,
                            skew=skew,
                        )
                        logger.info(f"{ticker}: Generated {len(strategies.strategies)} strategies")
                    else:
                        logger.warning(f"{ticker}: Could not fetch option chain for strategies: {chain_result.error}")
                except Exception as e:
                    logger.warning(f"{ticker}: Strategy generation failed: {e}")

            # Build complete analysis
            analysis = TickerAnalysis(
                ticker=ticker,
                earnings_date=earnings_date,
                earnings_timing=EarningsTiming.AMC,  # Default to After Market Close
                entry_time=datetime.now(),
                expiration=actual_expiration,  # Use adjusted expiration
                implied_move=implied_move,
                vrp=vrp,
                consistency=consistency,  # Phase 4 enhanced
                skew=skew,  # Phase 4 enhanced
                term_structure=None,  # Future enhancement
                strategies=strategies,  # Strategy recommendations
            )

            return Ok(analysis)

        except Exception as e:
            logger.error(f"Error analyzing {ticker}: {e}", exc_info=True)
            return Err(
                AppError(ErrorCode.CALCULATION, f"Analysis failed: {str(e)}")
            )

    def _apply_adaptive_thresholds(self, ticker: str, vrp: VRPResult) -> VRPResult:
        """Apply adaptive thresholds based on current VIX regime.

        In elevated volatility environments, we require higher VRP ratios
        to maintain edge. This may downgrade recommendations from the base
        VRP calculation.

        Args:
            ticker: Stock ticker (for logging)
            vrp: Original VRP result with base recommendation

        Returns:
            VRPResult with potentially adjusted recommendation
        """
        try:
            # Get current market conditions
            market_conditions_result = self.container.market_conditions_analyzer.get_current_conditions()

            if market_conditions_result.is_err:
                logger.debug(
                    f"{ticker}: Could not fetch market conditions for adaptive thresholds, "
                    f"using base recommendation: {vrp.recommendation.value}"
                )
                return vrp

            market_conditions = market_conditions_result.value

            # Get adaptive thresholds based on VIX regime
            adapted = self.container.adaptive_threshold_calculator.calculate(market_conditions)

            # Check if trading is not recommended in current regime
            if not adapted.trade_recommended:
                logger.warning(
                    f"{ticker}: Trading not recommended in {adapted.regime} regime "
                    f"(VIX={adapted.vix_level:.1f}). Overriding to SKIP."
                )
                return VRPResult(
                    ticker=vrp.ticker,
                    expiration=vrp.expiration,
                    implied_move_pct=vrp.implied_move_pct,
                    historical_mean_move_pct=vrp.historical_mean_move_pct,
                    vrp_ratio=vrp.vrp_ratio,
                    edge_score=vrp.edge_score,
                    recommendation=Recommendation.SKIP,
                )

            # Re-evaluate recommendation using adapted thresholds
            if vrp.vrp_ratio >= adapted.vrp_excellent:
                new_recommendation = Recommendation.EXCELLENT
            elif vrp.vrp_ratio >= adapted.vrp_good:
                new_recommendation = Recommendation.GOOD
            elif vrp.vrp_ratio >= adapted.vrp_marginal:
                new_recommendation = Recommendation.MARGINAL
            else:
                new_recommendation = Recommendation.SKIP

            # Log if recommendation changed
            if new_recommendation != vrp.recommendation:
                logger.info(
                    f"{ticker}: Adaptive thresholds adjusted recommendation from "
                    f"{vrp.recommendation.value} → {new_recommendation.value} "
                    f"(VIX regime: {adapted.regime}, factor: {adapted.adjustment_factor:.1f}x)"
                )
                return VRPResult(
                    ticker=vrp.ticker,
                    expiration=vrp.expiration,
                    implied_move_pct=vrp.implied_move_pct,
                    historical_mean_move_pct=vrp.historical_mean_move_pct,
                    vrp_ratio=vrp.vrp_ratio,
                    edge_score=vrp.edge_score,
                    recommendation=new_recommendation,
                )

            # Log that adaptive thresholds were applied but no change needed
            if adapted.is_adjusted:
                logger.debug(
                    f"{ticker}: Adaptive thresholds applied (factor: {adapted.adjustment_factor:.1f}x) "
                    f"but recommendation unchanged: {vrp.recommendation.value}"
                )

            return vrp

        except Exception as e:
            logger.warning(
                f"{ticker}: Error applying adaptive thresholds: {e}. "
                f"Using base recommendation: {vrp.recommendation.value}"
            )
            return vrp
