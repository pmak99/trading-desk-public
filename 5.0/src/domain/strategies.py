"""
Strategy generator for IV Crush trades.

Generates credit spread strategies based on direction and liquidity.
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
            description=f"Sell {short_strike}P / Buy {long_strike}P",
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
            description=f"Sell {short_strike}C / Buy {long_strike}C",
            short_strike=short_strike,
            long_strike=long_strike,
            expiration=expiration,
            max_profit=credit * 100,
            max_risk=max_risk * 100,
            pop=68,
            breakeven=short_strike + credit,
        ))

    else:  # NEUTRAL
        # Iron Condor: bull put + bear call
        put_short = _round_strike(price - short_distance, "down")
        put_long = _round_strike(put_short - spread_width, "down")
        call_short = _round_strike(price + short_distance, "up")
        call_long = _round_strike(call_short + spread_width, "up")

        credit = spread_width * 0.5  # Both sides
        max_risk = spread_width - credit

        strategies.append(Strategy(
            name="Iron Condor",
            description=f"{put_long}P/{put_short}P - {call_short}C/{call_long}C",
            short_strike=put_short,  # Lower short strike for reference
            long_strike=call_short,  # Upper short strike
            expiration=expiration,
            max_profit=credit * 100,
            max_risk=max_risk * 100,
            pop=60,
            breakeven=put_short - credit,  # Lower breakeven
        ))

    # Sort by POP descending
    strategies.sort(key=lambda s: s.pop, reverse=True)

    return strategies
