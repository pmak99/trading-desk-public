"""Vertical spread construction — assembles strikes, metrics, sizing, and Greeks into a Strategy."""
import logging
from typing import Optional

from src.domain.types import (
    Money, OptionChain, VRPResult, Strategy, StrategyLeg, SizingContext
)
from src.domain.enums import OptionType, StrategyType, DirectionalBias
from src.config.config import StrategyConfig
from src.application.metrics.liquidity_scorer import LiquidityScorer
from src.application.services.strategy.strike_selection import select_strikes_for_spread
from src.application.services.strategy.metrics import calculate_spread_metrics, calculate_position_greeks
from src.application.services.strategy.sizing import calculate_contracts_kelly, calculate_contracts

logger = logging.getLogger(__name__)


def build_vertical_spread(
    config: StrategyConfig,
    liquidity_scorer: LiquidityScorer,
    ticker: str,
    option_chain: OptionChain,
    vrp: VRPResult,
    option_type: OptionType,
    strategy_type: StrategyType,
    below: bool,
    bias: DirectionalBias,
    sizing_context: Optional[SizingContext] = None,
) -> Optional[Strategy]:
    """
    Build a vertical credit spread with asymmetric strike placement based on bias.

    Args:
        config: Strategy configuration
        liquidity_scorer: Scorer for evaluating option liquidity
        ticker: Ticker symbol
        option_chain: Options chain
        vrp: VRP analysis
        option_type: PUT or CALL
        strategy_type: BULL_PUT_SPREAD or BEAR_CALL_SPREAD
        below: True for put spread (below price), False for call spread (above)
        bias: Directional bias for asymmetric positioning
        sizing_context: Optional contract cap signals

    Returns:
        Strategy or None if construction fails
    """
    strikes = select_strikes_for_spread(
        config, ticker, option_chain, vrp, option_type, bias, below
    )

    if not strikes:
        return None

    short_strike, long_strike = strikes

    # Get option quotes
    option_chain_side = option_chain.puts if option_type == OptionType.PUT else option_chain.calls
    if short_strike not in option_chain_side or long_strike not in option_chain_side:
        # Debug: Show what strikes were selected and what's available
        available_strikes = sorted([float(s.price) for s in option_chain_side.keys()])
        logger.warning(
            f"{ticker}: Strikes not found in {option_type.value}s chain. "
            f"Selected: short=${float(short_strike.price):.2f}, long=${float(long_strike.price):.2f}. "
            f"Available: ${available_strikes[0]:.2f}-${available_strikes[-1]:.2f} "
            f"({len(available_strikes)} strikes)"
        )
        return None

    short_quote = option_chain_side[short_strike]
    long_quote = option_chain_side[long_strike]

    # Note: Liquidity validation is now done by LiquidityScorer after strategy construction
    # This allows for more sophisticated tier-based classification (EXCELLENT/WARNING/REJECT)
    # rather than binary accept/reject. The tier is attached to the strategy and can be
    # used for filtering or displaying warnings.

    # Calculate metrics
    metrics = calculate_spread_metrics(short_quote, long_quote, short_strike, long_strike)

    if metrics['net_credit'].amount < config.min_credit_per_spread:
        logger.warning(f"{ticker}: Credit too low for {strategy_type.value}")
        return None

    # Build legs
    legs = [
        StrategyLeg(
            strike=short_strike,
            option_type=option_type,
            action="SELL",
            contracts=1,
            premium=short_quote.mid,
        ),
        StrategyLeg(
            strike=long_strike,
            option_type=option_type,
            action="BUY",
            contracts=1,
            premium=long_quote.mid,
        ),
    ]

    # Position sizing - use Kelly Criterion if enabled, else fixed risk budget
    if config.use_kelly_sizing:
        contracts = calculate_contracts_kelly(
            config,
            max_profit=metrics['max_profit'],
            max_loss=metrics['max_loss'],
            probability_of_profit=metrics['pop'],
            sizing_context=sizing_context,
        )
    else:
        contracts = calculate_contracts(config, metrics['max_loss'], sizing_context)

    # Calculate position Greeks
    position_greeks = calculate_position_greeks(
        [(short_strike, short_quote, -1), (long_strike, long_quote, 1)],
        contracts
    )

    # Calculate commissions
    total_commission = Money(2 * contracts * config.commission_per_contract)
    net_profit_after_fees = Money(
        float(metrics['max_profit'].amount * contracts) - float(total_commission.amount)
    )

    # Build strategy
    return Strategy(
        ticker=ticker,
        strategy_type=strategy_type,
        expiration=option_chain.expiration,
        legs=legs,
        stock_price=option_chain.stock_price,
        net_credit=metrics['net_credit'],
        max_profit=metrics['max_profit'] * contracts,
        max_loss=metrics['max_loss'] * contracts,
        breakeven=[metrics['breakeven']],
        probability_of_profit=metrics['pop'],
        reward_risk_ratio=metrics['reward_risk'],
        contracts=contracts,
        capital_required=metrics['max_loss'] * contracts,
        commission_per_contract=config.commission_per_contract,
        total_commission=total_commission,
        net_profit_after_fees=net_profit_after_fees,
        profitability_score=0.0,  # Calculated later
        risk_score=0.0,  # Calculated later
        overall_score=0.0,  # Calculated later
        rationale="",  # Generated later
        position_delta=position_greeks['delta'],
        position_gamma=position_greeks['gamma'],
        position_theta=position_greeks['theta'],
        position_vega=position_greeks['vega'],
    )
