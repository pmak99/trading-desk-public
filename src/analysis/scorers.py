"""
Scoring strategies for ticker evaluation using Strategy pattern.

Breaks down the 172-line calculate_score() god function into
manageable, testable components.
"""

from abc import ABC, abstractmethod
from typing import Dict
import logging
from src.config.config_loader import ConfigLoader

logger = logging.getLogger(__name__)


# Load config once at module level using shared config loader
_TRADING_CRITERIA = ConfigLoader.load_trading_criteria()


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

    PRIMARY FILTER - 40% weight
    - IV >= minimum required (60% for IV, 50% for IV Rank from config)
    - 60-80% IV: Good (score 60-80)
    - 80-100% IV: Excellent (score 80-100)
    - 100%+ IV: Premium (score 100)
    """

    def __init__(self, weight: float = None, min_iv: int = None):
        # Load from config or use defaults
        if _TRADING_CRITERIA:
            weight = weight or _TRADING_CRITERIA['scoring_weights']['iv_score']
            min_iv = min_iv or _TRADING_CRITERIA['iv_thresholds']['minimum']
            self.iv_rank_min = _TRADING_CRITERIA['iv_rank_thresholds']['minimum']
            self.iv_rank_good = _TRADING_CRITERIA['iv_rank_thresholds']['good']
            self.iv_rank_excellent = _TRADING_CRITERIA['iv_rank_thresholds']['excellent']
            self.iv_excellent = _TRADING_CRITERIA['iv_thresholds']['excellent']
            self.iv_extreme = _TRADING_CRITERIA['iv_thresholds']['extreme']
        else:
            # Fallback to hardcoded defaults
            weight = weight or 0.50
            min_iv = min_iv or 60
            self.iv_rank_min = 50
            self.iv_rank_good = 60
            self.iv_rank_excellent = 75
            self.iv_excellent = 80
            self.iv_extreme = 100

        super().__init__(weight)
        self.min_iv = min_iv

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
        # HARD FILTER: Must be >= min_iv
        if current_iv < self.min_iv:
            logger.info(f"{ticker}: IV {current_iv}% < {self.min_iv}% - SKIPPING")
            return 0.0

        # Score based on IV level
        if current_iv >= self.iv_extreme:  # Premium IV - exceptional
            return 100.0
        elif current_iv >= self.iv_excellent:  # Excellent IV
            return 80.0 + (current_iv - self.iv_excellent) * 1.0
        else:  # min_iv to iv_excellent - good IV
            return 60.0 + (current_iv - self.min_iv) * 1.0

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
        # Convert decimal to percentage for comparison with self.min_iv
        iv_pct = iv * 100
        if iv_pct >= self.min_iv:
            return 80.0  # Probably high IV
        elif iv_pct >= (self.min_iv * 0.67):  # ~2/3 of minimum
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

    def __init__(self, weight: float = None):
        # Load from config or use defaults
        if _TRADING_CRITERIA:
            weight = weight or _TRADING_CRITERIA['scoring_weights']['iv_crush_edge']
            self.excellent = _TRADING_CRITERIA['iv_crush_thresholds']['excellent']
            self.better = _TRADING_CRITERIA['iv_crush_thresholds']['better']
            self.good = _TRADING_CRITERIA['iv_crush_thresholds']['good']
            self.minimum = _TRADING_CRITERIA['iv_crush_thresholds']['minimum']
        else:
            weight = weight or 0.30
            self.excellent = 1.3
            self.better = 1.2
            self.good = 1.1
            self.minimum = 1.0

        super().__init__(weight)

    def score(self, data: Dict) -> float:
        """Score based on IV crush ratio."""
        options_data = data.get('options_data', {})
        iv_crush_ratio = options_data.get('iv_crush_ratio')

        if iv_crush_ratio is None:
            return 50.0  # Neutral - no data

        # iv_crush_ratio > 1.0 means implied > actual (good for IV crush)
        if iv_crush_ratio >= self.excellent:  # Implied 30%+ higher
            return 100.0
        elif iv_crush_ratio >= self.better:  # Implied 20%+ higher
            return 80.0
        elif iv_crush_ratio >= self.good:  # Implied 10%+ higher
            return 60.0
        elif iv_crush_ratio >= self.minimum:  # Implied slightly higher
            return 40.0
        else:  # Implied < actual (no edge)
            return 0.0


class LiquidityScorer(TickerScorer):
    """
    Score based on OPTIONS market liquidity (not stock volume).

    30% weight in final score

    Components:
    - Options volume (40%) - total daily volume of options contracts traded
    - Open interest (40%) - total outstanding options contracts
    - Bid-ask spread (20%) - tightness of options market

    IMPORTANT: This uses OPTIONS metrics, NOT underlying stock volume.
    Stock volume is stored separately in data['volume'] but NOT used for scoring.
    """

    def __init__(self, weight: float = None):
        # Load from config or use defaults
        if _TRADING_CRITERIA:
            weight = weight or _TRADING_CRITERIA['scoring_weights']['options_liquidity']
            liq = _TRADING_CRITERIA['liquidity_thresholds']
            self.min_volume = liq.get('minimum_volume', 100)
            self.min_oi = liq.get('minimum_open_interest', 500)
            self.vol_very_high = liq['volume']['very_high']
            self.vol_high = liq['volume']['high']
            self.vol_good = liq['volume']['good']
            self.vol_acceptable = liq['volume']['acceptable']
            self.oi_very_liquid = liq['open_interest']['very_liquid']
            self.oi_liquid = liq['open_interest']['liquid']
            self.oi_good = liq['open_interest']['good']
            self.oi_acceptable = liq['open_interest']['acceptable']
            self.spread_excellent = liq['spread']['excellent']
            self.spread_good = liq['spread']['good']
            self.spread_okay = liq['spread']['okay']
        else:
            weight = weight or 0.15
            self.min_volume = 100
            self.min_oi = 500
            self.vol_very_high = 50000
            self.vol_high = 10000
            self.vol_good = 5000
            self.vol_acceptable = 1000
            self.oi_very_liquid = 100000
            self.oi_liquid = 50000
            self.oi_good = 10000
            self.oi_acceptable = 5000
            self.spread_excellent = 0.02
            self.spread_good = 0.05
            self.spread_okay = 0.10

        super().__init__(weight)

    def score(self, data: Dict) -> float:
        """Score based on OPTIONS liquidity metrics (not stock volume)."""
        options_data = data.get('options_data', {})
        ticker = data.get('ticker', 'UNKNOWN')

        # NOTE: Using OPTIONS volume and open interest, NOT stock volume
        # options_volume = total daily volume of all options contracts
        # open_interest = total number of outstanding options contracts
        options_volume = options_data.get('options_volume', 0)
        open_interest = options_data.get('open_interest', 0)

        # HARD FILTER: Must meet minimum liquidity requirements
        if options_volume < self.min_volume or open_interest < self.min_oi:
            logger.info(f"{ticker}: Liquidity too low (Options Vol: {options_volume}, OI: {open_interest}) - SKIPPING")
            return 0.0

        volume_score = self._score_options_volume(options_volume)
        oi_score = self._score_open_interest(open_interest)
        spread_score = self._score_bid_ask_spread(
            options_data.get('bid_ask_spread_pct')
        )

        # Weighted combination
        return (volume_score * 0.4) + (oi_score * 0.4) + (spread_score * 0.2)

    def _score_options_volume(self, volume: int) -> float:
        """Score options volume."""
        if volume >= self.vol_very_high:  # Very high
            return 100.0
        elif volume >= self.vol_high:  # High
            return 80.0
        elif volume >= self.vol_good:  # Good
            return 60.0
        elif volume >= self.vol_acceptable:  # Acceptable
            return 40.0
        else:
            return 20.0

    def _score_open_interest(self, oi: int) -> float:
        """Score open interest."""
        if oi >= self.oi_very_liquid:  # Very liquid
            return 100.0
        elif oi >= self.oi_liquid:  # Liquid
            return 80.0
        elif oi >= self.oi_good:  # Good
            return 60.0
        elif oi >= self.oi_acceptable:  # Acceptable
            return 40.0
        else:
            return 20.0

    def _score_bid_ask_spread(self, spread_pct: float) -> float:
        """Score bid-ask spread (lower is better)."""
        if spread_pct is None:
            return 50.0  # No data

        if spread_pct <= self.spread_excellent:  # Excellent
            return 100.0
        elif spread_pct <= self.spread_good:  # Good
            return 80.0
        elif spread_pct <= self.spread_okay:  # Okay
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

    def __init__(self, weight: float = None):
        # Load from config or use defaults
        if _TRADING_CRITERIA:
            weight = weight or _TRADING_CRITERIA['scoring_weights']['fundamentals']
            fund = _TRADING_CRITERIA['fundamentals']
            self.mega_cap = fund['market_cap']['mega_cap'] * 1e9
            self.large_cap = fund['market_cap']['large_cap'] * 1e9
            self.mid_cap = fund['market_cap']['mid_cap'] * 1e9
            self.price_min_ideal = fund['price']['min_ideal']
            self.price_max_ideal = fund['price']['max_ideal']
            self.price_min_acceptable = fund['price']['min_acceptable']
            self.price_max_acceptable = fund['price']['max_acceptable']
        else:
            weight = weight or 0.05
            self.mega_cap = 200e9
            self.large_cap = 50e9
            self.mid_cap = 10e9
            self.price_min_ideal = 50
            self.price_max_ideal = 400
            self.price_min_acceptable = 20
            self.price_max_acceptable = 500

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
        if market_cap >= self.mega_cap:  # Mega cap
            return 100.0
        elif market_cap >= self.large_cap:  # Large cap
            return 80.0
        elif market_cap >= self.mid_cap:  # Mid cap
            return 60.0
        else:
            return 40.0

    def _score_price(self, price: float) -> float:
        """Score price range (ideal for premium selling)."""
        if self.price_min_ideal <= price <= self.price_max_ideal:  # Ideal for selling premium
            return 100.0
        elif self.price_min_acceptable <= price <= self.price_max_acceptable:  # Acceptable
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
        min_iv: int = None
    ):
        """
        Initialize composite scorer.

        Args:
            scorers: List of TickerScorer instances (defaults to standard set)
            min_iv: Minimum IV percentage for filtering (loads from config if None)
        """
        if scorers is None:
            # Default scoring strategy - weights and thresholds from config or defaults
            self.scorers = [
                IVScorer(min_iv=min_iv),  # Loads weight and min_iv from config
                IVCrushEdgeScorer(),       # Loads weight from config
                LiquidityScorer(),         # Loads weight from config
                FundamentalsScorer()       # Loads weight from config
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

            # Hard filter: If IVScorer or LiquidityScorer returns 0, filter out the ticker
            # IVScorer: IV < 50% or IV Rank < 50%
            # LiquidityScorer: Volume < 100 or OI < 500
            if score == 0 and isinstance(scorer, (IVScorer, LiquidityScorer)):
                return 0.0

            scores.append(score * scorer.weight)

        # Total weighted score
        total = sum(scores)

        return round(total, 2)
