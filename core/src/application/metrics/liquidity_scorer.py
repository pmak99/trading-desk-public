"""
Liquidity Scoring System for Options.

Evaluates option liquidity quality using multiple factors:
- Open Interest (market size)
- Volume (current trading activity)
- Bid-Ask Spread (transaction cost)
- Depth (size at bid/ask)

Returns a composite liquidity score (0-100) and individual metrics.
"""

import logging
from dataclasses import dataclass
from typing import Optional
from src.domain.types import OptionQuote, Money

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiquidityScore:
    """
    Comprehensive liquidity scoring for an option.

    Attributes:
        overall_score: Composite score (0-100, higher is better)
        oi_score: Open interest score (0-100)
        volume_score: Volume score (0-100)
        spread_score: Bid-ask spread score (0-100)
        depth_score: Market depth score (0-100)

        # Raw metrics
        open_interest: Open interest
        volume: Daily volume
        bid_ask_spread_pct: Bid-ask spread as % of mid
        effective_spread: Dollar bid-ask spread

        # Quality flags
        is_liquid: Whether option meets minimum liquidity standards
        liquidity_tier: Tier classification (excellent/good/fair/poor)
    """
    overall_score: float
    oi_score: float
    volume_score: float
    spread_score: float
    depth_score: Optional[float]

    open_interest: int
    volume: int
    bid_ask_spread_pct: float
    effective_spread: Money

    is_liquid: bool
    liquidity_tier: str


class LiquidityScorer:
    """
    Scores option liquidity using multiple factors.

    Scoring methodology:
    - Open Interest: Baseline market size (40% weight)
    - Volume: Current trading activity (30% weight)
    - Bid-Ask Spread: Transaction cost (25% weight)
    - Depth: Size at bid/ask if available (5% weight)

    Thresholds are configurable and based on market norms for earnings trades.
    """

    def __init__(
        self,
        min_oi: int = 50,
        good_oi: int = 500,
        excellent_oi: int = 2000,
        min_volume: int = 20,
        good_volume: int = 100,
        excellent_volume: int = 500,
        max_spread_pct: float = 10.0,
        good_spread_pct: float = 5.0,
        excellent_spread_pct: float = 2.0,
    ):
        """
        Initialize liquidity scorer with thresholds.

        Args:
            min_oi: Minimum acceptable open interest
            good_oi: Good open interest threshold
            excellent_oi: Excellent open interest threshold
            min_volume: Minimum acceptable volume
            good_volume: Good volume threshold
            excellent_volume: Excellent volume threshold
            max_spread_pct: Maximum acceptable spread %
            good_spread_pct: Good spread % threshold
            excellent_spread_pct: Excellent spread % threshold
        """
        self.min_oi = min_oi
        self.good_oi = good_oi
        self.excellent_oi = excellent_oi
        self.min_volume = min_volume
        self.good_volume = good_volume
        self.excellent_volume = excellent_volume
        self.max_spread_pct = max_spread_pct
        self.good_spread_pct = good_spread_pct
        self.excellent_spread_pct = excellent_spread_pct

        # Scoring weights
        self.oi_weight = 0.40
        self.volume_weight = 0.30
        self.spread_weight = 0.25
        self.depth_weight = 0.05

    def score_option(self, option: OptionQuote) -> LiquidityScore:
        """
        Score liquidity for a single option.

        Args:
            option: Option quote with market data

        Returns:
            LiquidityScore with composite and individual scores
        """
        # Extract raw metrics
        oi = option.open_interest or 0
        volume = option.volume or 0

        # Calculate bid-ask spread
        if option.bid and option.ask:
            spread = float(option.ask.amount - option.bid.amount)
            effective_spread = Money(spread)
            mid = float(option.mid.amount)
            spread_pct = (spread / mid * 100) if mid > 0 else 100.0
        else:
            # No bid/ask available - use worst case
            effective_spread = Money(999.99)
            spread_pct = 100.0

        # Score each factor
        oi_score = self._score_open_interest(oi)
        volume_score = self._score_volume(volume)
        spread_score = self._score_spread(spread_pct)

        # Depth score (optional - not all data providers give this)
        depth_score = None
        if hasattr(option, 'bid_size') and hasattr(option, 'ask_size'):
            depth_score = self._score_depth(option.bid_size, option.ask_size)

        # Calculate weighted composite score
        if depth_score is not None:
            # All factors available
            overall = (
                oi_score * self.oi_weight +
                volume_score * self.volume_weight +
                spread_score * self.spread_weight +
                depth_score * self.depth_weight
            )
        else:
            # No depth data - redistribute weight proportionally
            weights = self._redistribute_weights_without_depth()
            overall = (
                oi_score * weights['oi'] +
                volume_score * weights['volume'] +
                spread_score * weights['spread']
            )

        # Determine liquidity tier
        if overall >= 80:
            tier = "excellent"
        elif overall >= 65:
            tier = "good"
        elif overall >= 50:
            tier = "fair"
        else:
            tier = "poor"

        # Check if meets minimum standards
        is_liquid = (
            oi >= self.min_oi and
            volume >= self.min_volume and
            spread_pct <= self.max_spread_pct
        )

        return LiquidityScore(
            overall_score=overall,
            oi_score=oi_score,
            volume_score=volume_score,
            spread_score=spread_score,
            depth_score=depth_score,
            open_interest=oi,
            volume=volume,
            bid_ask_spread_pct=spread_pct,
            effective_spread=effective_spread,
            is_liquid=is_liquid,
            liquidity_tier=tier,
        )

    def _redistribute_weights_without_depth(self) -> dict:
        """
        Redistribute weights when depth data is unavailable.

        The depth weight (5%) is redistributed proportionally across
        the other factors based on their original weights.

        Returns:
            Dict with redistributed weights for oi, volume, and spread
        """
        # Calculate total weight without depth
        total = self.oi_weight + self.volume_weight + self.spread_weight

        # Redistribute proportionally to sum to 1.0
        return {
            'oi': self.oi_weight / total,
            'volume': self.volume_weight / total,
            'spread': self.spread_weight / total,
        }

    def _score_open_interest(self, oi: int) -> float:
        """
        Score open interest (0-100).

        Open interest represents the total number of outstanding contracts.
        Higher OI = more liquid, easier to enter/exit positions.
        """
        if oi >= self.excellent_oi:
            return 100.0
        elif oi >= self.good_oi:
            # Linear interpolation between good and excellent
            ratio = (oi - self.good_oi) / (self.excellent_oi - self.good_oi)
            return 80.0 + (ratio * 20.0)
        elif oi >= self.min_oi:
            # Linear interpolation between min and good
            ratio = (oi - self.min_oi) / (self.good_oi - self.min_oi)
            return 50.0 + (ratio * 30.0)
        else:
            # Below minimum - score drops off quickly
            ratio = oi / self.min_oi
            return ratio * 50.0

    def _score_volume(self, volume: int) -> float:
        """
        Score daily volume (0-100).

        Volume represents current trading activity.
        Higher volume = more active market, better price discovery.
        """
        if volume >= self.excellent_volume:
            return 100.0
        elif volume >= self.good_volume:
            # Linear interpolation between good and excellent
            ratio = (volume - self.good_volume) / (self.excellent_volume - self.good_volume)
            return 80.0 + (ratio * 20.0)
        elif volume >= self.min_volume:
            # Linear interpolation between min and good
            ratio = (volume - self.min_volume) / (self.good_volume - self.min_volume)
            return 50.0 + (ratio * 30.0)
        else:
            # Below minimum - score drops off quickly
            ratio = volume / self.min_volume if self.min_volume > 0 else 0
            return ratio * 50.0

    def _score_spread(self, spread_pct: float) -> float:
        """
        Score bid-ask spread (0-100).

        Spread represents transaction cost.
        Lower spread = better, less slippage when entering/exiting.

        Inverted scoring: lower spread = higher score
        """
        if spread_pct <= self.excellent_spread_pct:
            return 100.0
        elif spread_pct <= self.good_spread_pct:
            # Linear interpolation between excellent and good
            ratio = (spread_pct - self.excellent_spread_pct) / (self.good_spread_pct - self.excellent_spread_pct)
            return 100.0 - (ratio * 20.0)
        elif spread_pct <= self.max_spread_pct:
            # Linear interpolation between good and max
            ratio = (spread_pct - self.good_spread_pct) / (self.max_spread_pct - self.good_spread_pct)
            return 80.0 - (ratio * 30.0)
        else:
            # Above maximum - score drops off quickly
            # At 2x max spread, score is 0
            ratio = min(1.0, (spread_pct - self.max_spread_pct) / self.max_spread_pct)
            return 50.0 * (1.0 - ratio)

    def _score_depth(self, bid_size: Optional[int], ask_size: Optional[int]) -> float:
        """
        Score market depth (0-100).

        Depth represents size available at bid/ask.
        Higher depth = can trade larger size without moving market.

        Note: Not all data providers give depth, so this is optional.
        """
        if bid_size is None or ask_size is None:
            return 50.0  # Neutral score if no data

        min_size = min(bid_size, ask_size)

        # Thresholds for depth scoring (in contracts)
        # For earnings trades, 10+ contracts is good, 50+ is excellent
        if min_size >= 50:
            return 100.0
        elif min_size >= 10:
            ratio = (min_size - 10) / 40
            return 80.0 + (ratio * 20.0)
        elif min_size >= 5:
            ratio = (min_size - 5) / 5
            return 60.0 + (ratio * 20.0)
        else:
            # Below 5 contracts - poor depth
            return (min_size / 5) * 60.0

    def score_strategy_legs(self, legs: list[OptionQuote]) -> dict:
        """
        Score liquidity for all legs in a strategy.

        Args:
            legs: List of option quotes for strategy legs

        Returns:
            Dict with aggregate metrics:
            - min_score: Minimum score across all legs (bottleneck)
            - avg_score: Average score
            - all_liquid: Whether all legs meet minimum standards
            - scores: Individual LiquidityScore for each leg
        """
        if not legs:
            return {
                'min_score': 0,
                'avg_score': 0,
                'all_liquid': False,
                'scores': [],
            }

        scores = [self.score_option(leg) for leg in legs]

        min_score = min(s.overall_score for s in scores)
        avg_score = sum(s.overall_score for s in scores) / len(scores)
        all_liquid = all(s.is_liquid for s in scores)

        return {
            'min_score': min_score,
            'avg_score': avg_score,
            'all_liquid': all_liquid,
            'scores': scores,
        }
