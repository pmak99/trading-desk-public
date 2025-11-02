"""
Scoring strategies for ticker evaluation using Strategy pattern.

Breaks down the 172-line calculate_score() god function into
manageable, testable components.
"""

from abc import ABC, abstractmethod
from typing import Dict
import logging

logger = logging.getLogger(__name__)


class TickerScorer(ABC):
    """Abstract base class for ticker scoring strategies."""

    def __init__(self, weight: float):
        """
        Initialize scorer with weight.

        Args:
            weight: Weight of this score in final calculation (0.0-1.0)
        """
        self.weight = weight

    @abstractmethod
    def score(self, data: Dict) -> float:
        """
        Calculate score for ticker data.

        Args:
            data: Ticker data dict

        Returns:
            Score from 0-100
        """
        pass

    def weighted_score(self, data: Dict) -> float:
        """Get weighted score."""
        return self.score(data) * self.weight


class IVScorer(TickerScorer):
    """
    Score based on implied volatility (IV) levels.

    PRIMARY FILTER - 50% weight
    - IV >= 60% required (hard filter)
    - 60-80%: Good (score 60-80)
    - 80-100%: Excellent (score 80-100)
    - 100%+: Premium (score 100)
    """

    def __init__(self, weight: float = 0.50, min_iv: int = 60):
        super().__init__(weight)
        self.min_iv = min_iv
        self.iv_rank_min = 50
        self.iv_rank_good = 60
        self.iv_rank_excellent = 75

    def score(self, data: Dict) -> float:
        """Score based on actual IV % or IV Rank."""
        options_data = data.get('options_data', {})
        ticker = data.get('ticker', 'UNKNOWN')

        # Try actual IV % first (most reliable)
        current_iv = options_data.get('current_iv')
        if current_iv is not None and current_iv > 0:
            return self._score_from_current_iv(current_iv, ticker)

        # Fallback to IV Rank
        iv_rank = options_data.get('iv_rank')
        if iv_rank is not None and iv_rank > 0:
            return self._score_from_iv_rank(iv_rank, ticker)

        # Fallback to yfinance IV estimate
        return self._score_from_yf_iv(data.get('iv', 0))

    def _score_from_current_iv(self, current_iv: float, ticker: str) -> float:
        """Score from actual IV percentage."""
        # HARD FILTER: Must be >= 60%
        if current_iv < self.min_iv:
            logger.info(f"{ticker}: IV {current_iv}% < {self.min_iv}% - SKIPPING")
            return 0.0

        # Score based on IV level
        if current_iv >= 100:  # Premium IV - exceptional
            return 100.0
        elif current_iv >= 80:  # Excellent IV
            return 80.0 + (current_iv - 80) * 1.0
        else:  # 60-80% - good IV
            return 60.0 + (current_iv - 60) * 1.0

    def _score_from_iv_rank(self, iv_rank: float, ticker: str) -> float:
        """Score from IV Rank (percentile)."""
        if iv_rank < self.iv_rank_min:
            logger.info(f"{ticker}: IV Rank {iv_rank}% < {self.iv_rank_min}% - SKIPPING")
            return 0.0

        if iv_rank >= self.iv_rank_excellent:  # 75%+
            return 100.0
        elif iv_rank >= self.iv_rank_good:  # 60-75%
            return 70.0 + (iv_rank - self.iv_rank_good) * 2
        else:  # 50-60%
            return 50.0 + (iv_rank - self.iv_rank_min) * 2

    def _score_from_yf_iv(self, iv: float) -> float:
        """Score from yfinance IV estimate (least reliable)."""
        if iv >= 0.60:
            return 80.0  # Probably high IV
        elif iv >= 0.40:
            return 60.0
        else:
            return 30.0  # Low confidence


class IVCrushEdgeScorer(TickerScorer):
    """
    Score based on IV crush edge (implied vs actual moves).

    30% weight
    - Measures if implied move historically > actual move
    - Ratio > 1.3: Excellent edge (score 100)
    - Ratio > 1.2: Good edge (score 80)
    - Ratio < 1.0: No edge (score 0)
    """

    def __init__(self, weight: float = 0.30):
        super().__init__(weight)

    def score(self, data: Dict) -> float:
        """Score based on IV crush ratio."""
        options_data = data.get('options_data', {})
        iv_crush_ratio = options_data.get('iv_crush_ratio')

        if iv_crush_ratio is None:
            return 50.0  # Neutral - no data

        # iv_crush_ratio > 1.0 means implied > actual (good for IV crush)
        if iv_crush_ratio >= 1.3:  # Implied 30%+ higher
            return 100.0
        elif iv_crush_ratio >= 1.2:  # Implied 20%+ higher
            return 80.0
        elif iv_crush_ratio >= 1.1:  # Implied 10%+ higher
            return 60.0
        elif iv_crush_ratio >= 1.0:  # Implied slightly higher
            return 40.0
        else:  # Implied < actual (no edge)
            return 0.0


class LiquidityScorer(TickerScorer):
    """
    Score based on options market liquidity.

    15% weight
    Components:
    - Options volume (40%)
    - Open interest (40%)
    - Bid-ask spread (20%)
    """

    def __init__(self, weight: float = 0.15):
        super().__init__(weight)

    def score(self, data: Dict) -> float:
        """Score based on liquidity metrics."""
        options_data = data.get('options_data', {})

        volume_score = self._score_options_volume(
            options_data.get('options_volume', 0)
        )
        oi_score = self._score_open_interest(
            options_data.get('open_interest', 0)
        )
        spread_score = self._score_bid_ask_spread(
            options_data.get('bid_ask_spread_pct')
        )

        # Weighted combination
        return (volume_score * 0.4) + (oi_score * 0.4) + (spread_score * 0.2)

    def _score_options_volume(self, volume: int) -> float:
        """Score options volume."""
        if volume >= 50000:  # Very high
            return 100.0
        elif volume >= 10000:  # High
            return 80.0
        elif volume >= 5000:  # Good
            return 60.0
        elif volume >= 1000:  # Acceptable
            return 40.0
        else:
            return 20.0

    def _score_open_interest(self, oi: int) -> float:
        """Score open interest."""
        if oi >= 100000:  # Very liquid
            return 100.0
        elif oi >= 50000:  # Liquid
            return 80.0
        elif oi >= 10000:  # Good
            return 60.0
        elif oi >= 5000:  # Acceptable
            return 40.0
        else:
            return 20.0

    def _score_bid_ask_spread(self, spread_pct: float) -> float:
        """Score bid-ask spread (lower is better)."""
        if spread_pct is None:
            return 50.0  # No data

        if spread_pct <= 0.02:  # 2% or less - excellent
            return 100.0
        elif spread_pct <= 0.05:  # 5% or less - good
            return 80.0
        elif spread_pct <= 0.10:  # 10% or less - okay
            return 60.0
        else:  # Wide spreads - bad
            return 20.0


class FundamentalsScorer(TickerScorer):
    """
    Score based on fundamental characteristics.

    5% weight (minor factor)
    Components:
    - Market cap (50%)
    - Stock price range (50%)
    """

    def __init__(self, weight: float = 0.05):
        super().__init__(weight)

    def score(self, data: Dict) -> float:
        """Score based on fundamentals."""
        market_cap = data.get('market_cap', 0)
        price = data.get('price', 0)

        cap_score = self._score_market_cap(market_cap)
        price_score = self._score_price(price)

        return (cap_score + price_score) / 2

    def _score_market_cap(self, market_cap: float) -> float:
        """Score market capitalization."""
        if market_cap >= 200e9:  # $200B+ mega cap
            return 100.0
        elif market_cap >= 50e9:  # $50B+ large cap
            return 80.0
        elif market_cap >= 10e9:  # $10B+ mid cap
            return 60.0
        else:
            return 40.0

    def _score_price(self, price: float) -> float:
        """Score price range (ideal for premium selling)."""
        if 50 <= price <= 400:  # Ideal for selling premium
            return 100.0
        elif 20 <= price <= 500:  # Acceptable
            return 80.0
        else:
            return 50.0


class CompositeScorer:
    """
    Composite scorer that combines multiple scoring strategies.

    Implements the calculation previously done in the 172-line
    calculate_score() method.
    """

    def __init__(
        self,
        scorers: list[TickerScorer] = None,
        min_iv: int = 60
    ):
        """
        Initialize composite scorer.

        Args:
            scorers: List of TickerScorer instances (defaults to standard set)
            min_iv: Minimum IV percentage for filtering
        """
        if scorers is None:
            # Default scoring strategy (matches original weights)
            self.scorers = [
                IVScorer(weight=0.50, min_iv=min_iv),
                IVCrushEdgeScorer(weight=0.30),
                LiquidityScorer(weight=0.15),
                FundamentalsScorer(weight=0.05)
            ]
        else:
            self.scorers = scorers

    def calculate_score(self, data: Dict) -> float:
        """
        Calculate composite score for ticker.

        Args:
            data: Ticker data dict

        Returns:
            Score from 0-100, or 0 if filtered out
        """
        # Calculate individual scores
        scores = []
        for scorer in self.scorers:
            score = scorer.score(data)

            # Hard filter: If any scorer returns 0, the ticker is filtered out
            # (currently only IVScorer does this for IV < 60%)
            if score == 0 and isinstance(scorer, IVScorer):
                return 0.0

            scores.append(score * scorer.weight)

        # Total weighted score
        total = sum(scores)

        return round(total, 2)
