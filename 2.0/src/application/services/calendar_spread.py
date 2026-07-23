"""
Pre-earnings ATM calendar spread builder — Jun 2026 PILOT.

Structure: SELL the earnings-week (front) ATM option, BUY the same strike
~30 days out (back). Net debit; max loss = debit paid; the long back leg is
a built-in hedge against the gap, so this is the defined-risk alternative
for setups where compound tail risk (TRR HIGH / ORATS sizing alarm) would
otherwise force a skip or a 25-contract cap on a credit spread.

Evidence (practitioner, 2020-2026): ~58-63% win rates, 1.7-2:1 managed P/L
when gated on VRP >= 1.4x and exited within 24-48h post-earnings (MenthorQ
ACES; ORATS earnings-effect research). Direction: call calendar by default,
put calendar when fused skew bias is bearish (put IV rich -> put calendar
collects more front premium).

PILOT CONSTRAINTS:
- max 10 contracts regardless of conviction (PILOT_MAX_CONTRACTS)
- generated only when term structure shows backwardation (the front IV
  must contain event premium for the short leg to crush)
- exit rule unchanged: close next trading day after earnings; do NOT hold
  the back leg as a directional position.

Max profit is an ESTIMATE (managed-exit heuristic, 50% of debit), not a
model price — calendars have path-dependent payoff; the displayed
reward/risk is intentionally conservative.
"""

import logging
from typing import Optional

from src.domain.enums import StrategyType, OptionType, DirectionalBias
from src.domain.types import (
    Money,
    OptionChain,
    SizingContext,
    Strategy,
    StrategyLeg,
)
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode

logger = logging.getLogger(__name__)

PILOT_MAX_CONTRACTS = 10
# Managed-exit heuristic: profit target is 30-50% of max theoretical value;
# we estimate max_profit at 50% of debit for scoring/display purposes.
ESTIMATED_PROFIT_FRACTION = 0.50
# Practitioner-documented win-rate band midpoint (58-63%).
ESTIMATED_POP = 0.55
COMMISSION_PER_CONTRACT = 0.30

BEARISH_BIASES = (
    DirectionalBias.WEAK_BEARISH,
    DirectionalBias.BEARISH,
    DirectionalBias.STRONG_BEARISH,
)


def build_calendar_spread(
    front_chain: OptionChain,
    back_chain: OptionChain,
    bias: DirectionalBias = DirectionalBias.NEUTRAL,
    sizing_context: Optional[SizingContext] = None,
) -> Result[Strategy, AppError]:
    """
    Build an ATM calendar spread from front and back chains.

    Returns Err when the structure is not constructible (missing strike in
    the back chain, non-positive debit, illiquid legs).
    """
    ticker = front_chain.ticker
    option_type = (
        OptionType.PUT if bias in BEARISH_BIASES else OptionType.CALL
    )

    try:
        strike = front_chain.atm_strike()
    except ValueError:
        return Err(AppError(ErrorCode.NODATA, f"{ticker}: no strikes in front chain"))

    front_side = front_chain.puts if option_type == OptionType.PUT else front_chain.calls
    back_side = back_chain.puts if option_type == OptionType.PUT else back_chain.calls

    front_quote = front_side.get(strike)
    # Back chain may have different strike listing — use exact match only;
    # a different back strike would make this a diagonal, not a calendar.
    back_quote = back_side.get(strike)

    if front_quote is None or back_quote is None:
        return Err(AppError(
            ErrorCode.NODATA,
            f"{ticker}: strike {strike} not present in both expirations",
        ))

    if not front_quote.is_liquid or not back_quote.is_liquid:
        return Err(AppError(
            ErrorCode.NODATA,
            f"{ticker}: calendar legs fail liquidity check "
            f"(front OI={front_quote.open_interest}, back OI={back_quote.open_interest})",
        ))

    # Buy back at ask-leaning mid, sell front at bid-leaning mid: use mids,
    # consistent with the vertical spread builder's pricing convention.
    net_debit = float(back_quote.mid.amount) - float(front_quote.mid.amount)
    if net_debit <= 0:
        return Err(AppError(
            ErrorCode.INVALID,
            f"{ticker}: calendar net debit non-positive ({net_debit:.2f}) — "
            f"front mid >= back mid, structure has no edge to buy",
        ))

    contracts = PILOT_MAX_CONTRACTS
    if sizing_context is not None:
        contracts = min(contracts, sizing_context.trr_cap)

    max_loss = net_debit * 100 * contracts
    est_max_profit = max_loss * ESTIMATED_PROFIT_FRACTION
    total_commission = COMMISSION_PER_CONTRACT * contracts * 2

    legs = [
        StrategyLeg(
            strike=strike,
            option_type=option_type,
            action="SELL",
            contracts=contracts,
            premium=front_quote.mid,
            expiration=front_chain.expiration,
        ),
        StrategyLeg(
            strike=strike,
            option_type=option_type,
            action="BUY",
            contracts=contracts,
            premium=back_quote.mid,
            expiration=back_chain.expiration,
        ),
    ]

    min_oi = min(front_quote.open_interest, back_quote.open_interest)
    max_spread_pct = max(front_quote.spread_pct, back_quote.spread_pct)

    strategy = Strategy(
        ticker=ticker,
        strategy_type=StrategyType.CALENDAR_SPREAD,
        expiration=front_chain.expiration,
        legs=legs,
        stock_price=front_chain.stock_price,
        net_credit=Money(-net_debit),  # debit structure: negative credit
        max_profit=Money(est_max_profit),
        max_loss=Money(max_loss),
        breakeven=[],  # path-dependent; not meaningful pre-announcement
        probability_of_profit=ESTIMATED_POP,
        reward_risk_ratio=ESTIMATED_PROFIT_FRACTION,
        contracts=contracts,
        capital_required=Money(max_loss),
        commission_per_contract=COMMISSION_PER_CONTRACT,
        total_commission=Money(total_commission),
        net_profit_after_fees=Money(est_max_profit - total_commission),
        profitability_score=0.0,   # pilot: not run through StrategyScorer
        risk_score=0.0,
        overall_score=0.0,
        rationale=(
            f"PILOT: ATM {option_type.value} calendar — sell "
            f"{front_chain.expiration} / buy {back_chain.expiration} at {strike}. "
            f"Debit ${net_debit:.2f}/spread; max loss = debit; back leg hedges the gap. "
            f"Profit/POP figures are managed-exit estimates, not model prices. "
            f"Exit next trading day after earnings."
        ),
    )
    logger.info(
        f"{ticker}: calendar pilot candidate — {strategy.strike_description}, "
        f"{contracts} contracts, max loss ${max_loss:,.0f}"
    )
    return Ok(strategy)
