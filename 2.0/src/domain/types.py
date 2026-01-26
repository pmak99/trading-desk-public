"""
Domain types for IV Crush 2.0 system.

All types are immutable (frozen dataclasses) to ensure thread safety
and prevent accidental mutations.
"""

from dataclasses import dataclass
from decimal import Decimal, getcontext
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from typing import Dict, Optional, List
from src.domain.enums import (
    EarningsTiming,
    OptionType,
    Recommendation,
    ExpirationCycle,
    SettlementType,
    StrategyType,
    DirectionalBias
)

# Set Decimal precision for financial calculations
# 28 digits is standard for financial calculations, preventing precision loss
getcontext().prec = 28

# Percentage validation constants
MIN_PERCENTAGE = -100.0  # Allow -100% for complete losses
MAX_PERCENTAGE = 1000.0  # Allow up to 1000% for extreme gains

# Response size limits (bytes)
MAX_API_RESPONSE_SIZE = 10 * 1024 * 1024  # 10MB to prevent OOM attacks


# ============================================================================
# Value Objects (Primitives)
# ============================================================================


@dataclass(frozen=True)
class Money:
    """
    Monetary amount using Decimal for precision.
    Immutable and safe for financial calculations.
    """

    amount: Decimal

    def __init__(self, amount: float | Decimal | str):
        object.__setattr__(self, 'amount', Decimal(str(amount)))

    def __add__(self, other: 'Money') -> 'Money':
        return Money(self.amount + other.amount)

    def __sub__(self, other: 'Money') -> 'Money':
        return Money(self.amount - other.amount)

    def __mul__(self, scalar: float | Decimal) -> 'Money':
        return Money(self.amount * Decimal(str(scalar)))

    def __truediv__(self, scalar: float | Decimal) -> 'Money':
        return Money(self.amount / Decimal(str(scalar)))

    def __lt__(self, other: 'Money') -> bool:
        return self.amount < other.amount

    def __le__(self, other: 'Money') -> bool:
        return self.amount <= other.amount

    def __gt__(self, other: 'Money') -> bool:
        return self.amount > other.amount

    def __ge__(self, other: 'Money') -> bool:
        return self.amount >= other.amount

    def __str__(self) -> str:
        return f"${self.amount:.2f}"


@dataclass(frozen=True)
class Percentage:
    """
    Percentage value with validation.
    Stored as float (e.g., 5.0 means 5%).
    """

    value: float

    def __init__(self, value: float):
        if value < MIN_PERCENTAGE or value > MAX_PERCENTAGE:
            raise ValueError(
                f"Invalid percentage: {value}. "
                f"Must be between {MIN_PERCENTAGE}% and {MAX_PERCENTAGE}%"
            )
        object.__setattr__(self, 'value', float(value))

    def to_decimal(self) -> Decimal:
        """Convert to decimal multiplier (e.g., 5.0% -> 0.05)."""
        return Decimal(str(self.value / 100))

    def __str__(self) -> str:
        return f"{self.value:.2f}%"


@dataclass(frozen=True)
class Strike:
    """Option strike price."""

    price: Decimal

    def __init__(self, price: float | Decimal | str):
        object.__setattr__(self, 'price', Decimal(str(price)))

    def __hash__(self):
        return hash(self.price)

    def __eq__(self, other):
        return isinstance(other, Strike) and self.price == other.price

    def __lt__(self, other: 'Strike') -> bool:
        return self.price < other.price

    def __str__(self) -> str:
        return f"${self.price:.2f}"


# ============================================================================
# Options Data Structures
# ============================================================================


@dataclass(frozen=True)
class OptionQuote:
    """
    Single option quote with bid, ask, greeks, and volume.
    """

    bid: Money
    ask: Money
    implied_volatility: Optional[Percentage] = None
    open_interest: int = 0
    volume: int = 0

    # Greeks (optional, Phase 2 enhancement)
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None

    @property
    def mid(self) -> Money:
        """Mid-point price."""
        return Money((self.bid.amount + self.ask.amount) / 2)

    @property
    def spread(self) -> Money:
        """Bid-ask spread."""
        return Money(self.ask.amount - self.bid.amount)

    @property
    def spread_pct(self) -> float:
        """Spread as percentage of mid price."""
        if not self.bid or not self.ask or self.bid.amount <= 0 or self.ask.amount <= 0:
            return 100.0
        if self.mid.amount == 0:
            return 100.0
        return float((self.ask.amount - self.bid.amount) / self.mid.amount * 100)

    @property
    def is_liquid(self) -> bool:
        """Basic liquidity check."""
        return (
            self.open_interest > 0
            and self.volume > 0  # Must have actual volume, not just >= 0
            and self.spread_pct < 50.0
            and self.bid.amount > 0
        )


@dataclass(frozen=True)
class OptionChain:
    """
    Complete option chain for a ticker and expiration.
    Provides efficient lookups and ATM strike calculation.
    """

    ticker: str
    expiration: date
    stock_price: Money
    calls: Dict[Strike, OptionQuote]
    puts: Dict[Strike, OptionQuote]

    @property
    def strikes(self) -> List[Strike]:
        """All strikes sorted by price."""
        return sorted(set(self.calls.keys()) | set(self.puts.keys()))

    def atm_strike(self) -> Strike:
        """
        Find closest at-the-money strike using binary search.
        Returns the strike closest to current stock price.
        """
        import bisect

        if not self.strikes:
            raise ValueError("No strikes available")

        strikes_sorted = sorted(self.strikes, key=lambda s: float(s.price))
        prices = [float(s.price) for s in strikes_sorted]
        stock_px = float(self.stock_price.amount)

        idx = bisect.bisect_left(prices, stock_px)

        if idx == 0:
            return strikes_sorted[0]
        if idx == len(strikes_sorted):
            return strikes_sorted[-1]

        # Return closest strike
        prev_strike = strikes_sorted[idx - 1]
        curr_strike = strikes_sorted[idx]

        prev_diff = abs(float(prev_strike.price) - stock_px)
        curr_diff = abs(float(curr_strike.price) - stock_px)

        return prev_strike if prev_diff < curr_diff else curr_strike

    def get_straddle(self, strike: Strike) -> tuple[OptionQuote, OptionQuote]:
        """Get call and put for a straddle at given strike."""
        if strike not in self.calls:
            raise ValueError(f"Strike {strike} not in calls")
        if strike not in self.puts:
            raise ValueError(f"Strike {strike} not in puts")

        return self.calls[strike], self.puts[strike]

    def strikes_near_atm(self, percent_range: float = 10.0) -> List[Strike]:
        """Get strikes within +/- percent of stock price."""
        stock_px = float(self.stock_price.amount)
        lower = stock_px * (1 - percent_range / 100)
        upper = stock_px * (1 + percent_range / 100)

        return [
            strike for strike in self.strikes
            if lower <= float(strike.price) <= upper
        ]


# ============================================================================
# Analysis Results
# ============================================================================


@dataclass(frozen=True)
class ImpliedMove:
    """
    Implied move calculation from ATM straddle.
    Represents market's expectation of price movement.
    """

    ticker: str
    expiration: date
    stock_price: Money
    atm_strike: Strike
    straddle_cost: Money
    implied_move_pct: Percentage
    upper_bound: Money
    lower_bound: Money

    # Phase 2 enhancements
    call_iv: Optional[Percentage] = None
    put_iv: Optional[Percentage] = None
    avg_iv: Optional[Percentage] = None


@dataclass(frozen=True)
class HistoricalMove:
    """
    Historical price movement around an earnings event.
    Tracks gap, intraday, and close-to-close moves.
    """

    ticker: str
    earnings_date: date
    prev_close: Money
    earnings_open: Money
    earnings_high: Money
    earnings_low: Money
    earnings_close: Money

    # Core metrics
    intraday_move_pct: Percentage  # high-low range
    gap_move_pct: Percentage        # open vs prev_close
    close_move_pct: Percentage      # close vs prev_close

    # Phase 2 enhancements
    volume_before: Optional[int] = None
    volume_earnings: Optional[int] = None

    @property
    def volume_ratio(self) -> Optional[float]:
        """Volume spike ratio (earnings day vs. normal)."""
        if self.volume_before and self.volume_earnings and self.volume_before > 0:
            return self.volume_earnings / self.volume_before
        return None


@dataclass(frozen=True)
class VRPResult:
    """
    Volatility Risk Premium analysis result.
    Compares implied move to historical mean move.
    """

    ticker: str
    expiration: date
    implied_move_pct: Percentage
    historical_mean_move_pct: Percentage
    vrp_ratio: float  # implied / historical
    edge_score: float  # risk-adjusted edge (Sharpe-like)
    recommendation: Recommendation

    @property
    def is_tradeable(self) -> bool:
        """Whether this opportunity meets minimum threshold."""
        return self.recommendation in [Recommendation.EXCELLENT, Recommendation.GOOD]


@dataclass(frozen=True)
class ConsistencyResult:
    """
    Historical move consistency analysis.
    Uses MAD (Median Absolute Deviation) for robustness.
    """

    ticker: str
    mean_move_pct: float
    median_move_pct: float
    mad: float  # Median Absolute Deviation
    consistency_score: float  # 1 - (MAD / median), higher is better
    sample_size: int


@dataclass(frozen=True)
class SkewResult:
    """
    Put-call IV skew analysis.
    Detects directional bias and volatility smile/smirk.
    """

    ticker: str
    expiration: date
    skew_atm: float  # Put IV - Call IV at ATM
    skew_strength: str  # 'strong', 'moderate', 'weak'
    direction: str  # 'bullish', 'bearish', 'neutral'


@dataclass(frozen=True)
class TermStructureResult:
    """
    IV term structure analysis across expirations.
    Detects backwardation (short-term IV > long-term IV).
    """

    ticker: str
    expirations: List[date]
    ivs: List[Percentage]
    slope: float  # Positive = contango, Negative = backwardation
    is_backwardation: bool


# ============================================================================
# Composite Analysis
# ============================================================================


@dataclass(frozen=True)
class TickerAnalysis:
    """
    Complete analysis for a ticker earnings event.
    Combines all metrics into a single recommendation.
    """

    ticker: str
    earnings_date: date
    earnings_timing: EarningsTiming
    entry_time: datetime
    expiration: date

    # Core metrics
    implied_move: ImpliedMove
    vrp: VRPResult

    # Enrichment metrics (optional)
    consistency: Optional[ConsistencyResult] = None
    skew: Optional[SkewResult] = None
    term_structure: Optional[TermStructureResult] = None

    # Strategy recommendations (optional)
    strategies: Optional['StrategyRecommendation'] = None

    # Overall recommendation
    recommendation: Recommendation = Recommendation.SKIP
    confidence: Optional[float] = None  # 0.0 - 1.0

    @property
    def is_excellent(self) -> bool:
        """Excellent opportunity (VRP >= 2.0x)."""
        return self.recommendation == Recommendation.EXCELLENT

    @property
    def is_tradeable(self) -> bool:
        """Meets minimum tradeable threshold."""
        return self.recommendation in [Recommendation.EXCELLENT, Recommendation.GOOD]


# ============================================================================
# Strategy Types (Quantitative Strategy Generation)
# ============================================================================


@dataclass(frozen=True)
class StrategyLeg:
    """
    Single leg of an options strategy.
    Represents one option position (long or short).
    """

    strike: Strike
    option_type: OptionType  # CALL or PUT
    action: str  # "BUY" or "SELL"
    contracts: int
    premium: Money  # Price per contract

    @property
    def is_long(self) -> bool:
        """Whether this is a long position (buying)."""
        return self.action == "BUY"

    @property
    def is_short(self) -> bool:
        """Whether this is a short position (selling)."""
        return self.action == "SELL"

    @property
    def cost(self) -> Money:
        """Total cost/credit for this leg."""
        total = self.premium * self.contracts * 100  # 100 shares per contract
        return total if self.is_long else Money(-total.amount)


@dataclass
class Strategy:
    """
    Complete options strategy with all legs and calculated metrics.

    Supports vertical spreads (bull put, bear call) and iron condors.

    Note: Not frozen to allow score updates during strategy ranking.
    """

    ticker: str
    strategy_type: StrategyType
    expiration: date
    legs: List[StrategyLeg]
    stock_price: Money  # Current stock price (for profit zone calculations)

    # Calculated metrics
    net_credit: Money  # Positive for credit spreads
    max_profit: Money
    max_loss: Money
    breakeven: List[Money]  # Can have 1-2 breakevens
    probability_of_profit: float  # 0.0 - 1.0
    reward_risk_ratio: float  # max_profit / max_loss

    # Position sizing
    contracts: int  # Number of spreads for $20K risk budget
    capital_required: Money  # Total capital at risk

    # Commission and fees
    commission_per_contract: float  # Commission per contract (e.g., $0.30)
    total_commission: Money  # Total commission for all legs
    net_profit_after_fees: Money  # max_profit - total_commission

    # Scoring
    profitability_score: float  # 0-100
    risk_score: float  # 0-100 (lower is safer)
    overall_score: float  # 0-100 (composite)

    # Supporting rationale
    rationale: str  # Brief explanation of edge

    # Position Greeks (aggregated across all legs)
    position_delta: Optional[float] = None  # Net delta exposure
    position_gamma: Optional[float] = None  # Net gamma exposure
    position_theta: Optional[float] = None  # Net theta (daily P/L from decay)
    position_vega: Optional[float] = None   # Net vega (IV sensitivity)

    # Liquidity metrics (POST-LOSS ANALYSIS - Added Nov 2025)
    liquidity_tier: Optional[str] = None  # "EXCELLENT", "WARNING", or "REJECT"
    min_open_interest: Optional[int] = None  # Minimum OI across all legs
    max_spread_pct: Optional[float] = None  # Maximum bid-ask spread % across all legs
    min_volume: Optional[int] = None  # Minimum volume across all legs

    @property
    def strike_description(self) -> str:
        """Human-readable strike description."""
        if self.strategy_type == StrategyType.BULL_PUT_SPREAD:
            short = next(leg for leg in self.legs if leg.is_short and leg.option_type == OptionType.PUT)
            long = next(leg for leg in self.legs if leg.is_long and leg.option_type == OptionType.PUT)
            return f"Short {short.strike}P / Long {long.strike}P"

        elif self.strategy_type == StrategyType.BEAR_CALL_SPREAD:
            short = next(leg for leg in self.legs if leg.is_short and leg.option_type == OptionType.CALL)
            long = next(leg for leg in self.legs if leg.is_long and leg.option_type == OptionType.CALL)
            return f"Short {short.strike}C / Long {long.strike}C"

        elif self.strategy_type == StrategyType.IRON_CONDOR:
            put_short = next(leg for leg in self.legs if leg.is_short and leg.option_type == OptionType.PUT)
            put_long = next(leg for leg in self.legs if leg.is_long and leg.option_type == OptionType.PUT)
            call_short = next(leg for leg in self.legs if leg.is_short and leg.option_type == OptionType.CALL)
            call_long = next(leg for leg in self.legs if leg.is_long and leg.option_type == OptionType.CALL)
            return f"Put: {put_short.strike}/{put_long.strike} | Call: {call_short.strike}/{call_long.strike}"

        elif self.strategy_type == StrategyType.IRON_BUTTERFLY:
            # Get ATM strikes (short positions)
            atm_call = next(leg for leg in self.legs if leg.is_short and leg.option_type == OptionType.CALL)
            atm_put = next(leg for leg in self.legs if leg.is_short and leg.option_type == OptionType.PUT)
            # Get wing strikes (long positions)
            wing_call = next(leg for leg in self.legs if leg.is_long and leg.option_type == OptionType.CALL)
            wing_put = next(leg for leg in self.legs if leg.is_long and leg.option_type == OptionType.PUT)
            return f"ATM: {atm_call.strike}C/{atm_put.strike}P | Wings: {wing_put.strike}P/{wing_call.strike}C"

        return "N/A"

    @property
    def is_defined_risk(self) -> bool:
        """Whether this strategy has defined/limited risk."""
        return True  # All our strategies (spreads) have defined risk


@dataclass(frozen=True)
class StrategyRecommendation:
    """
    Complete strategy recommendation with 2-3 ranked options.
    Generated from VRP analysis and options chain data.
    """

    ticker: str
    expiration: date
    analysis_time: datetime

    # Input context
    stock_price: Money
    implied_move_pct: Percentage
    vrp_ratio: float
    directional_bias: DirectionalBias

    # Generated strategies (2-3 options)
    strategies: List[Strategy]

    # Best recommendation
    recommended_index: int  # Index in strategies list
    recommendation_rationale: str  # Why this one is best

    @property
    def recommended_strategy(self) -> Strategy:
        """Get the recommended strategy."""
        return self.strategies[self.recommended_index]

    @property
    def has_multiple_options(self) -> bool:
        """Whether multiple strategy options were generated."""
        return len(self.strategies) > 1


# ============================================================================
# Timezone Helpers (Phase 2)
# ============================================================================

MARKET_TZ = ZoneInfo("America/New_York")


def market_now() -> datetime:
    """Get current time in market timezone."""
    return datetime.now(tz=MARKET_TZ)


def to_market_time(dt: datetime) -> datetime:
    """Convert any datetime to market timezone."""
    return dt.astimezone(MARKET_TZ)


def utc_now() -> datetime:
    """Get current time in UTC."""
    return datetime.now(tz=timezone.utc)
