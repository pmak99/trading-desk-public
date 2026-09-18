"""
Enhanced Consistency Analyzer - Phase 4 Algorithmic Optimization

Consistency (1 - coefficient of variation), trend and trustworthiness of the
historical moves VRP divides by.

One weighting convention: the mean and the standard deviation are both
equal-weighted, because the number this describes is VRPCalculator's
denominator -- an equal-weighted mean of the same moves -- and because the
Jun 2026 A/B that found consistency flat-to-anti-predictive
(scripts/research/scoring_factors.py) computed it equal-weighted. Until
2026-09-14 the mean was weighted 0.85^i toward recent quarters while the SD was
an unweighted sample SD taken about that weighted mean, so the distance between
the two means was counted as dispersion (audit 4.4).
"""

import logging
import statistics
from dataclasses import dataclass
from typing import List

from src.domain.errors import AppError, Err, ErrorCode, Ok, Result
from src.domain.types import HistoricalMove, Percentage

logger = logging.getLogger(__name__)


@dataclass
class ConsistencyAnalysis:
    """
    Enhanced consistency analysis results.

    Attributes:
        ticker: Stock symbol
        num_quarters: Number of historical quarters analyzed
        mean_move: Mean absolute move, equal-weighted (the VRP denominator)
        std_dev: Sample standard deviation of absolute moves about that mean
        consistency_score: 0-100 score (higher = more consistent)
        trend: Movement trend (increasing, decreasing, stable)
        trend_slope: Rate of change in moves over time
        trustworthiness: 0-1 score (higher = more reliable signal)
        recent_bias: Recent moves vs overall mean
    """
    ticker: str
    num_quarters: int
    mean_move: Percentage
    std_dev: Percentage
    consistency_score: float
    trend: str
    trend_slope: float
    trustworthiness: float
    recent_bias: float


class ConsistencyAnalyzerEnhanced:
    """
    Consistency analyzer over the moves VRP divides by.

    1. Consistency score from the coefficient of variation (equal-weighted
       mean and SD -- see the module docstring for why not recency-weighted)
    2. Trend (increasing/decreasing/stable moves)
    3. Trustworthiness score
    4. Recent bias: the last RECENT_QUARTERS moves vs the overall mean

    IMPORTANT: The move_metric parameter should match the VRP calculator's
    metric to ensure apples-to-apples comparison. Container passes
    config.algorithms.vrp_move_metric.
    """

    # Configuration
    MIN_QUARTERS = 4  # Minimum quarters for reliable analysis
    STABLE_THRESHOLD = 0.5  # Slope threshold for "stable" classification
    HIGH_TRUSTWORTHINESS = 0.7  # Threshold for high trust
    RECENT_QUARTERS = 4  # Number of quarters for "recent" bias

    def __init__(self, move_metric: str = "close"):
        """
        Initialize consistency analyzer.

        Args:
            move_metric: Which move metric to use for consistency analysis.
                - "close": Close-to-close move (RECOMMENDED - matches VRP/straddle)
                - "intraday": High-low range on earnings day
                - "gap": Gap open vs previous close
        """
        if move_metric not in ["close", "intraday", "gap"]:
            raise ValueError(
                f"Invalid move_metric: {move_metric}. "
                f"Must be 'close', 'intraday', or 'gap'"
            )
        self.move_metric = move_metric

    def analyze_consistency(
        self,
        ticker: str,
        historical_moves: List[HistoricalMove]
    ) -> Result[ConsistencyAnalysis, AppError]:
        """
        Analyze consistency of historical moves.

        Args:
            ticker: Stock symbol
            historical_moves: List of historical earnings moves (newest first)

        Returns:
            Result with ConsistencyAnalysis or AppError
        """
        logger.info(
            f"Analyzing consistency: {ticker} "
            f"with {len(historical_moves)} quarters"
        )

        if len(historical_moves) < self.MIN_QUARTERS:
            return Err(
                AppError(
                    ErrorCode.NODATA,
                    f"Insufficient historical data: "
                    f"{len(historical_moves)} < {self.MIN_QUARTERS} quarters"
                )
            )

        # Extract move magnitudes based on configured metric (matches VRP calculator).
        # DB values are signed; consistency measures magnitude stability, so take
        # abs() — signed values near-cancel and collapse the score to 0.
        if self.move_metric == "close":
            moves = [abs(float(hm.close_move_pct.value)) for hm in historical_moves]
        elif self.move_metric == "intraday":
            moves = [abs(float(hm.intraday_move_pct.value)) for hm in historical_moves]
        elif self.move_metric == "gap":
            moves = [abs(float(hm.gap_move_pct.value)) for hm in historical_moves]
        else:
            # Should never happen due to __init__ validation, but be defensive
            moves = [abs(float(hm.close_move_pct.value)) for hm in historical_moves]

        # Equal-weighted mean and sample SD (Bessel's n-1) about that same mean.
        # MIN_QUARTERS >= 2, so stdev always has the two points it needs.
        mean_move = statistics.mean(moves)
        std_dev = statistics.stdev(moves)

        # Calculate trend
        trend_slope = self._calculate_trend(moves)
        trend = self._classify_trend(trend_slope)

        # Calculate consistency score (inverse of coefficient of variation)
        # Higher score = more consistent moves
        cv = std_dev / mean_move if mean_move > 0 else float('inf')
        consistency_score = max(0, min(100, 100 * (1 - cv)))

        # Calculate trustworthiness (combines consistency + sample size)
        trustworthiness = self._calculate_trustworthiness(
            consistency_score,
            len(historical_moves),
            trend_slope
        )

        # Calculate recent bias
        recent_bias = self._calculate_recent_bias(moves, mean_move)

        analysis = ConsistencyAnalysis(
            ticker=ticker,
            num_quarters=len(historical_moves),
            mean_move=Percentage(mean_move),
            std_dev=Percentage(std_dev),
            consistency_score=consistency_score,
            trend=trend,
            trend_slope=trend_slope,
            trustworthiness=trustworthiness,
            recent_bias=recent_bias
        )

        logger.info(
            f"{ticker}: Consistency={consistency_score:.1f}, "
            f"Trend={trend}, "
            f"Trust={trustworthiness:.2f}"
        )

        return Ok(analysis)

    def _calculate_trend(self, moves: List[float]) -> float:
        """
        Calculate trend slope using linear regression.

        Positive slope = increasing moves (bad for IV crush)
        Negative slope = decreasing moves (good for IV crush)
        Zero slope = stable moves

        Args:
            moves: List of moves (newest first)

        Returns:
            Trend slope (% per quarter)
        """
        n = len(moves)
        if n < 2:
            return 0.0

        # Reverse to get chronological order (oldest first)
        moves_chrono = list(reversed(moves))

        # Simple linear regression: y = mx + b
        x = list(range(n))
        x_mean = sum(x) / n
        y_mean = sum(moves_chrono) / n

        numerator = sum((x[i] - x_mean) * (moves_chrono[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))

        if denominator == 0:
            return 0.0

        slope = numerator / denominator
        return slope

    def _classify_trend(self, slope: float) -> str:
        """
        Classify trend based on slope.

        Args:
            slope: Trend slope

        Returns:
            Trend classification
        """
        if slope > self.STABLE_THRESHOLD:
            return "increasing"  # Moves getting larger (bad signal)
        elif slope < -self.STABLE_THRESHOLD:
            return "decreasing"  # Moves getting smaller (good signal)
        else:
            return "stable"  # Consistent moves

    def _calculate_trustworthiness(
        self,
        consistency_score: float,
        num_quarters: int,
        trend_slope: float
    ) -> float:
        """
        Calculate overall trustworthiness of the signal.

        Combines:
        - Consistency score (0-100)
        - Sample size (more quarters = more trust)
        - Trend stability (stable/decreasing = more trust)

        Args:
            consistency_score: Consistency score (0-100)
            num_quarters: Number of historical quarters
            trend_slope: Trend slope

        Returns:
            Trustworthiness score (0-1)
        """
        # Base trust from consistency (0-1 scale)
        consistency_factor = consistency_score / 100.0

        # Sample size factor (asymptotic to 1.0)
        # 4 quarters = 0.5, 8 quarters = 0.67, 12 quarters = 0.75
        sample_factor = min(1.0, num_quarters / (num_quarters + 4))

        # Trend factor (penalize increasing volatility)
        if trend_slope > self.STABLE_THRESHOLD:
            trend_factor = 0.7  # Penalty for increasing moves
        else:
            trend_factor = 1.0  # Stable or decreasing is good

        # Combine factors
        trustworthiness = consistency_factor * sample_factor * trend_factor

        # Defensive bounds check (should always be in [0, 1] given factor constraints)
        return min(1.0, max(0.0, trustworthiness))

    def _calculate_recent_bias(
        self,
        moves: List[float],
        overall_mean: float
    ) -> float:
        """
        Calculate bias of recent moves vs overall mean.

        Positive bias = recent moves larger than average
        Negative bias = recent moves smaller than average

        Args:
            moves: List of moves (newest first)
            overall_mean: Overall mean of all moves

        Returns:
            Bias percentage
        """
        if len(moves) < self.RECENT_QUARTERS:
            return 0.0

        recent_moves = moves[:self.RECENT_QUARTERS]
        recent_mean = sum(recent_moves) / len(recent_moves)

        if overall_mean == 0:
            return 0.0

        bias = ((recent_mean - overall_mean) / overall_mean) * 100
        return bias
