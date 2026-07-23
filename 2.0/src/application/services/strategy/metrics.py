"""Spread metrics, position Greeks, and strategy liquidity calculation."""
import logging
from typing import List, Optional, Tuple

from src.domain.types import Money, Strike, OptionQuote, StrategyLeg, Strategy, OptionChain
from src.domain.enums import OptionType
from src.application.metrics.liquidity_scorer import LiquidityScorer

logger = logging.getLogger(__name__)


def calculate_spread_metrics(
    short_quote: OptionQuote,
    long_quote: OptionQuote,
    short_strike: Strike,
    long_strike: Strike,
) -> dict:
    """
    Calculate metrics for a vertical spread.

    Returns:
        Dict with net_credit, max_profit, max_loss, breakeven, pop, reward_risk
    """
    # Net credit (what we collect)
    net_credit = Money(short_quote.mid.amount - long_quote.mid.amount)

    # Max profit = credit received
    max_profit = Money(net_credit.amount * 100)  # Per contract

    # Spread width
    spread_width = abs(float(short_strike.price) - float(long_strike.price))

    # Max loss = width - credit
    max_loss = Money((spread_width - float(net_credit.amount)) * 100)

    # Breakeven
    if short_strike > long_strike:  # Put spread
        breakeven = Money(float(short_strike.price) - float(net_credit.amount))
    else:  # Call spread
        breakeven = Money(float(short_strike.price) + float(net_credit.amount))

    # Probability of profit (estimate from delta if available)
    if short_quote.delta:
        pop = 1.0 - abs(short_quote.delta)
    else:
        # Fallback: Use distance from price as proxy
        # FIX: Aligned with target_delta_short = 0.25 (25-delta ≈ 75% POP)
        pop = 0.75  # Default 75% for ~25-delta (was 70% for 30-delta)

    # Reward/risk ratio
    reward_risk = float(max_profit.amount / max_loss.amount) if max_loss.amount > 0 else 0.0

    return {
        'net_credit': net_credit,
        'max_profit': max_profit,
        'max_loss': max_loss,
        'breakeven': breakeven,
        'pop': pop,
        'reward_risk': reward_risk,
    }


def calculate_position_greeks(
    legs_data: List[Tuple[Strike, OptionQuote, int]],
    contracts: int,
) -> dict:
    """
    Calculate aggregated position Greeks across all legs.

    Args:
        legs_data: List of (strike, quote, multiplier) tuples
                  multiplier: -1 for short positions, +1 for long positions
        contracts: Number of contracts in the position

    Returns:
        Dict with delta, gamma, theta, vega (None if greeks not available)
    """
    delta_total = 0.0
    gamma_total = 0.0
    theta_total = 0.0
    vega_total = 0.0
    has_greeks = False

    for strike, quote, multiplier in legs_data:
        if quote.delta:
            delta_total += quote.delta * multiplier * contracts * 100
            has_greeks = True

        if quote.gamma:
            gamma_total += quote.gamma * multiplier * contracts * 100

        if quote.theta:
            theta_total += quote.theta * multiplier * contracts * 100

        if quote.vega:
            vega_total += quote.vega * multiplier * contracts * 100

    return {
        'delta': delta_total if has_greeks else None,
        'gamma': gamma_total if has_greeks else None,
        'theta': theta_total if has_greeks else None,
        'vega': vega_total if has_greeks else None,
    }


def combine_greeks(spread1: Strategy, spread2: Strategy) -> dict:
    """
    Combine position Greeks from two spreads (for iron condor).

    Returns:
        Dict with combined delta, gamma, theta, vega (each None if both spreads have None)
    """
    def combine_greek(g1, g2):
        if g1 is None and g2 is None:
            return None
        return (g1 or 0.0) + (g2 or 0.0)

    return {
        'delta': combine_greek(spread1.position_delta, spread2.position_delta),
        'gamma': combine_greek(spread1.position_gamma, spread2.position_gamma),
        'theta': combine_greek(spread1.position_theta, spread2.position_theta),
        'vega': combine_greek(spread1.position_vega, spread2.position_vega),
    }


def calculate_strategy_liquidity(
    liquidity_scorer: LiquidityScorer,
    ticker: str,
    option_chain: OptionChain,
    legs: List[StrategyLeg],
) -> dict:
    """
    Calculate liquidity metrics for strategy (package order optimized).

    For package orders (spreads/condors traded as single order), only SHORT legs
    matter for liquidity. Short strikes determine premium collected and fill quality.

    Returns:
        Dict with liquidity_tier, min_open_interest, max_spread_pct
    """
    if not legs:
        logger.warning(f"{ticker}: Empty legs list in liquidity calculation")
        return {
            'liquidity_tier': "REJECT",
            'min_open_interest': 0,
            'max_spread_pct': 100.0,
        }

    short_legs = [leg for leg in legs if leg.is_short]

    if not short_legs:
        logger.warning(f"{ticker}: No short legs found in strategy")
        return {
            'liquidity_tier': "REJECT",
            'min_open_interest': 0,
            'max_spread_pct': 100.0,
        }

    tiers = []
    oi_values = []
    spread_pcts = []

    for leg in short_legs:
        chain = option_chain.calls if leg.option_type == OptionType.CALL else option_chain.puts
        quote = chain.get(leg.strike)

        if not quote:
            tiers.append("REJECT")
            oi_values.append(0)
            spread_pcts.append(100.0)
            continue

        tier = liquidity_scorer.classify_option_tier(quote)
        tiers.append(tier)
        oi_values.append(quote.open_interest or 0)
        spread_pcts.append(liquidity_scorer.calculate_spread_pct(quote))

    if "REJECT" in tiers:
        overall_tier = "REJECT"
    elif "WARNING" in tiers:
        overall_tier = "WARNING"
    else:
        overall_tier = "EXCELLENT"

    min_oi = min(oi_values)
    max_spread = max(spread_pcts)

    logger.debug(
        f"{ticker}: Strategy liquidity (SHORT legs only): {overall_tier} "
        f"(min_oi={min_oi}, max_spread={max_spread:.1f}%, {len(short_legs)} short legs checked)"
    )

    return {
        'liquidity_tier': overall_tier,
        'min_open_interest': min_oi,
        'max_spread_pct': max_spread,
    }
