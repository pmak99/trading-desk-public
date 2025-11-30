"""
Quantitative strategy generator for earnings trades.

Generates 2-3 trade strategies with strike selections:
- Bull Put Spread (credit spread below price)
- Bear Call Spread (credit spread above price)
- Iron Condor (wide profit zone, neutral)
- Iron Butterfly (tight profit zone at ATM, neutral)

Selection and ranking based on:
- VRP ratio (implied vs historical volatility)
- Risk/reward ratios
- Probability of profit
- Position sizing for $20K risk budget

Pure quantitative approach - deterministic and backtestable.
"""

import logging
from datetime import datetime
from typing import List, Optional, Tuple
from decimal import Decimal

from src.domain.types import (
    Money, Strike, Percentage, OptionChain, OptionQuote,
    Strategy, StrategyLeg, StrategyRecommendation,
    VRPResult, SkewResult
)
from src.domain.enums import (
    StrategyType, DirectionalBias, OptionType
)
from src.config.config import StrategyConfig
from src.domain.scoring import StrategyScorer
from src.application.metrics.liquidity_scorer import LiquidityScorer

logger = logging.getLogger(__name__)


class StrategyGenerator:
    """
    Quantitative options strategy generator.

    Generates credit spreads (bull put, bear call) and iron condors
    based on VRP analysis and market conditions.
    """

    def __init__(self, config: StrategyConfig, liquidity_scorer: LiquidityScorer):
        """
        Initialize strategy generator with configuration.

        Args:
            config: Strategy configuration with all parameters
            liquidity_scorer: Liquidity scorer for evaluating option liquidity
        """
        self.config = config
        self.scorer = StrategyScorer(config.scoring_weights)
        self.liquidity_scorer = liquidity_scorer

    def generate_strategies(
        self,
        ticker: str,
        option_chain: OptionChain,
        vrp: VRPResult,
        skew: Optional[SkewResult] = None,
    ) -> StrategyRecommendation:
        """
        Generate 2-3 ranked strategy recommendations.

        Args:
            ticker: Ticker symbol
            option_chain: Complete options chain with greeks
            vrp: VRP analysis result
            skew: Optional skew analysis for directional bias

        Returns:
            StrategyRecommendation with 2-3 ranked strategies
        """
        logger.info(f"{ticker}: Generating strategies (VRP: {vrp.vrp_ratio:.2f}x)...")

        # Determine directional bias from skew
        bias = self._determine_bias(skew)
        logger.debug(f"{ticker}: Directional bias = {bias.value}")

        # Select strategy types based on conditions
        strategy_types = self._select_strategy_types(vrp, bias)
        logger.debug(f"{ticker}: Strategy types = {[s.value for s in strategy_types]}")

        # Generate each strategy
        strategies = []
        for strategy_type in strategy_types:
            try:
                strategy = self._build_strategy(
                    ticker, strategy_type, option_chain, vrp, bias
                )
                if strategy:
                    # Calculate and populate liquidity metrics
                    liquidity_metrics = self._calculate_strategy_liquidity(
                        ticker, option_chain, strategy.legs
                    )
                    strategy.liquidity_tier = liquidity_metrics['liquidity_tier']
                    strategy.min_open_interest = liquidity_metrics['min_open_interest']
                    strategy.max_spread_pct = liquidity_metrics['max_spread_pct']

                    strategies.append(strategy)
            except Exception as e:
                logger.warning(f"{ticker}: Failed to build {strategy_type.value}: {e}")

        if not strategies:
            raise ValueError(f"Could not generate any valid strategies for {ticker}")

        # Score and rank strategies
        self.scorer.score_strategies(strategies, vrp)
        strategies.sort(key=lambda s: s.overall_score, reverse=True)

        # Select best strategy
        recommended_idx = 0
        rationale = self.scorer.generate_recommendation_rationale(strategies[0], vrp, bias)

        return StrategyRecommendation(
            ticker=ticker,
            expiration=option_chain.expiration,
            analysis_time=datetime.now(),
            stock_price=option_chain.stock_price,
            implied_move_pct=vrp.implied_move_pct,
            vrp_ratio=vrp.vrp_ratio,
            directional_bias=bias,
            strategies=strategies[:3],  # Top 3 strategies
            recommended_index=recommended_idx,
            recommendation_rationale=rationale,
        )

    def _determine_bias(self, skew: Optional[SkewResult]) -> DirectionalBias:
        """
        Determine directional bias from IV skew.

        Args:
            skew: Skew analysis (optional)

        Returns:
            DirectionalBias enum
        """
        if not skew:
            return DirectionalBias.NEUTRAL

        # Handle both old SkewResult and new SkewAnalysis types
        # SkewResult has: direction = 'bearish', 'bullish', 'neutral'
        # SkewAnalysis has: directional_bias = 'put_bias', 'call_bias', 'neutral'

        # Try new SkewAnalysis format first
        if hasattr(skew, 'directional_bias'):
            if skew.directional_bias == 'put_bias':
                return DirectionalBias.BEARISH
            elif skew.directional_bias == 'call_bias':
                return DirectionalBias.BULLISH
            else:
                return DirectionalBias.NEUTRAL

        # Fallback to old SkewResult format
        elif hasattr(skew, 'direction'):
            if skew.direction == 'bearish':
                return DirectionalBias.BEARISH
            elif skew.direction == 'bullish':
                return DirectionalBias.BULLISH
            else:
                return DirectionalBias.NEUTRAL

        return DirectionalBias.NEUTRAL

    def _select_strategy_types(
        self, vrp: VRPResult, bias: DirectionalBias
    ) -> List[StrategyType]:
        """
        Select 2-3 strategy types based on VRP and bias.

        Logic:
        - Very High VRP (>2.5) + Neutral: Include iron butterfly
        - High VRP (>2.0): Include iron condor + both spreads
        - Moderate VRP (1.5-2.0): Include directional spread + iron condor
        - Bullish: Prefer bull put spread
        - Bearish: Prefer bear call spread
        - Neutral: Prefer iron condor/butterfly

        Args:
            vrp: VRP analysis
            bias: Directional bias

        Returns:
            List of 2-3 strategy types to generate
        """
        types = []

        if vrp.vrp_ratio >= 2.5 and bias == DirectionalBias.NEUTRAL:
            # Very high VRP + neutral = iron butterfly optimal
            types = [
                StrategyType.IRON_BUTTERFLY,
                StrategyType.IRON_CONDOR,
                StrategyType.BULL_PUT_SPREAD,
            ]
        elif vrp.vrp_ratio >= 2.0:
            # Excellent VRP - all strategies viable
            if bias == DirectionalBias.NEUTRAL:
                types = [
                    StrategyType.IRON_CONDOR,
                    StrategyType.BULL_PUT_SPREAD,
                    StrategyType.BEAR_CALL_SPREAD,
                ]
            elif bias == DirectionalBias.BULLISH:
                types = [
                    StrategyType.BULL_PUT_SPREAD,
                    StrategyType.IRON_CONDOR,
                    StrategyType.BEAR_CALL_SPREAD,
                ]
            else:  # BEARISH
                types = [
                    StrategyType.BEAR_CALL_SPREAD,
                    StrategyType.IRON_CONDOR,
                    StrategyType.BULL_PUT_SPREAD,
                ]

        elif vrp.vrp_ratio >= 1.5:
            # Good VRP - 2 strategies
            if bias == DirectionalBias.NEUTRAL:
                types = [StrategyType.IRON_CONDOR, StrategyType.BULL_PUT_SPREAD]
            elif bias == DirectionalBias.BULLISH:
                types = [StrategyType.BULL_PUT_SPREAD, StrategyType.IRON_CONDOR]
            else:  # BEARISH
                types = [StrategyType.BEAR_CALL_SPREAD, StrategyType.IRON_CONDOR]

        else:
            # Marginal VRP - single best strategy
            if bias == DirectionalBias.BULLISH:
                types = [StrategyType.BULL_PUT_SPREAD]
            elif bias == DirectionalBias.BEARISH:
                types = [StrategyType.BEAR_CALL_SPREAD]
            else:
                types = [StrategyType.BULL_PUT_SPREAD]  # Default to bull put

        return types

    def _build_strategy(
        self,
        ticker: str,
        strategy_type: StrategyType,
        option_chain: OptionChain,
        vrp: VRPResult,
        bias: DirectionalBias,
    ) -> Optional[Strategy]:
        """
        Build a complete strategy with legs and metrics.

        Args:
            ticker: Ticker symbol
            strategy_type: Type of strategy to build
            option_chain: Options chain
            vrp: VRP analysis
            bias: Directional bias

        Returns:
            Complete Strategy object or None if not viable
        """
        if strategy_type == StrategyType.BULL_PUT_SPREAD:
            return self._build_bull_put_spread(ticker, option_chain, vrp)
        elif strategy_type == StrategyType.BEAR_CALL_SPREAD:
            return self._build_bear_call_spread(ticker, option_chain, vrp)
        elif strategy_type == StrategyType.IRON_CONDOR:
            return self._build_iron_condor(ticker, option_chain, vrp)
        elif strategy_type == StrategyType.IRON_BUTTERFLY:
            return self._build_iron_butterfly(ticker, option_chain, vrp)
        else:
            logger.warning(f"{ticker}: Strategy type {strategy_type} not implemented")
            return None

    def _build_vertical_spread(
        self,
        ticker: str,
        option_chain: OptionChain,
        vrp: VRPResult,
        option_type: OptionType,
        strategy_type: StrategyType,
        below: bool
    ) -> Optional[Strategy]:
        """
        Build a vertical credit spread (common logic for bull put and bear call).

        Args:
            ticker: Ticker symbol
            option_chain: Options chain
            vrp: VRP analysis
            option_type: PUT or CALL
            strategy_type: BULL_PUT_SPREAD or BEAR_CALL_SPREAD
            below: True for put spread (below price), False for call spread (above)

        Returns:
            Strategy or None if construction fails
        """
        # Try delta-based selection first (more precise if Greeks available)
        strikes = self._select_strikes_delta_based(option_chain, option_type)

        # Verify delta-based strikes are outside implied move zone
        if strikes:
            strikes = self._verify_strikes_outside_implied_move(
                ticker, strikes, option_chain, vrp, below=below
            )

        # Fallback to distance-based if delta not available or strikes inside implied move
        if not strikes:
            logger.debug(f"{ticker}: Using distance-based selection (outside implied move)")
            strikes = self._select_strikes_distance_based(
                option_chain, vrp, option_type, below=below
            )

        if not strikes:
            return None

        short_strike, long_strike = strikes

        # Get option quotes
        option_chain_side = option_chain.puts if option_type == OptionType.PUT else option_chain.calls
        if short_strike not in option_chain_side or long_strike not in option_chain_side:
            logger.warning(f"{ticker}: Strikes not found in {option_type.value}s chain")
            return None

        short_quote = option_chain_side[short_strike]
        long_quote = option_chain_side[long_strike]

        # Validate liquidity
        if not short_quote.is_liquid or not long_quote.is_liquid:
            logger.warning(f"{ticker}: Insufficient liquidity for {strategy_type.value}")
            return None

        # Calculate metrics
        metrics = self._calculate_spread_metrics(
            short_quote, long_quote, short_strike, long_strike
        )

        if metrics['net_credit'].amount < self.config.min_credit_per_spread:
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

        # Position sizing
        contracts = self._calculate_contracts(metrics['max_loss'])

        # Calculate position Greeks
        position_greeks = self._calculate_position_greeks(
            [(short_strike, short_quote, -1), (long_strike, long_quote, 1)],
            contracts
        )

        # Calculate commissions
        total_commission = Money(2 * contracts * self.config.commission_per_contract)
        net_profit_after_fees = Money(
            float(metrics['max_profit'].amount * contracts) - float(total_commission.amount)
        )

        # Build strategy
        return Strategy(
            ticker=ticker,
            strategy_type=strategy_type,
            expiration=option_chain.expiration,
            legs=legs,
            net_credit=metrics['net_credit'],
            max_profit=metrics['max_profit'] * contracts,
            max_loss=metrics['max_loss'] * contracts,
            breakeven=[metrics['breakeven']],
            probability_of_profit=metrics['pop'],
            reward_risk_ratio=metrics['reward_risk'],
            contracts=contracts,
            capital_required=metrics['max_loss'] * contracts,
            commission_per_contract=self.config.commission_per_contract,
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

    def _build_bull_put_spread(
        self, ticker: str, option_chain: OptionChain, vrp: VRPResult
    ) -> Optional[Strategy]:
        """Build bull put spread (credit spread below price)."""
        return self._build_vertical_spread(
            ticker=ticker,
            option_chain=option_chain,
            vrp=vrp,
            option_type=OptionType.PUT,
            strategy_type=StrategyType.BULL_PUT_SPREAD,
            below=True
        )

    def _build_bear_call_spread(
        self, ticker: str, option_chain: OptionChain, vrp: VRPResult
    ) -> Optional[Strategy]:
        """Build bear call spread (credit spread above price)."""
        return self._build_vertical_spread(
            ticker=ticker,
            option_chain=option_chain,
            vrp=vrp,
            option_type=OptionType.CALL,
            strategy_type=StrategyType.BEAR_CALL_SPREAD,
            below=False
        )

    def _build_iron_condor(
        self, ticker: str, option_chain: OptionChain, vrp: VRPResult
    ) -> Optional[Strategy]:
        """Build iron condor (put spread + call spread)."""

        # Build put spread (lower side)
        put_spread = self._build_bull_put_spread(ticker, option_chain, vrp)
        if not put_spread:
            return None

        # Build call spread (upper side)
        call_spread = self._build_bear_call_spread(ticker, option_chain, vrp)
        if not call_spread:
            return None

        # Combine legs
        legs = put_spread.legs + call_spread.legs

        # Combined metrics
        net_credit = put_spread.net_credit + call_spread.net_credit
        max_profit = Money(net_credit.amount * 100)  # Total credit collected per contract
        max_loss = max(
            put_spread.max_loss / put_spread.contracts,
            call_spread.max_loss / call_spread.contracts
        )

        # Breakevens (both put and call side)
        breakevens = [put_spread.breakeven[0], call_spread.breakeven[0]]

        # POP for iron condor: probability stock stays between both short strikes
        # More accurate approach: Use deltas of short strikes to estimate probability
        # P(profit) = 1 - P(below put) - P(above call)
        # P(below put) ≈ |put delta|, P(above call) ≈ |call delta|

        # Get deltas from short legs (first leg of each spread)
        put_short_quote = option_chain.puts.get(put_spread.legs[0].strike)
        call_short_quote = option_chain.calls.get(call_spread.legs[0].strike)

        if put_short_quote and put_short_quote.delta and call_short_quote and call_short_quote.delta:
            # Delta-based calculation (most accurate)
            put_short_delta = abs(put_short_quote.delta)  # Prob of expiring ITM
            call_short_delta = abs(call_short_quote.delta)  # Prob of expiring ITM
            pop = max(0.0, 1.0 - put_short_delta - call_short_delta)
        else:
            # Fallback: Use individual spread POPs as approximation
            # This is less accurate but better than nothing
            pop = max(0.0, put_spread.probability_of_profit + call_spread.probability_of_profit - 1.0)

        # Reward/risk
        reward_risk = float(max_profit.amount / max_loss.amount)

        # Position sizing
        contracts = self._calculate_contracts(max_loss)

        # Combine position Greeks from both spreads
        position_greeks = self._combine_greeks(put_spread, call_spread)

        # Calculate commissions
        # Iron condor has 4 legs: 4 legs * contracts * commission_per_contract
        total_commission = Money(4 * contracts * self.config.commission_per_contract)
        net_profit_after_fees = Money(
            float(max_profit.amount * contracts) - float(total_commission.amount)
        )

        return Strategy(
            ticker=ticker,
            strategy_type=StrategyType.IRON_CONDOR,
            expiration=option_chain.expiration,
            legs=legs,
            net_credit=net_credit,
            max_profit=max_profit * contracts,
            max_loss=max_loss * contracts,
            breakeven=breakevens,
            probability_of_profit=pop,
            reward_risk_ratio=reward_risk,
            contracts=contracts,
            capital_required=max_loss * contracts,
            commission_per_contract=self.config.commission_per_contract,
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

    def _build_iron_butterfly(
        self, ticker: str, option_chain: OptionChain, vrp: VRPResult
    ) -> Optional[Strategy]:
        """
        Build iron butterfly (ATM short straddle + OTM long strangle).

        Structure:
        - Sell ATM call and put (collect premium)
        - Buy OTM call and put at equal distance (protection)
        - Max profit at current stock price
        - Tighter profit zone than iron condor

        Best for: Very high VRP + neutral bias + expectation stock stays near current price
        """
        stock_price = float(option_chain.stock_price.amount)

        # Find ATM strike (where we sell)
        try:
            atm_strike = option_chain.atm_strike()
        except (ValueError, IndexError) as e:
            logger.warning(f"{ticker}: Could not find ATM strike: {e}")
            return None

        # Calculate wing width (configurable % of stock price or $3, whichever is larger)
        # Tighter than condor since we expect minimal movement
        wing_width = max(stock_price * self.config.iron_butterfly_wing_width_pct, 3.0)

        # Find protection strikes (equal distance from ATM)
        available_strikes = sorted(option_chain.strikes, key=lambda s: float(s.price))

        long_put_strike = self._find_nearest_strike(
            available_strikes, float(atm_strike.price) - wing_width
        )
        long_call_strike = self._find_nearest_strike(
            available_strikes, float(atm_strike.price) + wing_width
        )

        if not long_put_strike or not long_call_strike:
            logger.warning(f"{ticker}: Could not find wing strikes for iron butterfly")
            return None

        # Verify strikes exist in chains
        if (atm_strike not in option_chain.calls or
            atm_strike not in option_chain.puts or
            long_call_strike not in option_chain.calls or
            long_put_strike not in option_chain.puts):
            logger.warning(f"{ticker}: Strikes not found in option chains")
            return None

        # Get quotes
        atm_call = option_chain.calls[atm_strike]
        atm_put = option_chain.puts[atm_strike]
        wing_call = option_chain.calls[long_call_strike]
        wing_put = option_chain.puts[long_put_strike]

        # Validate liquidity
        if not all([atm_call.is_liquid, atm_put.is_liquid,
                   wing_call.is_liquid, wing_put.is_liquid]):
            logger.warning(f"{ticker}: Insufficient liquidity for iron butterfly")
            return None

        # Calculate net credit
        credit_collected = atm_call.mid.amount + atm_put.mid.amount
        debit_paid = wing_call.mid.amount + wing_put.mid.amount
        net_credit = Money(credit_collected - debit_paid)

        if net_credit.amount < self.config.min_credit_per_spread:
            logger.warning(f"{ticker}: Credit too low for iron butterfly")
            return None

        # Max profit = net credit
        max_profit = Money(net_credit.amount * 100)

        # Max loss = wing width - net credit
        actual_wing_width = float(long_call_strike.price) - float(atm_strike.price)
        max_loss = Money((actual_wing_width - float(net_credit.amount)) * 100)

        # Breakevens (two, symmetric around ATM)
        breakeven_lower = Money(float(atm_strike.price) - float(net_credit.amount))
        breakeven_upper = Money(float(atm_strike.price) + float(net_credit.amount))

        # POP (probability stock stays within breakevens)
        # Calculate based on profit range relative to stock price
        profit_range = 2 * float(net_credit.amount)  # Total width of profit zone
        profit_range_pct = profit_range / stock_price * 100

        # Configurable POP estimation formula
        # Linear interpolation: POP = base + (range - reference) * sensitivity
        # Clamped between min and max
        pop = min(
            self.config.ib_pop_max,
            max(
                self.config.ib_pop_min,
                self.config.ib_pop_base + (profit_range_pct - self.config.ib_pop_reference_range) * self.config.ib_pop_sensitivity
            )
        )

        # Reward/risk
        if max_loss.amount <= 0:
            logger.error(f"{ticker}: Invalid max_loss ${max_loss.amount:.2f} for iron butterfly - skipping")
            return None
        reward_risk = float(max_profit.amount / max_loss.amount)

        # Position sizing
        contracts = self._calculate_contracts(max_loss)

        # Calculate position Greeks
        position_greeks = self._calculate_position_greeks(
            [
                (atm_strike, atm_call, -1),      # Short ATM call
                (atm_strike, atm_put, -1),       # Short ATM put
                (long_call_strike, wing_call, 1), # Long OTM call
                (long_put_strike, wing_put, 1),   # Long OTM put
            ],
            contracts
        )

        # Build legs
        legs = [
            # Short ATM straddle
            StrategyLeg(
                strike=atm_strike,
                option_type=OptionType.CALL,
                action="SELL",
                contracts=1,
                premium=atm_call.mid,
            ),
            StrategyLeg(
                strike=atm_strike,
                option_type=OptionType.PUT,
                action="SELL",
                contracts=1,
                premium=atm_put.mid,
            ),
            # Long OTM strangle (protection)
            StrategyLeg(
                strike=long_call_strike,
                option_type=OptionType.CALL,
                action="BUY",
                contracts=1,
                premium=wing_call.mid,
            ),
            StrategyLeg(
                strike=long_put_strike,
                option_type=OptionType.PUT,
                action="BUY",
                contracts=1,
                premium=wing_put.mid,
            ),
        ]

        # Calculate commissions
        # Iron butterfly has 4 legs: 4 legs * contracts * commission_per_contract
        total_commission = Money(4 * contracts * self.config.commission_per_contract)
        net_profit_after_fees = Money(
            float(max_profit.amount * contracts) - float(total_commission.amount)
        )

        return Strategy(
            ticker=ticker,
            strategy_type=StrategyType.IRON_BUTTERFLY,
            expiration=option_chain.expiration,
            legs=legs,
            net_credit=net_credit,
            max_profit=max_profit * contracts,
            max_loss=max_loss * contracts,
            breakeven=[breakeven_lower, breakeven_upper],
            probability_of_profit=pop,
            reward_risk_ratio=reward_risk,
            contracts=contracts,
            capital_required=max_loss * contracts,
            commission_per_contract=self.config.commission_per_contract,
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

    def _select_strikes_delta_based(
        self,
        option_chain: OptionChain,
        option_type: OptionType,
        target_delta_short: Optional[float] = None,
        target_delta_long: Optional[float] = None,
    ) -> Optional[Tuple[Strike, Strike]]:
        """
        Select strikes based on delta (probability-based selection).

        More precise than distance-based when Greeks are available.

        Args:
            option_chain: Options chain with greeks
            option_type: CALL or PUT
            target_delta_short: Target delta for short strike (default from config)
            target_delta_long: Target delta for long strike (default from config)

        Returns:
            Tuple of (short_strike, long_strike) or None
        """
        # Use config values if not specified
        if target_delta_short is None:
            target_delta_short = self.config.target_delta_short
        if target_delta_long is None:
            target_delta_long = self.config.target_delta_long

        chain = option_chain.puts if option_type == OptionType.PUT else option_chain.calls

        # Find strikes with deltas closest to targets
        short_strike = None
        long_strike = None
        min_short_diff = float('inf')
        min_long_diff = float('inf')

        for strike, quote in chain.items():
            if not quote.delta:
                continue  # Skip if no delta available

            # For puts: delta is negative, we want around -0.30 (short) and -0.20 (long)
            # For calls: delta is positive, we want around +0.30 (short) and +0.20 (long)
            delta_abs = abs(quote.delta)

            # Find short strike (higher delta, closer to ATM)
            short_diff = abs(delta_abs - target_delta_short)
            if short_diff < min_short_diff:
                min_short_diff = short_diff
                short_strike = strike

            # Find long strike (lower delta, further OTM)
            long_diff = abs(delta_abs - target_delta_long)
            if long_diff < min_long_diff:
                min_long_diff = long_diff
                long_strike = strike

        if not short_strike or not long_strike:
            return None

        # Ensure proper ordering
        if option_type == OptionType.PUT:
            # Puts: short should be higher strike than long
            if short_strike < long_strike:
                short_strike, long_strike = long_strike, short_strike
        else:
            # Calls: short should be lower strike than long
            if short_strike > long_strike:
                short_strike, long_strike = long_strike, short_strike

        return short_strike, long_strike

    def _select_strikes_distance_based(
        self,
        option_chain: OptionChain,
        vrp: VRPResult,
        option_type: OptionType,
        below: bool,
    ) -> Optional[Tuple[Strike, Strike]]:
        """
        Select strikes based on distance from current price.

        Position strikes OUTSIDE implied move range with buffer.

        Args:
            option_chain: Options chain
            vrp: VRP analysis
            option_type: CALL or PUT
            below: If True, select below price (puts), else above (calls)

        Returns:
            Tuple of (short_strike, long_strike) or None
        """
        stock_price = float(option_chain.stock_price.amount)
        implied_move_pct = vrp.implied_move_pct.value / 100
        implied_move_dollars = stock_price * implied_move_pct

        # Add 10% buffer beyond implied move
        buffer = implied_move_dollars * 0.10

        # Spread width - Fixed dollar amounts based on stock price (user strategy)
        # >= $20: Use $5 spread width
        # < $20:  Use $3 spread width
        if stock_price >= self.config.spread_width_threshold:
            spread_width = self.config.spread_width_high_price
        else:
            spread_width = self.config.spread_width_low_price

        if below:
            # Put spread: Position below lower bound
            short_strike_price = stock_price - implied_move_dollars - buffer
            long_strike_price = short_strike_price - spread_width
        else:
            # Call spread: Position above upper bound
            short_strike_price = stock_price + implied_move_dollars + buffer
            long_strike_price = short_strike_price + spread_width

        # Find nearest available strikes
        available_strikes = sorted(option_chain.strikes, key=lambda s: float(s.price))

        short_strike = self._find_nearest_strike(available_strikes, short_strike_price)
        long_strike = self._find_nearest_strike(available_strikes, long_strike_price)

        if not short_strike or not long_strike:
            return None

        # Ensure proper ordering (short closer to price, long further)
        if below and short_strike < long_strike:
            return None
        if not below and short_strike > long_strike:
            return None

        return short_strike, long_strike

    def _find_nearest_strike(
        self, strikes: List[Strike], target_price: float
    ) -> Optional[Strike]:
        """Find the strike nearest to target price."""
        if not strikes:
            return None

        return min(strikes, key=lambda s: abs(float(s.price) - target_price))

    def _verify_strikes_outside_implied_move(
        self,
        ticker: str,
        strikes: Tuple[Strike, Strike],
        option_chain: OptionChain,
        vrp: VRPResult,
        below: bool,
    ) -> Optional[Tuple[Strike, Strike]]:
        """
        Verify that selected strikes are outside the implied move zone.

        Args:
            ticker: Ticker symbol
            strikes: Tuple of (short_strike, long_strike)
            option_chain: Options chain
            vrp: VRP analysis
            below: If True, check below price (puts), else above (calls)

        Returns:
            Original strikes if valid, None if inside implied move zone
        """
        stock_price = float(option_chain.stock_price.amount)
        implied_move_pct = vrp.implied_move_pct.value / 100
        implied_move_dollars = stock_price * implied_move_pct

        short_strike, long_strike = strikes
        short_price = float(short_strike.price)

        if below:
            # Put spread: short strike must be below lower bound
            lower_bound = stock_price - implied_move_dollars
            if short_price >= lower_bound:
                logger.debug(
                    f"{ticker}: Short strike ${short_price:.2f} is inside implied move "
                    f"(lower bound: ${lower_bound:.2f})"
                )
                return None
        else:
            # Call spread: short strike must be above upper bound
            upper_bound = stock_price + implied_move_dollars
            if short_price <= upper_bound:
                logger.debug(
                    f"{ticker}: Short strike ${short_price:.2f} is inside implied move "
                    f"(upper bound: ${upper_bound:.2f})"
                )
                return None

        return strikes

    def _calculate_spread_metrics(
        self,
        short_quote: OptionQuote,
        long_quote: OptionQuote,
        short_strike: Strike,
        long_strike: Strike,
    ) -> dict:
        """
        Calculate metrics for a vertical spread.

        Args:
            short_quote: Quote for short leg
            long_quote: Quote for long leg
            short_strike: Short strike
            long_strike: Long strike

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
            pop = 0.70  # Default 70% for ~30-delta

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

    def _calculate_contracts(self, max_loss_per_spread: Money) -> int:
        """
        Calculate number of contracts based on configured risk budget.

        Args:
            max_loss_per_spread: Max loss per single spread

        Returns:
            Number of contracts to trade
        """
        if max_loss_per_spread.amount <= 0:
            return 0

        contracts = int(self.config.risk_budget_per_trade / float(max_loss_per_spread.amount))

        # Minimum 1 contract, maximum from config for safety
        return max(1, min(contracts, self.config.max_contracts))

    def _calculate_position_greeks(
        self,
        legs_data: List[Tuple[Strike, OptionQuote, int]],
        contracts: int
    ) -> dict:
        """
        Calculate aggregated position Greeks across all legs.

        Position Greeks represent the net exposure of the entire strategy.
        - Delta: Net directional exposure (positive = bullish, negative = bearish)
        - Gamma: Rate of delta change (higher = more sensitive to price moves)
        - Theta: Daily P/L from time decay (positive = time is on our side)
        - Vega: IV sensitivity (positive = benefits from rising IV)

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
            # Delta: Accumulate directional exposure
            if quote.delta:
                delta_total += quote.delta * multiplier * contracts * 100
                has_greeks = True

            # Gamma: Accumulate convexity
            if quote.gamma:
                gamma_total += quote.gamma * multiplier * contracts * 100

            # Theta: Accumulate time decay (note: theta is negative for long options)
            if quote.theta:
                theta_total += quote.theta * multiplier * contracts * 100

            # Vega: Accumulate IV sensitivity
            if quote.vega:
                vega_total += quote.vega * multiplier * contracts * 100

        # Return None if no greeks available, otherwise return totals
        return {
            'delta': delta_total if has_greeks else None,
            'gamma': gamma_total if has_greeks else None,
            'theta': theta_total if has_greeks else None,
            'vega': vega_total if has_greeks else None,
        }

    def _combine_greeks(self, spread1: Strategy, spread2: Strategy) -> dict:
        """
        Combine position Greeks from two spreads (for iron condor).

        Args:
            spread1: First spread (e.g., put spread)
            spread2: Second spread (e.g., call spread)

        Returns:
            Dict with combined delta, gamma, theta, vega (each None if both spreads have None)
        """
        # Combine each Greek independently - None if both spreads have None for that Greek
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

    def _calculate_strategy_liquidity(
        self,
        ticker: str,
        option_chain: OptionChain,
        legs: List[StrategyLeg]
    ) -> dict:
        """
        Calculate liquidity metrics for strategy (package order optimized).

        For package orders (spreads/condors traded as single order), only SHORT legs
        matter for liquidity. Short strikes determine premium collected and fill quality.
        Long strikes are cheap protection - their liquidity is less important.

        Strategy types:
        - Vertical spreads (bull put, bear call): Check SHORT leg only
        - Iron condors/butterflies: Check BOTH short legs, use worst

        Args:
            ticker: Ticker symbol (for logging)
            option_chain: Options chain with quotes
            legs: List of strategy legs with strikes

        Returns:
            Dict with:
            - liquidity_tier: "EXCELLENT", "WARNING", or "REJECT"
            - min_open_interest: Minimum OI across SHORT legs only
            - max_spread_pct: Maximum spread % across SHORT legs only
        """
        # Guard clause: Empty legs should never happen, but be defensive
        if not legs:
            logger.warning(f"{ticker}: Empty legs list in liquidity calculation")
            return {
                'liquidity_tier': "REJECT",
                'min_open_interest': 0,
                'max_spread_pct': 100.0,
            }

        # For package orders: Only evaluate SHORT legs (where premium is collected)
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
            # Get the quote for this SHORT leg
            chain = option_chain.calls if leg.option_type == OptionType.CALL else option_chain.puts
            quote = chain.get(leg.strike)

            if not quote:
                # No quote found - mark as REJECT
                tiers.append("REJECT")
                oi_values.append(0)
                spread_pcts.append(100.0)
                continue

            # Score this individual option
            tier = self.liquidity_scorer.classify_option_tier(quote)
            tiers.append(tier)

            # Track metrics (use LiquidityScorer method for consistency)
            oi_values.append(quote.open_interest or 0)
            spread_pcts.append(self.liquidity_scorer.calculate_spread_pct(quote))

        # Overall tier = worst tier of SHORT legs only
        # Priority: REJECT > WARNING > EXCELLENT
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

