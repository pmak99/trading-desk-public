"""
VRP (Volatility Risk Premium) Calculator - Tier 1 Core Metric

Compares implied move to historical mean move to identify edge.
This is the core signal for the IV Crush strategy.

Metric Selection:
==================
The VRP calculation compares implied move (from ATM straddle) to historical moves.
Three historical move metrics are available:

1. close_move_pct: Earnings close vs previous close (RECOMMENDED)
   - Matches ATM straddle expectation (close-to-close movement)
   - Most consistent with how straddle is priced

2. intraday_move_pct: High-low range on earnings day
   - Captures intraday volatility
   - May be higher than close-to-close move

3. gap_move_pct: Earnings open vs previous close
   - Gap move only, ignores intraday action
   - Useful for overnight-only strategies

Default: close_move_pct for apples-to-apples comparison with implied move.
"""

import logging
import numpy as np
from datetime import date
from typing import List
from src.domain.types import (
    Percentage,
    VRPResult,
    HistoricalMove,
    ImpliedMove,
)
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode
from src.domain.enums import Recommendation

logger = logging.getLogger(__name__)


class VRPCalculator:
    """
    Calculate VRP ratio and generate trading recommendation.

    VRP Ratio = Implied Move / Historical Mean Move

    Thresholds (data-driven from 289-ticker grid scan):
        - Excellent: >= 7.0x (top 33rd percentile, exceptional edge)
        - Good: >= 4.0x (top 67th percentile, strong edge)
        - Marginal: >= 1.5x (baseline edge)
        - Skip: < 1.5x (insufficient edge)

    Edge Score: Risk-adjusted metric using consistency
        edge_score = vrp_ratio / (1 + consistency)
        Higher consistency (lower MAD) = higher edge score
    """

    def __init__(
        self,
        threshold_excellent: float = 7.0,
        threshold_good: float = 4.0,
        threshold_marginal: float = 1.5,
        min_quarters: int = 4,
        move_metric: str = "close",  # "close", "intraday", or "gap"
    ):
        self.thresholds = {
            'excellent': threshold_excellent,
            'good': threshold_good,
            'marginal': threshold_marginal,
        }
        self.min_quarters = min_quarters
        self.move_metric = move_metric

        # Validate metric
        if move_metric not in ["close", "intraday", "gap"]:
            raise ValueError(
                f"Invalid move_metric: {move_metric}. "
                f"Must be 'close', 'intraday', or 'gap'"
            )

    def calculate(
        self,
        ticker: str,
        expiration: date,
        implied_move: ImpliedMove,
        historical_moves: List[HistoricalMove],
    ) -> Result[VRPResult, AppError]:
        """
        Calculate VRP ratio and recommendation.

        Args:
            ticker: Stock symbol
            expiration: Option expiration date
            implied_move: Calculated implied move
            historical_moves: Past earnings moves (min 4 quarters)

        Returns:
            Result with VRPResult or AppError
        """
        logger.info(f"Calculating VRP: {ticker} (metric={self.move_metric})")

        # Validate historical data
        if not historical_moves:
            return Err(
                AppError(
                    ErrorCode.NODATA,
                    f"No historical moves for {ticker}",
                )
            )

        if len(historical_moves) < self.min_quarters:
            return Err(
                AppError(
                    ErrorCode.NODATA,
                    f"Need {self.min_quarters}+ quarters, got {len(historical_moves)}",
                )
            )

        # Extract historical move percentages based on configured metric
        if self.move_metric == "close":
            historical_pcts = [
                float(move.close_move_pct.value) for move in historical_moves
            ]
        elif self.move_metric == "intraday":
            historical_pcts = [
                float(move.intraday_move_pct.value) for move in historical_moves
            ]
        elif self.move_metric == "gap":
            historical_pcts = [
                float(move.gap_move_pct.value) for move in historical_moves
            ]
        else:
            return Err(
                AppError(
                    ErrorCode.CONFIGURATION,
                    f"Invalid move_metric: {self.move_metric}",
                )
            )

        # Calculate mean historical move
        mean_move = np.mean(historical_pcts)

        if mean_move <= 0:
            return Err(
                AppError(
                    ErrorCode.INVALID,
                    f"Invalid mean move: {mean_move:.2f}%",
                )
            )

        # Calculate VRP ratio
        implied_pct = float(implied_move.implied_move_pct.value)
        vrp_ratio = implied_pct / mean_move

        # Calculate consistency (using MAD - Median Absolute Deviation)
        median_move = np.median(historical_pcts)
        mad = np.median(np.abs(np.array(historical_pcts) - median_move))
        consistency_factor = mad / median_move if median_move > 0 else 999

        # Calculate edge score (risk-adjusted VRP)
        # Higher consistency (lower MAD) = higher edge score
        edge_score = vrp_ratio / (1 + consistency_factor)

        # Determine recommendation
        if vrp_ratio >= self.thresholds['excellent']:
            recommendation = Recommendation.EXCELLENT
        elif vrp_ratio >= self.thresholds['good']:
            recommendation = Recommendation.GOOD
        elif vrp_ratio >= self.thresholds['marginal']:
            recommendation = Recommendation.MARGINAL
        else:
            recommendation = Recommendation.SKIP

        result = VRPResult(
            ticker=ticker,
            expiration=expiration,
            implied_move_pct=implied_move.implied_move_pct,
            historical_mean_move_pct=Percentage(mean_move),
            vrp_ratio=vrp_ratio,
            edge_score=edge_score,
            recommendation=recommendation,
        )

        logger.info(
            f"{ticker}: VRP {vrp_ratio:.2f}x "
            f"(implied: {implied_pct:.2f}%, historical: {mean_move:.2f}%) "
            f"→ {recommendation.value.upper()}"
        )

        return Ok(result)

    def calculate_with_consistency(
        self,
        ticker: str,
        expiration: date,
        implied_move: ImpliedMove,
        historical_moves: List[HistoricalMove],
    ) -> Result[tuple[VRPResult, dict], AppError]:
        """
        Calculate VRP with detailed consistency metrics.

        Returns:
            Result with (VRPResult, consistency_dict)
        """
        vrp_result = self.calculate(
            ticker, expiration, implied_move, historical_moves
        )

        if vrp_result.is_err:
            return vrp_result

        # Calculate detailed consistency metrics using the same metric as VRP
        if self.move_metric == "close":
            historical_pcts = [
                float(move.close_move_pct.value) for move in historical_moves
            ]
        elif self.move_metric == "intraday":
            historical_pcts = [
                float(move.intraday_move_pct.value) for move in historical_moves
            ]
        else:  # gap
            historical_pcts = [
                float(move.gap_move_pct.value) for move in historical_moves
            ]

        mean = np.mean(historical_pcts)
        median = np.median(historical_pcts)
        std = np.std(historical_pcts)
        mad = np.median(np.abs(np.array(historical_pcts) - median))

        consistency = {
            'mean': mean,
            'median': median,
            'std': std,
            'mad': mad,
            'mad_pct': (mad / median * 100) if median > 0 else 0,
            'cv': (std / mean) if mean > 0 else 0,  # Coefficient of variation
            'sample_size': len(historical_moves),
        }

        return Ok((vrp_result.value, consistency))
