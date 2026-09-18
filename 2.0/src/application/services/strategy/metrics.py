"""Spread metrics, position Greeks, and strategy liquidity calculation."""
import logging
from datetime import date, datetime, time
from math import log, sqrt
from statistics import NormalDist
from typing import List, Optional, Tuple

from src.application.metrics.liquidity_scorer import LiquidityScorer
from src.domain.enums import OptionType
from src.domain.types import (
    MARKET_TZ,
    Money,
    OptionChain,
    OptionQuote,
    StrategyLeg,
    Strike,
)

logger = logging.getLogger(__name__)

# Equity options stop trading at the 4pm ET close on expiration day.
EXPIRATION_CLOSE_ET = time(16, 0)
# Calendar-day year: implied volatility is annualised over calendar time.
DAYS_PER_YEAR = 365.0

# POP used only when an input it needs is genuinely missing (no implied
# volatility on the short leg, no time to expiry, or neither a delta nor a
# stock price to place the short strike). A priced 25-delta spread computes to
# about 0.68-0.78 (puts) and 0.79-0.84 (calls) at 60-200% IV and 3-8 days, so
# this sits at the low end, and the caller caps it at the short strike's own
# 1 - |delta|. 0.70 is the sizing pop_scale knee (half of full quality scale).
FALLBACK_POP = 0.70

_STANDARD_NORMAL = NormalDist()


def years_to_expiration(expiration: date, as_of: datetime) -> float:
    """
    Calendar years from ``as_of`` to the 4pm ET close on ``expiration``.

    A naive ``as_of`` is taken as Eastern time. Weekends and holidays count:
    the year is calendar days / 365. Zero or negative once the option has
    stopped trading.
    """
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=MARKET_TZ)
    close = datetime.combine(expiration, EXPIRATION_CLOSE_ET, tzinfo=MARKET_TZ)
    return (close - as_of).total_seconds() / (DAYS_PER_YEAR * 24 * 3600)


def probability_beyond_breakeven(
    is_put: bool,
    short_strike: float,
    breakeven: float,
    short_quote: OptionQuote,
    stock_price: Optional[float],
    years_to_expiry: Optional[float],
) -> Optional[float]:
    """
    Lognormal probability that the underlying finishes beyond the breakeven on
    the spread's profitable side: above it for a put spread, below it for a
    call spread. Zero rates and dividends.

    With v = sigma * sqrt(T) (the short leg's implied volatility over calendar
    time to expiry) and z = N^-1(1 - |delta_short|):

        put spread:   POP = N(z - v + ln(K / BE) / v)
        call spread:  POP = N(z + v + ln(BE / K) / v)

    z is d1 for a put and -d1 for a call, so these are exactly N(d2(BE)) and
    N(-d2(BE)). Taking d1 from the quote's delta anchors the strike's distance
    to the vendor's own spot, rates and time convention at the moment the
    greeks were computed; only the -/+v term and the credit shift use T. With
    no delta, d1 is computed from the stock price instead.

    Returns None when an input is missing, so the caller can apply an explicit
    fallback rather than a silent default.
    """
    iv = short_quote.implied_volatility
    if iv is None or iv.value <= 0 or years_to_expiry is None or years_to_expiry <= 0:
        return None
    if short_strike <= 0:
        return None
    if breakeven <= 0:
        # A put spread profits wherever the price finishes above a non-positive
        # breakeven; a call spread never finishes below one.
        return 1.0 if is_put else 0.0

    v = iv.value / 100.0 * sqrt(years_to_expiry)

    p_short_otm_by_delta = 1.0 - abs(short_quote.delta) if short_quote.delta else None
    if p_short_otm_by_delta is not None and 0.0 < p_short_otm_by_delta < 1.0:
        z = _STANDARD_NORMAL.inv_cdf(p_short_otm_by_delta)
    elif stock_price is not None and stock_price > 0:
        d1 = (log(stock_price / short_strike) + v * v / 2.0) / v
        z = d1 if is_put else -d1
    else:
        return None

    if is_put:
        return _STANDARD_NORMAL.cdf(z - v + log(short_strike / breakeven) / v)
    return _STANDARD_NORMAL.cdf(z + v + log(breakeven / short_strike) / v)


def calculate_spread_metrics(
    short_quote: OptionQuote,
    long_quote: OptionQuote,
    short_strike: Strike,
    long_strike: Strike,
    *,
    stock_price: Optional[Money] = None,
    years_to_expiry: Optional[float] = None,
) -> dict:
    """
    Calculate metrics for a vertical spread.

    Args:
        short_quote, long_quote: Quotes for the sold and bought legs
        short_strike, long_strike: Their strikes (short above long = put spread)
        stock_price: Underlying price; places the short strike when it has no delta
        years_to_expiry: Calendar years to expiration (see years_to_expiration)

    Returns:
        Dict with net_credit, max_profit, max_loss, breakeven, pop, reward_risk,
        all at mid, and natural_credit / natural_max_loss: the same spread sold
        at the short leg's bid and bought at the long leg's ask. Every figure is
        per spread. pop is the probability of finishing beyond the breakeven
        (probability_beyond_breakeven), or FALLBACK_POP capped at the short
        strike's 1 - |delta| when an input it needs is missing.
    """
    # Net credit (what we collect)
    net_credit = Money(short_quote.mid.amount - long_quote.mid.amount)
    natural_credit = Money(short_quote.bid.amount - long_quote.ask.amount)

    # Max profit = credit received
    max_profit = Money(net_credit.amount * 100)  # Per contract

    # Spread width
    spread_width = abs(float(short_strike.price) - float(long_strike.price))

    # Max loss = width - credit
    max_loss = Money((spread_width - float(net_credit.amount)) * 100)
    natural_max_loss = Money((spread_width - float(natural_credit.amount)) * 100)

    # Breakeven
    is_put = short_strike > long_strike
    if is_put:
        breakeven = Money(float(short_strike.price) - float(net_credit.amount))
    else:  # Call spread
        breakeven = Money(float(short_strike.price) + float(net_credit.amount))

    # Probability of profit: finishing beyond the breakeven, not the short strike
    pop = probability_beyond_breakeven(
        is_put=is_put,
        short_strike=float(short_strike.price),
        breakeven=float(breakeven.amount),
        short_quote=short_quote,
        stock_price=float(stock_price.amount) if stock_price is not None else None,
        years_to_expiry=years_to_expiry,
    )
    if pop is None:
        pop = FALLBACK_POP
        if short_quote.delta:
            pop = min(FALLBACK_POP, 1.0 - abs(short_quote.delta))
        logger.warning(
            f"POP inputs missing for {'put' if is_put else 'call'} spread "
            f"short {short_strike} (iv={short_quote.implied_volatility}, "
            f"delta={short_quote.delta}, years_to_expiry={years_to_expiry}, "
            f"stock_price={stock_price}); using conservative fallback POP {pop:.2f}"
        )

    # Reward/risk ratio
    reward_risk = float(max_profit.amount / max_loss.amount) if max_loss.amount > 0 else 0.0

    return {
        'net_credit': net_credit,
        'max_profit': max_profit,
        'max_loss': max_loss,
        'breakeven': breakeven,
        'pop': pop,
        'reward_risk': reward_risk,
        'natural_credit': natural_credit,
        'natural_max_loss': natural_max_loss,
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

    for _strike, quote, multiplier in legs_data:
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


def position_liquidity_tier(leg_tiers: List[str]) -> str:
    """A position's tier from its graded legs: the worst leg, with GOOD reading as
    EXCELLENT (neither changes the size; WARNING halves it, CLAUDE.md §2)."""
    if "REJECT" in leg_tiers:
        return "REJECT"
    if "WARNING" in leg_tiers:
        return "WARNING"
    return "EXCELLENT"


def calculate_strategy_liquidity(
    liquidity_scorer: LiquidityScorer,
    ticker: str,
    option_chain: OptionChain,
    legs: List[StrategyLeg],
) -> dict:
    """
    Calculate liquidity metrics for strategy (package order optimized).

    For package orders (a spread traded as a single order), only SHORT legs
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

    overall_tier = position_liquidity_tier(tiers)

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
