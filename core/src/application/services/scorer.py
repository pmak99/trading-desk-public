"""
Ticker scoring and ranking for trade selection.

Scores tickers based on multiple factors (VRP, consistency, skew, liquidity)
and ranks them for position selection.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple
from datetime import date

from src.config.scoring_config import ScoringConfig, ScoringWeights, ScoringThresholds
from src.domain.types import Percentage

logger = logging.getLogger(__name__)


@dataclass
class TickerScore:
    """
    Composite score for a ticker with component breakdowns.
    """

    ticker: str
    earnings_date: date

    # Component scores (0-100 scale)
    vrp_score: float
    consistency_score: float
    skew_score: float
    liquidity_score: float

    # Composite score (weighted sum, 0-100 scale)
    composite_score: float

    # Raw metrics for reference
    vrp_ratio: Optional[float] = None
    consistency: Optional[float] = None
    avg_historical_move: Optional[float] = None

    # Ranking
    rank: Optional[int] = None
    selected: bool = False


class TickerScorer:
    """
    Scores and ranks tickers for trade selection.

    Uses configurable weights to combine multiple factors into
    a composite score, then ranks tickers for position selection.
    """

    def __init__(self, config: ScoringConfig):
        """
        Initialize scorer with configuration.

        Args:
            config: Scoring configuration with weights and thresholds
        """
        self.config = config
        self.weights = config.weights
        self.thresholds = config.thresholds

    def calculate_vrp_score(
        self,
        vrp_ratio: Optional[float],
    ) -> float:
        """
        Calculate VRP score (0-100).

        Args:
            vrp_ratio: Implied move / Historical mean move ratio

        Returns:
            Score from 0-100 based on VRP thresholds
        """
        if vrp_ratio is None or vrp_ratio <= 0:
            return 0.0

        # Excellent: 100 points
        if vrp_ratio >= self.thresholds.vrp_excellent:
            return 100.0

        # Good: 75 points
        if vrp_ratio >= self.thresholds.vrp_good:
            # Linear interpolation between good and excellent
            ratio_range = self.thresholds.vrp_excellent - self.thresholds.vrp_good
            if ratio_range <= 0:
                return 75.0
            ratio_above = vrp_ratio - self.thresholds.vrp_good
            return 75.0 + (25.0 * ratio_above / ratio_range)

        # Marginal: 50 points
        if vrp_ratio >= self.thresholds.vrp_marginal:
            # Linear interpolation between marginal and good
            ratio_range = self.thresholds.vrp_good - self.thresholds.vrp_marginal
            if ratio_range <= 0:
                return 50.0
            ratio_above = vrp_ratio - self.thresholds.vrp_marginal
            return 50.0 + (25.0 * ratio_above / ratio_range)

        # Below marginal: Gradual decline from 1.0 to marginal threshold
        # FIX: Was hard cutoff at 0, now gradual decline (still filters low edge)
        if vrp_ratio >= 1.0:
            # Linear interpolation from 0 points at 1.0x to 50 points at marginal
            ratio_range = self.thresholds.vrp_marginal - 1.0
            if ratio_range <= 0:
                return 0.0
            ratio_above = vrp_ratio - 1.0
            return 50.0 * (ratio_above / ratio_range)

        # VRP < 1.0: Implied vol less than historical (negative edge)
        return 0.0

    def calculate_consistency_score(
        self,
        consistency: Optional[float],
    ) -> float:
        """
        Calculate consistency score (0-100).

        Args:
            consistency: Trustworthiness score (0-1) from consistency analyzer

        Returns:
            Score from 0-100 based on consistency thresholds
        """
        if consistency is None or consistency < 0:
            return 0.0

        # Excellent: 100 points
        if consistency >= self.thresholds.consistency_excellent:
            return 100.0

        # Good: 75 points
        if consistency >= self.thresholds.consistency_good:
            range_size = (
                self.thresholds.consistency_excellent
                - self.thresholds.consistency_good
            )
            if range_size <= 0:
                return 75.0
            above_good = consistency - self.thresholds.consistency_good
            return 75.0 + (25.0 * above_good / range_size)

        # Marginal: 50 points
        if consistency >= self.thresholds.consistency_marginal:
            range_size = (
                self.thresholds.consistency_good
                - self.thresholds.consistency_marginal
            )
            if range_size <= 0:
                return 50.0
            above_marginal = consistency - self.thresholds.consistency_marginal
            return 50.0 + (25.0 * above_marginal / range_size)

        # Below marginal: No meaningful consistency, return 0
        return 0.0

    def calculate_skew_score(
        self,
        skew: Optional[float],
    ) -> float:
        """
        Calculate skew score (0-100).

        For straddles, neutral skew is best.
        For directional trades, moderate skew is acceptable.
        Extreme skew is penalized.

        Args:
            skew: Skew measure (typically -1 to +1, where 0 is neutral)

        Returns:
            Score from 0-100 based on skew thresholds
        """
        if skew is None:
            # No skew data = assume neutral (not penalized)
            return 75.0

        abs_skew = abs(skew)

        # Neutral (best for straddles): 100 points
        if abs_skew <= self.thresholds.skew_neutral_range:
            return 100.0

        # Moderate skew: 70 points
        if abs_skew <= self.thresholds.skew_moderate_range:
            range_size = (
                self.thresholds.skew_moderate_range
                - self.thresholds.skew_neutral_range
            )
            if range_size <= 0:
                return 70.0
            above_neutral = abs_skew - self.thresholds.skew_neutral_range
            return 100.0 - (30.0 * above_neutral / range_size)

        # Extreme skew: 40 points (still tradeable but not ideal)
        # For now, cap at 40 for very extreme skew
        return max(40.0, 70.0 - (abs_skew - self.thresholds.skew_moderate_range) * 50)

    def calculate_liquidity_score(
        self,
        open_interest: Optional[int],
        bid_ask_spread_pct: Optional[float],
        volume: Optional[int],
    ) -> float:
        """
        Calculate liquidity score (0-100).

        Combines open interest, bid-ask spread, and volume.

        Args:
            open_interest: Number of open contracts
            bid_ask_spread_pct: Bid-ask spread as percentage of mid
            volume: Daily volume

        Returns:
            Score from 0-100 based on liquidity thresholds
        """
        scores = []

        # Open Interest component (0-10 points)
        if open_interest is not None:
            if open_interest >= self.thresholds.excellent_open_interest:
                scores.append(10.0)
            elif open_interest >= self.thresholds.good_open_interest:
                scores.append(7.5)
            elif open_interest >= self.thresholds.min_open_interest:
                scores.append(5.0)
            else:
                scores.append(0.0)
        else:
            scores.append(5.0)  # Neutral if unknown

        # Spread component (0-10 points)
        if bid_ask_spread_pct is not None:
            if bid_ask_spread_pct <= self.thresholds.max_spread_excellent:
                scores.append(10.0)
            elif bid_ask_spread_pct <= self.thresholds.max_spread_good:
                scores.append(7.5)
            elif bid_ask_spread_pct <= self.thresholds.max_spread_marginal:
                scores.append(5.0)
            else:
                scores.append(0.0)
        else:
            scores.append(5.0)  # Neutral if unknown

        # Volume component (0-5 points)
        if volume is not None:
            if volume >= self.thresholds.excellent_volume:
                scores.append(5.0)
            elif volume >= self.thresholds.good_volume:
                scores.append(3.5)
            elif volume >= self.thresholds.min_volume:
                scores.append(2.0)
            else:
                scores.append(0.0)
        else:
            scores.append(2.5)  # Neutral if unknown

        # Scale to 0-100 (components sum to 0-25, scale by 4)
        return sum(scores) * 4.0

    def score_ticker(
        self,
        ticker: str,
        earnings_date: date,
        vrp_ratio: Optional[float] = None,
        consistency: Optional[float] = None,
        skew: Optional[float] = None,
        avg_historical_move: Optional[float] = None,
        open_interest: Optional[int] = None,
        bid_ask_spread_pct: Optional[float] = None,
        volume: Optional[int] = None,
    ) -> TickerScore:
        """
        Calculate composite score for a ticker.

        Args:
            ticker: Stock symbol
            earnings_date: Date of earnings event
            vrp_ratio: VRP ratio (implied / historical)
            consistency: Consistency score (0-1)
            skew: Skew measure
            avg_historical_move: Average historical move (for reference)
            open_interest: Options open interest
            bid_ask_spread_pct: Bid-ask spread percentage
            volume: Options volume

        Returns:
            TickerScore with component and composite scores
        """
        # Calculate component scores
        vrp_score = self.calculate_vrp_score(vrp_ratio)
        consistency_score = self.calculate_consistency_score(consistency)
        skew_score = self.calculate_skew_score(skew)
        liquidity_score = self.calculate_liquidity_score(
            open_interest, bid_ask_spread_pct, volume
        )

        # Calculate weighted composite score
        composite_score = (
            vrp_score * self.weights.vrp_weight
            + consistency_score * self.weights.consistency_weight
            + skew_score * self.weights.skew_weight
            + liquidity_score * self.weights.liquidity_weight
        )

        return TickerScore(
            ticker=ticker,
            earnings_date=earnings_date,
            vrp_score=vrp_score,
            consistency_score=consistency_score,
            skew_score=skew_score,
            liquidity_score=liquidity_score,
            composite_score=composite_score,
            vrp_ratio=vrp_ratio,
            consistency=consistency,
            avg_historical_move=avg_historical_move,
        )

    def rank_and_select(
        self,
        scores: List[TickerScore],
    ) -> List[TickerScore]:
        """
        Rank tickers by composite score and select top N.

        Args:
            scores: List of TickerScore objects

        Returns:
            Sorted and ranked list with selection flags set
        """
        # Filter by minimum score
        qualified = [
            s for s in scores
            if s.composite_score >= self.config.min_score
        ]

        # Sort by composite score (descending)
        sorted_scores = sorted(
            qualified,
            key=lambda s: s.composite_score,
            reverse=True,
        )

        # Assign ranks and selection flags
        for i, score in enumerate(sorted_scores, 1):
            score.rank = i
            score.selected = (i <= self.config.max_positions)

        logger.info(
            f"Ranked {len(sorted_scores)} tickers "
            f"({len(qualified)} qualified, {sum(s.selected for s in sorted_scores)} selected)"
        )

        return sorted_scores
