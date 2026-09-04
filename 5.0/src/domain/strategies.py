"""
Strategy generator for IV Crush trades.

Generates credit spread strategies based on direction and liquidity.
Iron Condors are permanently banned — NEUTRAL direction uses bull put + bear call spreads.
"""

from dataclasses import dataclass
from typing import List
import math


@dataclass
class Strategy:
    """Option strategy with P/L characteristics."""
    name: str
    description: str
    short_strike: float
    long_strike: float
    expiration: str
    max_profit: float
    max_risk: float
    pop: int  # Probability of profit (0-100)
    breakeven: float

    @property
    def risk_reward(self) -> float:
        """Risk/reward ratio."""
        return self.max_risk / self.max_profit if self.max_profit > 0 else 999


def _round_strike(price: float, direction: str = "down") -> float:
    """Round to nearest standard strike."""
    if price < 50:
        increment = 2.5
    elif price < 200:
        increment = 5.0
    else:
        increment = 10.0

    if direction == "down":
        return math.floor(price / increment) * increment
    else:
        return math.ceil(price / increment) * increment


def generate_strategies(
    ticker: str,
    price: float,
    implied_move_pct: float,
    direction: str,
    liquidity_tier: str,
    expiration: str = "",
) -> List[Strategy]:
    """
    Generate option strategies for ticker.

    Args:
        ticker: Stock symbol
        price: Current stock price
        implied_move_pct: Expected move percentage
        direction: BULLISH, BEARISH, or NEUTRAL
        liquidity_tier: EXCELLENT, GOOD, WARNING, or REJECT
        expiration: Option expiration date

    Returns:
        List of Strategy objects, sorted by POP descending
    """
    # Note: REJECT liquidity allowed but penalized in scoring (Feb 2026 relaxation)
    strategies = []
    implied_move = price * (implied_move_pct / 100)

    # Calculate strike distances based on implied move
    # Short strike at 1x implied move, long strike at 1.5x
    short_distance = implied_move
    spread_width = implied_move * 0.5

    if direction == "BULLISH":
        # Bull Put Spread: sell put below price, buy lower put
        short_strike = _round_strike(price - short_distance, "down")
        long_strike = _round_strike(short_strike - spread_width, "down")

        # Estimate credit (simplified)
        credit = spread_width * 0.35  # ~35% of width
        max_risk = (short_strike - long_strike) - credit

        strategies.append(Strategy(
            name="Bull Put Spread",
            description=f"Sell {short_strike:g}P / Buy {long_strike:g}P",
            short_strike=short_strike,
            long_strike=long_strike,
            expiration=expiration,
            max_profit=credit * 100,
            max_risk=max_risk * 100,
            pop=68,  # ~1 std dev
            breakeven=short_strike - credit,
        ))

    elif direction == "BEARISH":
        # Bear Call Spread: sell call above price, buy higher call
        short_strike = _round_strike(price + short_distance, "up")
        long_strike = _round_strike(short_strike + spread_width, "up")

        credit = spread_width * 0.35
        max_risk = (long_strike - short_strike) - credit

        strategies.append(Strategy(
            name="Bear Call Spread",
            description=f"Sell {short_strike:g}C / Buy {long_strike:g}C",
            short_strike=short_strike,
            long_strike=long_strike,
            expiration=expiration,
            max_profit=credit * 100,
            max_risk=max_risk * 100,
            pop=68,
            breakeven=short_strike + credit,
        ))

    else:  # NEUTRAL — prefer put side first (bull put), then call side
        # Bull Put Spread (primary)
        put_short = _round_strike(price - short_distance, "down")
        put_long = _round_strike(put_short - spread_width, "down")

        put_credit = spread_width * 0.35
        put_risk = (put_short - put_long) - put_credit

        strategies.append(Strategy(
            name="Bull Put Spread",
            description=f"Sell {put_short:g}P / Buy {put_long:g}P",
            short_strike=put_short,
            long_strike=put_long,
            expiration=expiration,
            max_profit=put_credit * 100,
            max_risk=put_risk * 100,
            pop=68,
            breakeven=put_short - put_credit,
        ))

        # Bear Call Spread (secondary)
        call_short = _round_strike(price + short_distance, "up")
        call_long = _round_strike(call_short + spread_width, "up")

        call_credit = spread_width * 0.35
        call_risk = (call_long - call_short) - call_credit

        strategies.append(Strategy(
            name="Bear Call Spread",
            description=f"Sell {call_short:g}C / Buy {call_long:g}C",
            short_strike=call_short,
            long_strike=call_long,
            expiration=expiration,
            max_profit=call_credit * 100,
            max_risk=call_risk * 100,
            pop=68,
            breakeven=call_short + call_credit,
        ))

    # Sort by POP descending
    strategies.sort(key=lambda s: s.pop, reverse=True)

    return strategies
