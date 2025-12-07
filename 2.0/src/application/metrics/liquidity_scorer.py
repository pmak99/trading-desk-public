"""
Liquidity Scoring System for Options.

Evaluates option liquidity quality using multiple factors:
- Open Interest (market size)
- Volume (current trading activity)
- Bid-Ask Spread (transaction cost)
- Depth (size at bid/ask)

Returns a composite liquidity score (0-100) and individual metrics.

Market Hours Awareness:
When markets are closed (weekends, holidays, after-hours), volume is always 0.
The scorer can operate in "OI-only" mode to avoid false REJECT classifications
during these periods. Use classify_straddle_tier_market_aware() for this behavior.

Dynamic Position Sizing:
Thresholds can be dynamically calculated based on position size and max loss budget.
The hybrid liquidity check evaluates strikes outside implied move (preferred) with
20-delta fallback for more accurate scan-stage liquidity assessment.
"""

import logging
from dataclasses import dataclass
from typing import Optional, Tuple, Dict, Any
from src.domain.types import OptionQuote, OptionChain, Money, Strike
from src.utils.market_hours import is_market_open, get_market_status

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
        # OI thresholds (4-tier)
        min_oi: int = 50,  # REJECT below this
        warning_oi: int = 100,  # WARNING tier threshold (1-2x position)
        good_oi: int = 500,  # GOOD tier threshold (2-5x position)
        excellent_oi: int = 1000,  # EXCELLENT tier threshold (>=5x position)
        # Volume thresholds
        min_volume: int = 20,
        good_volume: int = 100,
        excellent_volume: int = 250,
        # Spread thresholds (4-tier)
        max_spread_pct: float = 15.0,  # REJECT threshold - spread > 15%
        warning_spread_pct: float = 12.0,  # WARNING threshold - spread > 12%
        good_spread_pct: float = 8.0,  # GOOD threshold - spread > 8%
        excellent_spread_pct: float = 5.0,  # EXCELLENT threshold - spread <= 8%
    ):
        """
        Initialize liquidity scorer with 4-tier thresholds.

        4-Tier System:
        - EXCELLENT: OI >= excellent_oi, spread <= good_spread_pct (8%)
        - GOOD: OI >= good_oi, spread <= warning_spread_pct (12%)
        - WARNING: OI >= warning_oi, spread <= max_spread_pct (15%)
        - REJECT: Below WARNING thresholds

        Args:
            min_oi: Minimum OI for REJECT tier
            warning_oi: WARNING tier OI threshold
            good_oi: GOOD tier OI threshold
            excellent_oi: EXCELLENT tier OI threshold
            min_volume: Minimum acceptable volume
            good_volume: Good volume threshold
            excellent_volume: Excellent volume threshold
            max_spread_pct: REJECT threshold (spread > 15%)
            warning_spread_pct: WARNING threshold (spread > 12%)
            good_spread_pct: GOOD threshold (spread > 8%)
            excellent_spread_pct: EXCELLENT threshold (spread <= 8%)
        """
        self.min_oi = min_oi
        self.warning_oi = warning_oi
        self.good_oi = good_oi
        self.excellent_oi = excellent_oi
        self.min_volume = min_volume
        self.good_volume = good_volume
        self.excellent_volume = excellent_volume
        self.max_spread_pct = max_spread_pct
        self.warning_spread_pct = warning_spread_pct
        self.good_spread_pct = good_spread_pct
        self.excellent_spread_pct = excellent_spread_pct

        # Scoring weights
        self.oi_weight = 0.40
        self.volume_weight = 0.30
        self.spread_weight = 0.25
        self.depth_weight = 0.05

    def calculate_spread_pct(self, option: OptionQuote) -> float:
        """
        Calculate bid-ask spread as percentage of mid price.

        Args:
            option: Option quote with bid/ask data

        Returns:
            Spread percentage (0-100+), or 100.0 if no bid/ask available
        """
        if option.bid and option.ask:
            spread = float(option.ask.amount - option.bid.amount)
            mid = float(option.mid.amount)
            return (spread / mid * 100) if mid > 0 else 100.0
        return 100.0

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
        spread_pct = self.calculate_spread_pct(option)

        if option.bid and option.ask:
            spread = float(option.ask.amount - option.bid.amount)
            effective_spread = Money(spread)
        else:
            # No bid/ask available - use worst case
            effective_spread = Money(999.99)

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

        # Determine liquidity tier using 3-tier system (EXCELLENT/WARNING/REJECT)
        tier = self._classify_tier(oi, volume, spread_pct)

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

    def _classify_tier(self, oi: int, volume: int, spread_pct: float) -> str:
        """
        Classify liquidity into 4-tier system (EXCELLENT/GOOD/WARNING/REJECT).

        This is the single source of truth for tier classification across all modes.

        4-Tier System (from CLAUDE.md):
        - EXCELLENT: OI >= 5x position (excellent_oi), spread <= 8%
        - GOOD:      OI 2-5x position (good_oi to excellent_oi), spread 8-12%
        - WARNING:   OI 1-2x position (warning_oi to good_oi), spread 12-15%
        - REJECT:    OI < 1x position (below warning_oi), spread > 15%

        Final tier = worse of (OI tier, Spread tier)

        Args:
            oi: Open interest
            volume: Daily volume
            spread_pct: Bid-ask spread percentage

        Returns:
            "EXCELLENT", "GOOD", "WARNING", or "REJECT"
        """
        # Determine OI-based tier
        if oi < self.min_oi or volume < self.min_volume:
            oi_tier = "REJECT"
        elif oi < self.warning_oi:
            oi_tier = "REJECT"
        elif oi < self.good_oi:
            oi_tier = "WARNING"
        elif oi < self.excellent_oi:
            oi_tier = "GOOD"
        else:
            oi_tier = "EXCELLENT"

        # Determine spread-based tier
        if spread_pct > self.max_spread_pct:  # >15%
            spread_tier = "REJECT"
        elif spread_pct > self.warning_spread_pct:  # >12%
            spread_tier = "WARNING"
        elif spread_pct > self.good_spread_pct:  # >8%
            spread_tier = "GOOD"
        else:  # <=8%
            spread_tier = "EXCELLENT"

        # Final tier is the worse of the two
        tier_order = {"REJECT": 0, "WARNING": 1, "GOOD": 2, "EXCELLENT": 3}
        return min([oi_tier, spread_tier], key=lambda t: tier_order[t])

    def classify_option_tier(self, option: OptionQuote) -> str:
        """
        Classify a single option's liquidity tier (public method).

        Args:
            option: Option quote with market data

        Returns:
            "EXCELLENT", "GOOD", "WARNING", or "REJECT"
        """
        oi = option.open_interest or 0
        volume = option.volume or 0
        spread_pct = self.calculate_spread_pct(option)

        return self._classify_tier(oi, volume, spread_pct)

    def classify_straddle_tier(self, call: OptionQuote, put: OptionQuote) -> str:
        """
        Classify liquidity tier for a straddle (call + put).

        Uses the worse tier of the two legs to be conservative.

        Args:
            call: Call option quote
            put: Put option quote

        Returns:
            "EXCELLENT", "GOOD", "WARNING", or "REJECT"
        """
        call_tier = self.classify_option_tier(call)
        put_tier = self.classify_option_tier(put)

        # Final tier is the worse of the two
        tier_order = {"REJECT": 0, "WARNING": 1, "GOOD": 2, "EXCELLENT": 3}
        return min([call_tier, put_tier], key=lambda t: tier_order[t])

    def _classify_tier_oi_only(self, oi: int, spread_pct: float) -> str:
        """
        Classify liquidity tier using only OI and spread (ignoring volume).

        Used when markets are closed and volume is expected to be 0.
        This prevents false REJECT classifications during weekends/after-hours.

        4-Tier System (from CLAUDE.md):
        - EXCELLENT: OI >= excellent_oi, spread <= 8%
        - GOOD:      OI >= good_oi, spread <= 12%
        - WARNING:   OI >= warning_oi, spread <= 15%
        - REJECT:    OI < warning_oi or spread > 15%

        Final tier = worse of (OI tier, Spread tier)

        Args:
            oi: Open interest
            spread_pct: Bid-ask spread percentage

        Returns:
            "EXCELLENT", "GOOD", "WARNING", or "REJECT"
        """
        # Determine OI-based tier
        if oi < self.min_oi:
            oi_tier = "REJECT"
        elif oi < self.warning_oi:
            oi_tier = "REJECT"
        elif oi < self.good_oi:
            oi_tier = "WARNING"
        elif oi < self.excellent_oi:
            oi_tier = "GOOD"
        else:
            oi_tier = "EXCELLENT"

        # Determine spread-based tier
        if spread_pct > self.max_spread_pct:  # >15%
            spread_tier = "REJECT"
        elif spread_pct > self.warning_spread_pct:  # >12%
            spread_tier = "WARNING"
        elif spread_pct > self.good_spread_pct:  # >8%
            spread_tier = "GOOD"
        else:  # <=8%
            spread_tier = "EXCELLENT"

        # Final tier is the worse of the two
        tier_order = {"REJECT": 0, "WARNING": 1, "GOOD": 2, "EXCELLENT": 3}
        return min([oi_tier, spread_tier], key=lambda t: tier_order[t])

    def classify_option_tier_oi_only(self, option: OptionQuote) -> str:
        """
        Classify a single option's liquidity tier using OI-only mode.

        Used when markets are closed to avoid false rejections from volume=0.

        Args:
            option: Option quote with market data

        Returns:
            "EXCELLENT", "GOOD", "WARNING", or "REJECT"
        """
        oi = option.open_interest or 0
        spread_pct = self.calculate_spread_pct(option)
        return self._classify_tier_oi_only(oi, spread_pct)

    def classify_straddle_tier_market_aware(
        self, call: OptionQuote, put: OptionQuote
    ) -> Tuple[str, bool, str]:
        """
        Classify liquidity tier for a straddle with market-hours awareness.

        When markets are closed (weekends, holidays, after-hours), volume is
        expected to be 0. This method uses OI-only scoring in those cases to
        avoid false REJECT classifications.

        Args:
            call: Call option quote
            put: Put option quote

        Returns:
            Tuple of (tier, is_market_open, market_status_reason)
            - tier: "EXCELLENT", "GOOD", "WARNING", or "REJECT"
            - is_market_open: True if market is currently open
            - market_status_reason: e.g., "Weekend (Saturday)", "After Hours"
        """
        market_open, market_reason = get_market_status()

        if market_open:
            # Market is open - use full scoring with volume
            tier = self.classify_straddle_tier(call, put)
        else:
            # Market is closed - use OI-only scoring
            call_tier = self.classify_option_tier_oi_only(call)
            put_tier = self.classify_option_tier_oi_only(put)

            # Final tier is the worse of the two
            tier_order = {"REJECT": 0, "WARNING": 1, "GOOD": 2, "EXCELLENT": 3}
            tier = min([call_tier, put_tier], key=lambda t: tier_order[t])

        return (tier, market_open, market_reason)

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

    # ========================================================================
    # HYBRID LIQUIDITY CHECK (C-then-B approach with dynamic thresholds)
    # ========================================================================

    def calculate_dynamic_thresholds(
        self,
        stock_price: float,
        max_loss_budget: float = 20000.0,
        credit_ratio: float = 0.30,
    ) -> Dict[str, Any]:
        """
        Calculate dynamic OI thresholds based on position size for max loss budget.

        Spread width scales with stock price (~5-10% of stock) for realistic sizing:

        | Stock Price | Spread Width | % of Stock | Contracts for $20k |
        |-------------|--------------|------------|-------------------|
        | < $20       | $2.50        | 12-25%     | ~114              |
        | $20-$100    | $5.00        | 5-25%      | ~57               |
        | $100-$200   | $10.00       | 5-10%      | ~29               |
        | $200-$500   | $20.00       | 4-10%      | ~14               |
        | $500-$1000  | $50.00       | 5-10%      | ~6                |
        | $1000+      | $100.00      | 5-10%      | ~3                |

        Thresholds are set as multiples of position size:
        - REJECT: OI < 1x position size
        - WARNING: OI 1-2x position size
        - GOOD: OI 2-5x position size
        - EXCELLENT: OI >= 5x position size

        Args:
            stock_price: Current stock price
            max_loss_budget: Maximum loss budget (default $20,000)
            credit_ratio: Estimated credit as fraction of spread width (default 30%)

        Returns:
            Dict with:
            - spread_width: Spread width based on stock price tier
            - contracts: Number of contracts for max loss
            - min_oi: REJECT threshold (1x)
            - warning_oi: WARNING->GOOD threshold (2x)
            - good_oi: GOOD->EXCELLENT threshold (5x)
            - price_tier: Description of the price tier used
        """
        # Determine spread width based on stock price tier
        # Goal: Keep spread at ~5-10% of stock price with standard strike increments
        #
        # | Stock Price | Spread Width | % of Stock | Contracts for $20k |
        # |-------------|--------------|------------|-------------------|
        # | < $20       | $2.50        | 12-25%     | ~114              |
        # | $20-$50     | $5.00        | 10-25%     | ~57               |
        # | $50-$100    | $5.00        | 5-10%      | ~57               |
        # | $100-$200   | $10.00       | 5-10%      | ~29               |
        # | $200-$500   | $20.00       | 4-10%      | ~14               |
        # | $500-$1000  | $50.00       | 5-10%      | ~6                |
        # | $1000+      | $100.00      | 5-10%      | ~3                |
        #
        if stock_price >= 1000:
            spread_width = 100.0
            price_tier = "$1000+"
        elif stock_price >= 500:
            spread_width = 50.0
            price_tier = "$500-1000"
        elif stock_price >= 200:
            spread_width = 20.0
            price_tier = "$200-500"
        elif stock_price >= 100:
            spread_width = 10.0
            price_tier = "$100-200"
        elif stock_price >= 20:
            spread_width = 5.0
            price_tier = "$20-100"
        else:
            spread_width = 2.50
            price_tier = "<$20"

        # Calculate max loss per spread
        credit_estimate = spread_width * credit_ratio
        max_loss_per_spread = (spread_width - credit_estimate) * 100  # Per contract

        # Calculate contracts needed for max loss budget
        contracts = int(max_loss_budget / max_loss_per_spread)

        # Dynamic thresholds as multiples of position size
        min_oi = contracts * 1       # REJECT below 1x
        warning_oi = contracts * 2   # WARNING 1-2x
        good_oi = contracts * 5      # GOOD 2-5x, EXCELLENT at 5x+

        return {
            'spread_width': spread_width,
            'contracts': contracts,
            'min_oi': min_oi,
            'warning_oi': warning_oi,
            'good_oi': good_oi,
            'max_loss_budget': max_loss_budget,
            'price_tier': price_tier,
        }

    def _find_strike_outside_move(
        self,
        chain: OptionChain,
        implied_move_pct: float,
        is_call: bool,
    ) -> Optional[Tuple[Strike, OptionQuote]]:
        """
        Find the first strike just outside the implied move range.

        For calls: Find strike just above (stock_price * (1 + implied_move))
        For puts: Find strike just below (stock_price * (1 - implied_move))

        Args:
            chain: Option chain with calls and puts
            implied_move_pct: Implied move as percentage (e.g., 8.5 for 8.5%)
            is_call: True for call side, False for put side

        Returns:
            Tuple of (Strike, OptionQuote) at strike just outside implied move, or None if not found
        """
        stock_price = float(chain.stock_price.amount)
        move_decimal = implied_move_pct / 100.0

        if is_call:
            # Upper bound: stock * (1 + move)
            target_strike = stock_price * (1 + move_decimal)
            strikes = sorted(chain.calls.keys(), key=lambda s: float(s.price))
            # Find first strike >= target
            for strike in strikes:
                if float(strike.price) >= target_strike:
                    option = chain.calls.get(strike)
                    if option:
                        return (strike, option)
        else:
            # Lower bound: stock * (1 - move)
            target_strike = stock_price * (1 - move_decimal)
            strikes = sorted(chain.puts.keys(), key=lambda s: float(s.price), reverse=True)
            # Find first strike <= target (going down from ATM)
            for strike in strikes:
                if float(strike.price) <= target_strike:
                    option = chain.puts.get(strike)
                    if option:
                        return (strike, option)

        return None

    def _find_delta_strike(
        self,
        chain: OptionChain,
        target_delta: float,
        is_call: bool,
    ) -> Optional[Tuple[Strike, OptionQuote]]:
        """
        Find the option closest to target delta.

        For calls: Look for delta close to +target_delta (e.g., +0.20)
        For puts: Look for delta close to -target_delta (e.g., -0.20)

        Args:
            chain: Option chain with calls and puts
            target_delta: Target absolute delta (e.g., 0.20 for 20-delta)
            is_call: True for call side, False for put side

        Returns:
            Tuple of (Strike, OptionQuote) closest to target delta, or None if not found
        """
        options = chain.calls if is_call else chain.puts

        best_result = None
        best_delta_diff = float('inf')

        for strike, option in options.items():
            if option.delta is None:
                continue

            delta = abs(float(option.delta))
            delta_diff = abs(delta - target_delta)

            if delta_diff < best_delta_diff:
                best_delta_diff = delta_diff
                best_result = (strike, option)

        return best_result

    def classify_hybrid_tier(
        self,
        chain: OptionChain,
        implied_move_pct: float,
        stock_price: Optional[float] = None,
        max_loss_budget: float = 20000.0,
        use_dynamic_thresholds: bool = True,
    ) -> Tuple[str, Dict[str, Any]]:
        """
        Hybrid liquidity tier classification using C-then-B approach.

        Method C: Check strikes just outside implied move (preferred)
        Method B: Fall back to 20-delta strikes if C fails or finds dead strikes

        Uses dynamic thresholds based on position size when enabled.

        Args:
            chain: Option chain with calls and puts
            implied_move_pct: Implied move as percentage
            stock_price: Current stock price (derived from chain if not provided)
            max_loss_budget: Maximum loss budget for dynamic thresholds
            use_dynamic_thresholds: Whether to use dynamic or static thresholds

        Returns:
            Tuple of (tier, details_dict)
            - tier: "EXCELLENT", "GOOD", "WARNING", or "REJECT"
            - details_dict: Contains method used, strikes checked, OI values, etc.
        """
        if stock_price is None:
            stock_price = float(chain.stock_price.amount)

        # Calculate dynamic thresholds if enabled
        if use_dynamic_thresholds:
            thresholds = self.calculate_dynamic_thresholds(stock_price, max_loss_budget)
            min_oi = thresholds['min_oi']
            warning_oi = thresholds['warning_oi']
            good_oi = thresholds['good_oi']
        else:
            min_oi = self.min_oi
            warning_oi = self.min_oi * 2
            good_oi = self.excellent_oi
            thresholds = {'contracts': 'N/A', 'spread_width': 'N/A'}

        market_open, market_reason = get_market_status()

        details = {
            'method': None,
            'call_strike': None,
            'put_strike': None,
            'call_oi': 0,
            'put_oi': 0,
            'min_oi': 0,
            'call_spread_pct': 100.0,
            'put_spread_pct': 100.0,
            'market_open': market_open,
            'market_reason': market_reason,
            'thresholds': thresholds,
            'fallback_used': False,
        }

        # Method C: Strikes just outside implied move
        call_c_result = self._find_strike_outside_move(chain, implied_move_pct, is_call=True)
        put_c_result = self._find_strike_outside_move(chain, implied_move_pct, is_call=False)

        # Check if Method C found valid options with OI
        c_valid = (
            call_c_result is not None and
            put_c_result is not None and
            (call_c_result[1].open_interest or 0) > 0 and
            (put_c_result[1].open_interest or 0) > 0
        )

        if c_valid:
            call_strike, call_option = call_c_result
            put_strike, put_option = put_c_result
            details['method'] = 'C (outside implied move)'
        else:
            # Method B: Fall back to 20-delta strikes
            call_b_result = self._find_delta_strike(chain, target_delta=0.20, is_call=True)
            put_b_result = self._find_delta_strike(chain, target_delta=0.20, is_call=False)

            if call_b_result is not None and put_b_result is not None:
                call_strike, call_option = call_b_result
                put_strike, put_option = put_b_result
                details['method'] = 'B (20-delta fallback)'
                details['fallback_used'] = True
            else:
                # Neither method found valid options
                details['method'] = 'FAILED'
                return ("REJECT", details)

        # Extract metrics
        call_oi = call_option.open_interest or 0
        put_oi = put_option.open_interest or 0
        min_oi_found = min(call_oi, put_oi)

        call_spread_pct = self.calculate_spread_pct(call_option)
        put_spread_pct = self.calculate_spread_pct(put_option)
        max_spread = max(call_spread_pct, put_spread_pct)

        # Populate details
        details['call_strike'] = float(call_strike.price)
        details['put_strike'] = float(put_strike.price)
        details['call_oi'] = call_oi
        details['put_oi'] = put_oi
        details['min_oi'] = min_oi_found
        details['call_spread_pct'] = call_spread_pct
        details['put_spread_pct'] = put_spread_pct
        details['oi_ratio'] = min_oi_found / thresholds['contracts'] if thresholds['contracts'] != 'N/A' and thresholds['contracts'] > 0 else None

        # Classify tier using dynamic thresholds
        # OI Tiers: REJECT (<1x), WARNING (1-2x), GOOD (2-5x), EXCELLENT (>=5x)
        # Spread Tiers: REJECT (>15%), WARNING (>12%), GOOD (>8%), EXCELLENT (<=8%)
        #
        # Final tier = worse of (OI tier, Spread tier)

        # Determine OI-based tier
        if min_oi_found < min_oi:
            oi_tier = "REJECT"
        elif min_oi_found < warning_oi:
            oi_tier = "WARNING"
        elif min_oi_found < good_oi:
            oi_tier = "GOOD"
        else:
            oi_tier = "EXCELLENT"

        # Determine spread-based tier
        # Spread Tiers: REJECT (>15%), WARNING (>12%), GOOD (>8%), EXCELLENT (<=8%)
        if max_spread > self.max_spread_pct:  # >15%
            spread_tier = "REJECT"
        elif max_spread > self.warning_spread_pct:  # >12%
            spread_tier = "WARNING"
        elif max_spread > self.good_spread_pct:  # >8%
            spread_tier = "GOOD"
        else:  # <=8%
            spread_tier = "EXCELLENT"

        # Final tier is the worse of the two
        tier_order = {"REJECT": 0, "WARNING": 1, "GOOD": 2, "EXCELLENT": 3}
        tier = min([oi_tier, spread_tier], key=lambda t: tier_order[t])

        # Add tier breakdown to details for debugging
        details['oi_tier'] = oi_tier
        details['spread_tier'] = spread_tier

        return (tier, details)

    def classify_hybrid_tier_market_aware(
        self,
        chain: OptionChain,
        implied_move_pct: float,
        stock_price: Optional[float] = None,
        max_loss_budget: float = 20000.0,
        use_dynamic_thresholds: bool = True,
    ) -> Tuple[str, bool, str, Dict[str, Any]]:
        """
        Hybrid liquidity tier classification with market-hours awareness.

        Combines C-then-B approach with market hours detection.

        Args:
            chain: Option chain with calls and puts
            implied_move_pct: Implied move as percentage
            stock_price: Current stock price
            max_loss_budget: Maximum loss budget for dynamic thresholds
            use_dynamic_thresholds: Whether to use dynamic thresholds

        Returns:
            Tuple of (tier, is_market_open, market_reason, details_dict)
        """
        tier, details = self.classify_hybrid_tier(
            chain=chain,
            implied_move_pct=implied_move_pct,
            stock_price=stock_price,
            max_loss_budget=max_loss_budget,
            use_dynamic_thresholds=use_dynamic_thresholds,
        )

        return (tier, details['market_open'], details['market_reason'], details)
