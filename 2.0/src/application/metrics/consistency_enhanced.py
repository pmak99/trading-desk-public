"""
Enhanced Consistency Analyzer - Phase 4 Algorithmic Optimization

Exponential-weighted consistency analysis with trend detection.
Recent earnings moves are weighted more heavily than older ones.
"""

import logging
from typing import List
from dataclasses import dataclass
import math

from src.domain.types import HistoricalMove, Percentage
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode

logger = logging.getLogger(__name__)


@dataclass
class ConsistencyAnalysis:
    """
    Enhanced consistency analysis results.

    Attributes:
        ticker: Stock symbol
        num_quarters: Number of historical quarters analyzed
        mean_move: Weighted mean of historical moves
        std_dev: Standard deviation of moves
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
    Enhanced consistency analyzer with exponential weighting.

    Traditional consistency uses equal weights for all historical quarters.
    This enhanced version:
    1. Applies exponential decay to older quarters
    2. Calculates trend (increasing/decreasing/stable moves)
    3. Computes trustworthiness score
    4. Detects recent bias vs long-term mean

    Benefits:
    - Recent earnings weighted appropriately
    - Trend detection (increasing volatility = bad signal)
    - Confidence score for strategy decisions
    - Better adaptation to changing market conditions
    """

    # Configuration
    MIN_QUARTERS = 4  # Minimum quarters for reliable analysis
    DECAY_FACTOR = 0.85  # Weight decay per quarter (0.85 = 15% decay)
    STABLE_THRESHOLD = 0.5  # Slope threshold for "stable" classification
    HIGH_TRUSTWORTHINESS = 0.7  # Threshold for high trust
    RECENT_QUARTERS = 4  # Number of quarters for "recent" bias

    def __init__(self):
        pass

    def analyze_consistency(
        self,
        ticker: str,
        historical_moves: List[HistoricalMove]
    ) -> Result[ConsistencyAnalysis, AppError]:
        """
        Analyze consistency of historical moves with exponential weighting.

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

        # Extract intraday moves (most relevant for IV crush strategy)
        moves = [float(hm.intraday_move_pct.value) for hm in historical_moves]

        # Calculate exponentially-weighted statistics
        weighted_mean = self._exponential_weighted_mean(moves)
        std_dev = self._calculate_std_dev(moves, weighted_mean)

        # Calculate trend
        trend_slope = self._calculate_trend(moves)
        trend = self._classify_trend(trend_slope)

        # Calculate consistency score (inverse of coefficient of variation)
        # Higher score = more consistent moves
        cv = std_dev / weighted_mean if weighted_mean > 0 else float('inf')
        consistency_score = max(0, min(100, 100 * (1 - cv)))

        # Calculate trustworthiness (combines consistency + sample size)
        trustworthiness = self._calculate_trustworthiness(
            consistency_score,
            len(historical_moves),
            trend_slope
        )

        # Calculate recent bias
        recent_bias = self._calculate_recent_bias(moves, weighted_mean)

        analysis = ConsistencyAnalysis(
            ticker=ticker,
            num_quarters=len(historical_moves),
            mean_move=Percentage(weighted_mean),
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

    def _exponential_weighted_mean(self, moves: List[float]) -> float:
        """
        Calculate exponentially-weighted mean.

        Recent quarters weighted more heavily than older ones.
        Weight for quarter i = DECAY_FACTOR ^ i

        Args:
            moves: List of moves (newest first)

        Returns:
            Weighted mean
        """
        if not moves:
            return 0.0

        weighted_sum = 0.0
        weight_total = 0.0

        for i, move in enumerate(moves):
            weight = self.DECAY_FACTOR ** i
            weighted_sum += move * weight
            weight_total += weight

        return weighted_sum / weight_total if weight_total > 0 else 0.0

    def _calculate_std_dev(
        self,
        moves: List[float],
        mean: float
    ) -> float:
        """
        Calculate standard deviation around weighted mean using sample variance.

        Uses Bessel's correction (n-1) for unbiased estimation of population variance
        from a sample. This is appropriate since historical earnings moves are a sample
        from the true distribution of possible moves.

        Args:
            moves: List of moves
            mean: Weighted mean

        Returns:
            Standard deviation (sample standard deviation)
        """
        if len(moves) < 2:
            return 0.0

        # Use sample variance (n-1) for unbiased estimation
        variance = sum((x - mean) ** 2 for x in moves) / (len(moves) - 1)
        return math.sqrt(variance)

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
            overall_mean: Overall weighted mean

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
