"""
Liquidity analysis module for options trading.

Implements a 3-tier liquidity classification system to prevent trading
illiquid options that can lead to poor fills and excessive slippage.

Post-Loss Analysis (Nov 2025): This module was created after -$25K loss
where WDAY and ZS were flagged for insufficient liquidity but traded anyway.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional
from src.config.config import ThresholdsConfig
from src.domain.types import OptionQuote, Strike


class LiquidityTier(Enum):
    """
    Three-tier liquidity classification.

    REJECT: Auto-reject, should NEVER be traded
    WARNING: Tradeable but risky, discouraged
    EXCELLENT: High liquidity, preferred for trading
    """
    REJECT = "REJECT"
    WARNING = "WARNING"
    EXCELLENT = "EXCELLENT"


@dataclass(frozen=True)
class LiquidityMetrics:
    """
    Detailed liquidity metrics for an option contract.

    Attributes:
        open_interest: Total open contracts
        volume: Daily trading volume
        bid_ask_spread_pct: Bid-ask spread as % of mid price
        bid: Bid price
        ask: Ask price
        mid: Mid price
        has_valid_quotes: Whether bid/ask are valid (> 0)
    """
    open_interest: int
    volume: int
    bid_ask_spread_pct: float
    bid: float
    ask: float
    mid: float
    has_valid_quotes: bool

    @classmethod
    def from_option_quote(cls, quote: OptionQuote) -> 'LiquidityMetrics':
        """Create LiquidityMetrics from an OptionQuote."""
        return cls(
            open_interest=quote.open_interest,
            volume=quote.volume,
            bid_ask_spread_pct=quote.spread_pct,
            bid=quote.bid.amount,
            ask=quote.ask.amount,
            mid=quote.mid.amount,
            has_valid_quotes=(quote.bid.amount > 0 and quote.ask.amount > 0)
        )


@dataclass(frozen=True)
class LiquidityAnalysis:
    """
    Complete liquidity analysis for a set of options.

    Attributes:
        tier: Overall liquidity tier (worst of all analyzed options)
        short_strike_metrics: Metrics for the short strike option
        long_strike_metrics: Metrics for the long strike option (if spread)
        rejection_reasons: List of reasons if tier is REJECT
        warning_reasons: List of reasons if tier is WARNING
    """
    tier: LiquidityTier
    short_strike_metrics: Optional[LiquidityMetrics]
    long_strike_metrics: Optional[LiquidityMetrics]
    rejection_reasons: list[str]
    warning_reasons: list[str]

    def is_tradeable(self) -> bool:
        """Returns True if not in REJECT tier."""
        return self.tier != LiquidityTier.REJECT

    def has_warnings(self) -> bool:
        """Returns True if there are any warnings."""
        return len(self.warning_reasons) > 0 or self.tier == LiquidityTier.WARNING


def classify_liquidity(
    metrics: LiquidityMetrics,
    thresholds: ThresholdsConfig
) -> tuple[LiquidityTier, list[str], list[str]]:
    """
    Classify a single option's liquidity into a tier.

    Args:
        metrics: Liquidity metrics for the option
        thresholds: Configuration thresholds

    Returns:
        Tuple of (tier, rejection_reasons, warning_reasons)
    """
    rejection_reasons = []
    warning_reasons = []

    # Check REJECT tier (absolute minimums)
    if metrics.open_interest < thresholds.liquidity_reject_min_oi:
        rejection_reasons.append(f"OI {metrics.open_interest} < {thresholds.liquidity_reject_min_oi}")

    if metrics.bid_ask_spread_pct > thresholds.liquidity_reject_max_spread_pct:
        rejection_reasons.append(f"Spread {metrics.bid_ask_spread_pct:.1f}% > {thresholds.liquidity_reject_max_spread_pct:.1f}%")

    if metrics.volume < thresholds.liquidity_reject_min_volume:
        rejection_reasons.append(f"Volume {metrics.volume} < {thresholds.liquidity_reject_min_volume}")

    if not metrics.has_valid_quotes:
        rejection_reasons.append("No valid bid/ask quotes")

    # If any rejection reasons, return REJECT tier
    if rejection_reasons:
        return (LiquidityTier.REJECT, rejection_reasons, [])

    # Check EXCELLENT tier (all criteria must pass)
    is_excellent = (
        metrics.open_interest >= thresholds.liquidity_excellent_min_oi
        and metrics.bid_ask_spread_pct <= thresholds.liquidity_excellent_max_spread_pct
        and metrics.volume >= thresholds.liquidity_excellent_min_volume
    )

    if is_excellent:
        return (LiquidityTier.EXCELLENT, [], [])

    # Otherwise WARNING tier (tradeable but not ideal)
    if metrics.open_interest < thresholds.liquidity_warning_min_oi:
        warning_reasons.append(f"OI {metrics.open_interest} < {thresholds.liquidity_warning_min_oi}")

    if metrics.bid_ask_spread_pct > thresholds.liquidity_warning_max_spread_pct:
        warning_reasons.append(f"Spread {metrics.bid_ask_spread_pct:.1f}% > {thresholds.liquidity_warning_max_spread_pct:.1f}%")

    if metrics.volume < thresholds.liquidity_warning_min_volume:
        warning_reasons.append(f"Volume {metrics.volume} < {thresholds.liquidity_warning_min_volume}")

    return (LiquidityTier.WARNING, [], warning_reasons)


def analyze_spread_liquidity(
    short_strike_quote: OptionQuote,
    long_strike_quote: Optional[OptionQuote],
    thresholds: ThresholdsConfig
) -> LiquidityAnalysis:
    """
    Analyze liquidity for a spread (or single-leg position).

    For spreads, BOTH legs must have acceptable liquidity. The overall tier
    is the WORSE of the two legs (e.g., if short is EXCELLENT but long is WARNING,
    overall is WARNING).

    Args:
        short_strike_quote: Quote for the short (sold) option
        long_strike_quote: Quote for the long (bought) option, or None for naked positions
        thresholds: Configuration thresholds

    Returns:
        LiquidityAnalysis with overall tier and detailed metrics
    """
    # Analyze short strike
    short_metrics = LiquidityMetrics.from_option_quote(short_strike_quote)
    short_tier, short_reject, short_warn = classify_liquidity(short_metrics, thresholds)

    # If no long strike (naked position), return short analysis
    if long_strike_quote is None:
        return LiquidityAnalysis(
            tier=short_tier,
            short_strike_metrics=short_metrics,
            long_strike_metrics=None,
            rejection_reasons=short_reject,
            warning_reasons=short_warn
        )

    # Analyze long strike
    long_metrics = LiquidityMetrics.from_option_quote(long_strike_quote)
    long_tier, long_reject, long_warn = classify_liquidity(long_metrics, thresholds)

    # Overall tier is the WORSE of the two
    # REJECT > WARNING > EXCELLENT (in terms of "badness")
    tier_priority = {
        LiquidityTier.REJECT: 3,
        LiquidityTier.WARNING: 2,
        LiquidityTier.EXCELLENT: 1
    }

    overall_tier = short_tier if tier_priority[short_tier] >= tier_priority[long_tier] else long_tier

    # Combine rejection and warning reasons
    all_rejections = []
    all_warnings = []

    if short_reject:
        all_rejections.extend([f"Short leg: {r}" for r in short_reject])
    if long_reject:
        all_rejections.extend([f"Long leg: {r}" for r in long_reject])

    if short_warn:
        all_warnings.extend([f"Short leg: {r}" for r in short_warn])
    if long_warn:
        all_warnings.extend([f"Long leg: {r}" for r in long_warn])

    return LiquidityAnalysis(
        tier=overall_tier,
        short_strike_metrics=short_metrics,
        long_strike_metrics=long_metrics,
        rejection_reasons=all_rejections,
        warning_reasons=all_warnings
    )


def analyze_chain_liquidity(
    calls: Dict[Strike, OptionQuote],
    puts: Dict[Strike, OptionQuote],
    thresholds: ThresholdsConfig
) -> tuple[bool, Optional[str]]:
    """
    Quick liquidity check for an entire option chain.

    Used in scan pre-filtering to reject tickers that have NO acceptable
    options before doing full VRP analysis.

    Args:
        calls: Dictionary of call options (strike -> quote)
        puts: Dictionary of put options (strike -> quote)
        thresholds: Configuration thresholds

    Returns:
        Tuple of (has_acceptable_liquidity, reason_if_rejected)
    """
    if len(calls) == 0 or len(puts) == 0:
        return (False, "No options available in chain")

    # Check if at least SOME options meet minimum (REJECT tier) thresholds
    acceptable_calls = 0
    acceptable_puts = 0

    for strike, quote in calls.items():
        metrics = LiquidityMetrics.from_option_quote(quote)
        tier, _, _ = classify_liquidity(metrics, thresholds)
        if tier != LiquidityTier.REJECT:
            acceptable_calls += 1

    for strike, quote in puts.items():
        metrics = LiquidityMetrics.from_option_quote(quote)
        tier, _, _ = classify_liquidity(metrics, thresholds)
        if tier != LiquidityTier.REJECT:
            acceptable_puts += 1

    if acceptable_calls == 0 or acceptable_puts == 0:
        return (False, f"Insufficient liquidity (only {acceptable_calls} calls, {acceptable_puts} puts pass minimum thresholds)")

    return (True, None)
