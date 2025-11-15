"""Ticker analyzer service for IV Crush analysis."""

import logging
from datetime import date, datetime
from typing import Optional

from src.domain.types import TickerAnalysis, ImpliedMove, VRPResult
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode
from src.domain.enums import EarningsTiming

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
