"""
Scoring strategies for ticker evaluation using Strategy pattern.

Breaks down the 172-line calculate_score() god function into
manageable, testable components.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional
import logging
from src.config.config_loader import ConfigLoader
from src.core.types import TickerData, OptionsData
from src.core.memoization import memoize_with_dict_key

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
    def score(self, data: TickerData) -> float:
        """
        Calculate score for ticker data.

        Args:
            data: Ticker data dict

        Returns:
            Score from 0-100
        """
        pass

    def weighted_score(self, data: TickerData) -> float:
        """Get weighted score."""
        return self.score(data) * self.weight


class IVScorer(TickerScorer):
    """
    Score based on ABSOLUTE implied volatility level (current IV %).

    SIMPLIFIED - 25% weight (reduced from 40%)
    Answers: "Is there enough premium to crush?" (absolute level)

    This is a FILTER + LEVEL SCORE:
    - IV < 60%: Filtered out (score 0) - not enough premium
    - 60-80% IV: Good level (score 60-80)
    - 80-100% IV: Excellent level (score 80-100)
    - 100%+ IV: Extreme level (score 100)

    Note: This focuses purely on CURRENT IV level, not historical context.
    Use IVExpansionScorer for timing (is premium building NOW?).
    """

    def __init__(self, weight: Optional[float] = None, min_iv: Optional[int] = None) -> None:
        # Load from config or use defaults
        if _TRADING_CRITERIA:
            weight = weight or _TRADING_CRITERIA['scoring_weights'].get('current_iv_level', 0.25)
            min_iv = min_iv or _TRADING_CRITERIA['iv_thresholds']['minimum']
            self.iv_excellent: float = _TRADING_CRITERIA['iv_thresholds']['excellent']
            self.iv_extreme: float = _TRADING_CRITERIA['iv_thresholds']['extreme']
        else:
            # Fallback to hardcoded defaults
            weight = weight or 0.25
            min_iv = min_iv or 60
            self.iv_excellent = 80
            self.iv_extreme = 100

        super().__init__(weight)
        self.min_iv: int = min_iv

    def score(self, data: TickerData) -> float:
        """Score based on absolute current IV percentage."""
        options_data = data.get('options_data', {})
        ticker = data.get('ticker', 'UNKNOWN')

        # Try actual IV % first (most reliable)
        current_iv = options_data.get('current_iv')
        if current_iv is not None and current_iv > 0:
            return self._score_from_current_iv(current_iv, ticker)

        # Fallback to yfinance IV estimate
        return self._score_from_yf_iv(data.get('iv', 0))

    def _score_from_current_iv(self, current_iv: float, ticker: str) -> float:
        """Score from actual IV percentage."""
        # HARD FILTER: Must be >= min_iv
        if current_iv < self.min_iv:
            logger.info(f"{ticker}: IV {current_iv}% < {self.min_iv}% - SKIPPING")
            return 0.0

        # Score based on absolute IV level
        if current_iv >= self.iv_extreme:  # Premium IV - exceptional (100%+)
            return 100.0
        elif current_iv >= self.iv_excellent:  # Excellent IV (80%+)
            # UPDATED: Anything >= 80% scores 100 (for 1-2 day pre-earnings entries)
            return 100.0
        else:  # min_iv to iv_excellent - good IV (60-80%)
            return 60.0 + (current_iv - self.min_iv) * 1.0

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


class IVExpansionScorer(TickerScorer):
    """
    Score based on recent IV expansion velocity (weekly % change).

    PRIMARY METRIC for 1-2 day pre-earnings entries - 35% weight

    Measures whether premium is BUILDING (good) or LEAKING (bad):
    - Weekly IV +80%+: Excellent expansion (score 100) - premium building fast!
    - Weekly IV +40-80%: Good expansion (score 80) - solid buildup
    - Weekly IV +20-40%: Moderate expansion (score 60) - some buildup
    - Weekly IV 0-20%: Weak expansion (score 40) - minimal buildup
    - Weekly IV negative: Premium leaking (score 0) - avoid!

    This answers: "Is NOW the right time to enter?" (tactical timing)
    Unlike IV Rank which answers: "Is this stock expensive vs history?" (structural)
    """

    def __init__(self, weight: Optional[float] = None, db_path: Optional[str] = None) -> None:
        # Load from config or use defaults
        if _TRADING_CRITERIA:
            weight = weight or _TRADING_CRITERIA['scoring_weights'].get('iv_expansion_velocity', 0.35)
            exp = _TRADING_CRITERIA.get('iv_expansion_thresholds', {})
            self.excellent: float = exp.get('excellent', 80)
            self.good: float = exp.get('good', 40)
            self.moderate: float = exp.get('moderate', 20)
            self.minimum: float = exp.get('minimum', 0)
        else:
            weight = weight or 0.35
            self.excellent = 80  # +80% weekly change
            self.good = 40       # +40% weekly change
            self.moderate = 20   # +20% weekly change
            self.minimum = 0     # Any positive change

        super().__init__(weight)
        self.db_path = db_path  # Optional database path for testing

    def score(self, data: TickerData) -> float:
        """Score based on recent IV percentage change (flexible 1-7 day lookback) with on-demand backfill."""
        from src.options.iv_history_tracker import IVHistoryTracker

        options_data = data.get('options_data', {})
        ticker = data.get('ticker', 'UNKNOWN')
        current_iv = options_data.get('current_iv')

        if current_iv is None or current_iv <= 0:
            # No current IV data - return conservative score (don't filter out)
            return 30.0

        # Calculate recent IV % change (uses most recent data in past 1-7 days)
        tracker = IVHistoryTracker(db_path=self.db_path) if self.db_path else IVHistoryTracker()
        try:
            # Try to get recent IV change (most recent data in past 1-7 days)
            weekly_change = tracker.get_recent_iv_change(ticker, current_iv, max_lookback_days=7)

            # Self-healing: Auto-backfill if no data available
            if weekly_change is None:
                logger.info(f"{ticker}: No weekly IV data, attempting recent backfill...")
                from src.options.iv_history_backfill import IVHistoryBackfill

                backfiller = IVHistoryBackfill(iv_tracker=tracker)
                # Backfill last 10 days (enough for 5-9 day lookback window)
                result = backfiller.backfill_recent(ticker, days=10)

                if result['success'] and result['data_points'] > 0:
                    # Retry calculation with backfilled data
                    weekly_change = tracker.get_weekly_iv_change(ticker, current_iv)
                    if weekly_change is not None:
                        logger.info(f"{ticker}: ✓ Backfilled {result['data_points']} points, weekly change = {weekly_change:+.1f}%")
                    else:
                        logger.warning(f"{ticker}: Backfill succeeded but still no weekly data (may need more history)")
                else:
                    logger.warning(f"{ticker}: Backfill failed: {result.get('message', 'Unknown error')}")
        finally:
            tracker.close()

        if weekly_change is None:
            # No historical data and backfill failed - heavily penalize (10.0 instead of 30.0)
            # This is PRIMARY METRIC (35% weight), so missing data should significantly hurt score
            # Helps filter out small caps and thinly-traded options with no IV history
            logger.debug(f"{ticker}: No weekly IV data available after backfill attempt - heavily penalizing score")
            return 10.0

        # Score based on IV expansion velocity
        if weekly_change >= self.excellent:  # +80%+ (e.g., 40% → 72%)
            return 100.0
        elif weekly_change >= self.good:  # +40-80% (e.g., 50% → 70%)
            return 80.0
        elif weekly_change >= self.moderate:  # +20-40% (e.g., 60% → 72%)
            return 60.0
        elif weekly_change >= self.minimum:  # 0-20% (weak buildup)
            return 40.0
        else:  # Negative (premium leaking)
            # CRITICAL: Don't completely filter out (return 0), just heavily penalize
            # Some stocks have slow IV buildup patterns
            return 20.0


class IVCrushEdgeScorer(TickerScorer):
    """
    Score based on IV crush edge (implied vs actual moves).

    30% weight
    - Measures if implied move historically > actual move
    - Ratio > 1.3: Excellent edge (score 100)
    - Ratio > 1.2: Good edge (score 80)
    - Ratio < 1.0: No edge (score 0)
    """

    def __init__(self, weight: Optional[float] = None) -> None:
        # Load from config or use defaults
        if _TRADING_CRITERIA:
            weight = weight or _TRADING_CRITERIA['scoring_weights']['iv_crush_edge']
            self.excellent: float = _TRADING_CRITERIA['iv_crush_thresholds']['excellent']
            self.better: float = _TRADING_CRITERIA['iv_crush_thresholds']['better']
            self.good: float = _TRADING_CRITERIA['iv_crush_thresholds']['good']
            self.minimum: float = _TRADING_CRITERIA['iv_crush_thresholds']['minimum']
        else:
            weight = weight or 0.30
            self.excellent = 1.3
            self.better = 1.2
            self.good = 1.1
            self.minimum = 1.0

        super().__init__(weight)

    def score(self, data: TickerData) -> float:
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

    def __init__(self, weight: Optional[float] = None) -> None:
        # Load from config or use defaults
        if _TRADING_CRITERIA:
            weight = weight or _TRADING_CRITERIA['scoring_weights']['options_liquidity']
            liq = _TRADING_CRITERIA['liquidity_thresholds']
            self.min_volume: int = liq.get('minimum_volume', 100)
            self.min_oi: int = liq.get('minimum_open_interest', 500)
            self.vol_very_high: int = liq['volume']['very_high']
            self.vol_high: int = liq['volume']['high']
            self.vol_good: int = liq['volume']['good']
            self.vol_acceptable: int = liq['volume']['acceptable']
            self.oi_very_liquid: int = liq['open_interest']['very_liquid']
            self.oi_liquid: int = liq['open_interest']['liquid']
            self.oi_good: int = liq['open_interest']['good']
            self.oi_acceptable: int = liq['open_interest']['acceptable']
            self.spread_excellent: float = liq['spread']['excellent']
            self.spread_good: float = liq['spread']['good']
            self.spread_okay: float = liq['spread']['okay']
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

    def score(self, data: TickerData) -> float:
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

    def _score_bid_ask_spread(self, spread_pct: Optional[float]) -> float:
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

    def __init__(self, weight: Optional[float] = None) -> None:
        # Load from config or use defaults
        if _TRADING_CRITERIA:
            weight = weight or _TRADING_CRITERIA['scoring_weights']['fundamentals']
            fund = _TRADING_CRITERIA['fundamentals']
            self.mega_cap: float = fund['market_cap']['mega_cap'] * 1e9
            self.large_cap: float = fund['market_cap']['large_cap'] * 1e9
            self.mid_cap: float = fund['market_cap']['mid_cap'] * 1e9
            self.price_min_ideal: float = fund['price']['min_ideal']
            self.price_max_ideal: float = fund['price']['max_ideal']
            self.price_min_acceptable: float = fund['price']['min_acceptable']
            self.price_max_acceptable: float = fund['price']['max_acceptable']
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

    def score(self, data: TickerData) -> float:
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
        scorers: Optional[List[TickerScorer]] = None,
        min_iv: Optional[int] = None
    ) -> None:
        """
        Initialize composite scorer.

        Args:
            scorers: List of TickerScorer instances (defaults to standard set)
            min_iv: Minimum IV percentage for filtering (loads from config if None)
        """
        if scorers is None:
            # NEW scoring strategy optimized for 1-2 day pre-earnings entries
            # Weights: Expansion 35%, Liquidity 30%, Crush Edge 25%, Current IV 25%, Fundamentals 5%
            # Total = 120% (will be normalized by individual weights)
            self.scorers: List[TickerScorer] = [
                IVExpansionScorer(),       # 35% - Is premium building NOW? (tactical timing)
                LiquidityScorer(),         # 30% - Can we execute efficiently? (critical)
                IVCrushEdgeScorer(),       # 25% - Historical crush edge (strategy fit)
                IVScorer(min_iv=min_iv),   # 25% - Absolute IV level (filter + sizing)
                FundamentalsScorer()       # 5% - Market cap/price (minor factor)
            ]
        else:
            self.scorers = scorers

    @memoize_with_dict_key(maxsize=256)
    def calculate_score(self, data: TickerData) -> float:
        """
        Calculate composite score for ticker with earnings proximity boost.

        Memoized to avoid recalculating scores for the same ticker data.
        Cache size: 256 tickers (typical daily analysis volume).

        Strategy: Enter 1-2 days before earnings, so prioritize imminent earnings.

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
            # IVScorer: IV < 60%
            # LiquidityScorer: Volume < 100 or OI < 500
            if score == 0 and isinstance(scorer, (IVScorer, LiquidityScorer)):
                return 0.0

            scores.append(score * scorer.weight)

        # Total weighted score
        total = sum(scores)

        # Normalize from 120% to 100% (weights sum to 120%)
        # This ensures scores don't exceed 100
        normalized_score = (total / 120.0) * 100.0

        # Apply earnings proximity boost (strategy: enter 1-2 days before)
        earnings_date = data.get('earnings_date')
        if earnings_date:
            from datetime import datetime
            try:
                earnings_dt = datetime.strptime(earnings_date, '%Y-%m-%d')
                days_until = (earnings_dt.date() - datetime.now().date()).days

                # Proximity multiplier based on urgency
                if days_until <= 0:
                    # Today or past - maximum urgency
                    proximity_boost = 1.15
                elif days_until <= 2:
                    # 1-2 days - high urgency (optimal entry window)
                    proximity_boost = 1.10
                elif days_until <= 5:
                    # 3-5 days - normal urgency
                    proximity_boost = 1.0
                elif days_until <= 10:
                    # 6-10 days - lower urgency
                    proximity_boost = 0.95
                else:
                    # 10+ days - too early, deprioritize
                    proximity_boost = 0.85

                normalized_score *= proximity_boost
            except (ValueError, TypeError):
                # Invalid date format, skip proximity boost
                pass

        return round(min(normalized_score, 100.0), 2)
