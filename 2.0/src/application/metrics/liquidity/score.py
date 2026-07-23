"""Data types for liquidity scoring: LiquidityScore output DTO and LiquidityThresholds config."""
from dataclasses import dataclass
from typing import Optional
from src.domain.types import Money


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
        open_interest: Open interest
        volume: Daily volume
        bid_ask_spread_pct: Bid-ask spread as % of mid
        effective_spread: Dollar bid-ask spread
        is_liquid: Whether option meets minimum liquidity standards
        liquidity_tier: Tier classification (EXCELLENT/GOOD/WARNING/REJECT)
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


@dataclass(frozen=True)
class LiquidityThresholds:
    """Threshold config passed to all liquidity module functions. Defaults match LiquidityScorer.__init__."""
    # OI thresholds
    min_oi: int = 10
    warning_oi: int = 50
    good_oi: int = 100
    excellent_oi: int = 200
    # Volume thresholds
    min_volume: int = 0
    good_volume: int = 100
    excellent_volume: int = 250
    # Spread thresholds
    max_spread_pct: float = 25.0
    warning_spread_pct: float = 18.0
    good_spread_pct: float = 12.0
    excellent_spread_pct: float = 12.0
    # Scoring weights
    oi_weight: float = 0.40
    volume_weight: float = 0.30
    spread_weight: float = 0.25
    depth_weight: float = 0.05
