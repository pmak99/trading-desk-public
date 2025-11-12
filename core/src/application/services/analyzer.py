"""Ticker analyzer service for IV Crush analysis."""

import logging
from datetime import date, datetime
from typing import Optional

from src.domain.types import TickerAnalysis, ImpliedMove, VRPResult
from src.domain.errors import Result, AppError
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
        self, ticker: str, earnings_date: date, expiration: date
    ) -> Result[TickerAnalysis, AppError]:
        """Analyze a ticker for IV Crush opportunity.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Date of earnings announcement
            expiration: Option expiration date

        Returns:
            Result containing TickerAnalysis or error
        """
        try:
            # Step 1: Calculate implied move
            implied_result = self.container.implied_move_calculator.calculate(
                ticker, expiration
            )

            if implied_result.is_err:
                return Result.err(implied_result.error)

            implied_move = implied_result.value

            # Step 2: Get historical moves
            hist_result = self.container.prices_repository.get_historical_moves(
                ticker, limit=12
            )

            if hist_result.is_err:
                return Result.err(
                    AppError(
                        message=f"No historical data for {ticker}",
                        error_type="DATA_NOT_FOUND",
                    )
                )

            historical_moves = hist_result.value

            if len(historical_moves) < 3:
                return Result.err(
                    AppError(
                        message=f"Insufficient historical data for {ticker} (need 3+, got {len(historical_moves)})",
                        error_type="INSUFFICIENT_DATA",
                    )
                )

            # Step 3: Calculate VRP
            vrp_result = self.container.vrp_calculator.calculate(
                ticker=ticker,
                expiration=expiration,
                implied_move=implied_move,
                historical_moves=historical_moves,
            )

            if vrp_result.is_err:
                return Result.err(vrp_result.error)

            vrp = vrp_result.value

            # Build complete analysis
            analysis = TickerAnalysis(
                ticker=ticker,
                earnings_date=earnings_date,
                earnings_timing=EarningsTiming.AFTER_CLOSE,  # Default, can enhance later
                entry_time=datetime.now(),
                expiration=expiration,
                implied_move=implied_move,
                vrp=vrp,
                consistency=None,  # Phase 2 enhancement
                skew=None,  # Phase 2 enhancement
                term_structure=None,  # Phase 2 enhancement
            )

            return Result.ok(analysis)

        except Exception as e:
            logger.error(f"Error analyzing {ticker}: {e}", exc_info=True)
            return Result.err(
                AppError(message=f"Analysis failed: {str(e)}", error_type="ANALYSIS_ERROR")
            )
