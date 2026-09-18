"""
VRP (Volatility Risk Premium) Calculator - Tier 1 Core Metric

Compares implied move to historical mean move to identify edge.
This is the core signal for the IV Crush strategy.

Metric: close_move_pct (prev close -> reaction close) since the 2026-09 migration,
fixed in config.algorithms.vrp_move_metric. intraday_move_pct (reaction-day open ->
close, gap excluded) was live before; it is still computed and logged as a reference
(logged_vrp_columns). gap_move_pct is research-only.
"""

import logging
from datetime import date
from typing import List

import numpy as np

from src.domain.enums import Recommendation
from src.domain.errors import AppError, Err, ErrorCode, Ok, Result
from src.domain.types import (
    HistoricalMove,
    ImpliedMove,
    Percentage,
    VRPResult,
)
from src.domain.vrp_tier import (
    VRP_EXCELLENT_FROM,
    VRP_GOOD_FROM,
    VRP_MARGINAL_FROM,
    vrp_tier,
)

logger = logging.getLogger(__name__)


class VRPCalculator:
    """
    Calculate VRP ratio and generate trading recommendation.

    VRP Ratio = Implied Move / Historical Mean |Move|

    Graded by src.domain.vrp_tier (CLAUDE.md §1).

    Edge Score: Risk-adjusted metric using consistency
        edge_score = vrp_ratio / (1 + consistency)
        Higher consistency (lower MAD) = higher edge score
    """

    def __init__(
        self,
        threshold_excellent: float = VRP_EXCELLENT_FROM,
        threshold_good: float = VRP_GOOD_FROM,
        threshold_marginal: float = VRP_MARGINAL_FROM,
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

        # Extract historical move magnitudes based on configured metric.
        # DB values are signed; VRP compares against move MAGNITUDE, so take
        # abs() — a raw mean lets up/down moves cancel and inflates VRP.
        if self.move_metric == "close":
            historical_pcts = [
                abs(float(move.close_move_pct.value)) for move in historical_moves
            ]
        elif self.move_metric == "intraday":
            historical_pcts = [
                abs(float(move.intraday_move_pct.value)) for move in historical_moves
            ]
        elif self.move_metric == "gap":
            historical_pcts = [
                abs(float(move.gap_move_pct.value)) for move in historical_moves
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

        if mean_move <= 0 or np.isnan(mean_move) or np.isinf(mean_move):
            return Err(
                AppError(
                    ErrorCode.INVALID,
                    f"Invalid mean move: {mean_move}",
                )
            )

        # Guard against near-zero mean_move to avoid numerical instability
        if mean_move < 1e-6:
            logger.warning(
                f"{ticker}: mean_move ({mean_move}) is near zero, "
                f"returning 0.0 VRP ratio to avoid division instability"
            )
            result = VRPResult(
                ticker=ticker,
                expiration=expiration,
                implied_move_pct=implied_move.implied_move_pct,
                historical_mean_move_pct=Percentage(mean_move),
                vrp_ratio=0.0,
                edge_score=0.0,
                recommendation=Recommendation.SKIP,
                historical_quarters=len(historical_moves),
                move_metric=self.move_metric,
            )
            return Ok(result)

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

        recommendation = vrp_tier(vrp_ratio, **self.thresholds)

        result = VRPResult(
            ticker=ticker,
            expiration=expiration,
            implied_move_pct=implied_move.implied_move_pct,
            historical_mean_move_pct=Percentage(mean_move),
            vrp_ratio=vrp_ratio,
            edge_score=edge_score,
            recommendation=recommendation,
            historical_quarters=len(historical_moves),
            move_metric=self.move_metric,
        )

        logger.info(
            f"{ticker}: VRP {vrp_ratio:.2f}x "
            f"(implied: {implied_pct:.2f}%, historical: {mean_move:.2f}% over {len(historical_moves)}Q) "
            f"→ {recommendation.value.upper()}"
        )

        return Ok(result)


_MOVE_FIELDS = {"intraday": "intraday_move_pct", "close": "close_move_pct"}


def compute_baseline_vrp(
    implied_move_pct: float,
    historical_moves: List[HistoricalMove],
    metric: str,
    min_quarters: int = 4,
) -> tuple:
    """(mean |move| %, implied / mean) on the named move, or (None, None).

    None when fewer than min_quarters moves carry the field or the mean is not
    usable. `metric` is "intraday" or "close", the conventions analysis_log stores.
    """
    if metric not in _MOVE_FIELDS:
        raise ValueError(f"metric must be one of {sorted(_MOVE_FIELDS)}, got {metric!r}")
    field = _MOVE_FIELDS[metric]
    pcts = [
        abs(float(getattr(m, field).value))
        for m in historical_moves
        if getattr(m, field) is not None
    ]
    if len(pcts) < min_quarters:
        return None, None
    mean = float(np.mean(pcts))
    if mean < 1e-6 or np.isnan(mean) or np.isinf(mean):
        return None, None
    return mean, implied_move_pct / mean


def logged_vrp_columns(vrp: VRPResult, historical_moves: List[HistoricalMove]) -> dict:
    """The four analysis_log VRP values for one analysis.

    The graded convention (vrp.move_metric) comes from `vrp` itself, so the stored
    ratio is exactly the one recommendation was graded on; the other convention is
    recomputed on the same implied move, for reference.
    """
    implied = float(vrp.implied_move_pct.value)
    out = {}
    for metric in _MOVE_FIELDS:
        if metric == vrp.move_metric:
            mean, ratio = float(vrp.historical_mean_move_pct.value), vrp.vrp_ratio
        else:
            mean, ratio = compute_baseline_vrp(implied, historical_moves, metric)
        out[f"vrp_{metric}_ratio"] = ratio
        out[f"historical_{metric}_mean_pct"] = mean
    return out
