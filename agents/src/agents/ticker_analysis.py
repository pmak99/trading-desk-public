"""TickerAnalysisAgent - Executes 2.0 analysis for single ticker.

This agent wraps 2.0's analyzer to provide VRP calculation, liquidity scoring,
and strategy generation for a single ticker.
"""

from typing import Dict, Any, Optional, Tuple
from datetime import datetime, timedelta
import logging
import sys
from pathlib import Path

# Add 2.0 to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent / "2.0"))
from src.application.filters.weekly_options import has_weekly_options

from ..integration.container_2_0 import Container2_0
from ..integration.position_limits import PositionLimitsRepository
from ..utils.schemas import TickerAnalysisResponse
from .base import BaseAgent

logger = logging.getLogger(__name__)


class TickerAnalysisAgent:
    """
    Worker agent for analyzing a single ticker.

    Executes 2.0's full analysis pipeline:
    1. VRP calculation (implied move vs historical average)
    2. Liquidity scoring (4-tier system)
    3. Strategy generation (if requested)
    4. Composite scoring

    Example:
        agent = TickerAnalysisAgent()
        result = agent.analyze("NVDA", "2026-02-05")
    """

    def __init__(self):
        """Initialize agent with 2.0 container and position limits repo."""
        self.container = Container2_0()
        self.position_limits_repo = PositionLimitsRepository()

    def analyze(
        self,
        ticker: str,
        earnings_date: str,
        expiration: Optional[str] = None,
        generate_strategies: bool = True
    ) -> Dict[str, Any]:
        """
        Analyze ticker for earnings opportunity.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Earnings date (YYYY-MM-DD)
            expiration: Options expiration date (YYYY-MM-DD), auto-calculated if None
            generate_strategies: Whether to generate strategy recommendations

        Returns:
            Analysis result dict conforming to TickerAnalysisResponse schema

        Example:
            result = agent.analyze("NVDA", "2026-02-05")
            # Returns:
            # {
            #     "ticker": "NVDA",
            #     "vrp_ratio": 6.2,
            #     "recommendation": "EXCELLENT",
            #     "liquidity_tier": "GOOD",
            #     "score": 78,
            #     "strategies": [...],
            #     "error": None
            # }
        """
        try:
            # Auto-calculate expiration if not provided
            if expiration is None:
                expiration = self._calculate_expiration(earnings_date)

            # Convert dates to date objects (2.0 expects date objects, not strings)
            earnings_date_obj = datetime.strptime(earnings_date, '%Y-%m-%d').date()
            expiration_date_obj = datetime.strptime(expiration, '%Y-%m-%d').date()

            # Call 2.0's analyzer
            result = self.container.analyze_ticker(
                ticker=ticker,
                earnings_date=earnings_date_obj,
                expiration=expiration_date_obj,
                generate_strategies=generate_strategies
            )

            # Handle Result type from 2.0
            if hasattr(result, 'is_err') and result.is_err:
                error_msg = str(result.unwrap_err())
                logger.error(f"2.0 analyzer error for {ticker}: {error_msg}")
                error_response = BaseAgent.create_error_response(
                    agent_type="TickerAnalysisAgent",
                    error_message=error_msg,
                    ticker=ticker
                )
                # Add earnings_date to error response
                error_response['earnings_date'] = earnings_date
                return error_response

            # Unwrap Result value
            analysis_result = result.value if hasattr(result, 'value') else result

            # Validate unwrapped value is not None (Result.Ok(None) is possible)
            if analysis_result is None:
                logger.error(f"2.0 analyzer returned Ok(None) for {ticker}")
                error_response = BaseAgent.create_error_response(
                    agent_type="TickerAnalysisAgent",
                    error_message="Analyzer returned empty result",
                    ticker=ticker
                )
                error_response['earnings_date'] = earnings_date
                return error_response

            # Check weekly options availability
            has_weeklies, weekly_reason = self._check_weekly_options(ticker, earnings_date)

            # Extract key fields from 2.0's result
            response_data = {
                'ticker': ticker,
                'earnings_date': earnings_date,  # Include earnings date in response
                'vrp_ratio': self._extract_vrp_ratio(analysis_result),
                'recommendation': self._extract_recommendation(analysis_result),
                'liquidity_tier': self._extract_liquidity_tier(analysis_result),
                'score': self._extract_score(analysis_result),
                'strategies': self._extract_strategies(analysis_result) if generate_strategies else None,
                'position_limits': self._get_position_limits(ticker),
                'has_weekly_options': has_weeklies,
                'weekly_reason': weekly_reason,
                'error': None
            }

            # Validate response schema
            validated = TickerAnalysisResponse(**response_data)
            return validated.dict()

        except Exception as e:
            # Return error response
            logger.error(f"Error analyzing {ticker}: {e}")
            error_response = BaseAgent.create_error_response(
                agent_type="TickerAnalysisAgent",
                error_message=str(e),
                ticker=ticker
            )
            # Add earnings_date to error response
            error_response['earnings_date'] = earnings_date
            return error_response

    def _calculate_expiration(self, earnings_date: str) -> str:
        """
        Calculate options expiration date (Friday after earnings).

        Args:
            earnings_date: Earnings date (YYYY-MM-DD)

        Returns:
            Expiration date (YYYY-MM-DD)
        """
        earnings_dt = datetime.strptime(earnings_date, '%Y-%m-%d')

        # Find next Friday after earnings
        days_until_friday = (4 - earnings_dt.weekday()) % 7
        if days_until_friday == 0:
            days_until_friday = 7  # If earnings is Friday, use next Friday

        expiration_dt = earnings_dt + timedelta(days=days_until_friday)
        return expiration_dt.strftime('%Y-%m-%d')

    def _extract_vrp_ratio(self, result: Any) -> Optional[float]:
        """Extract VRP ratio from 2.0 TickerAnalysis result."""
        # TickerAnalysis has vrp: VRPResult with vrp_ratio field
        if hasattr(result, 'vrp') and hasattr(result.vrp, 'vrp_ratio'):
            return result.vrp.vrp_ratio
        return None

    def _extract_recommendation(self, result: Any) -> Optional[str]:
        """Extract VRP recommendation from 2.0 TickerAnalysis result."""
        # TickerAnalysis has vrp: VRPResult with recommendation (enum)
        if hasattr(result, 'vrp') and hasattr(result.vrp, 'recommendation'):
            # Convert enum to string and uppercase (schema expects uppercase)
            rec = result.vrp.recommendation
            rec_str = rec.value if hasattr(rec, 'value') else str(rec)
            return rec_str.upper()
        return None

    def _extract_liquidity_tier(self, result: Any) -> Optional[str]:
        """
        Extract liquidity tier from 2.0 result.

        Note: Liquidity tier is added to Strategy objects by strategy_generator,
        not in TickerAnalysis. If strategies are generated, get tier from
        recommended strategy. Otherwise return None.
        """
        if hasattr(result, 'strategies') and result.strategies:
            # strategies is StrategyRecommendation with recommended_strategy property
            strategy = result.strategies.recommended_strategy
            if hasattr(strategy, 'liquidity_tier'):
                return strategy.liquidity_tier
        return None

    def _extract_score(self, result: Any) -> Optional[int]:
        """
        Extract composite score from 2.0 result.

        Note: TickerAnalysis doesn't have composite_score. That's in a separate
        TickerScore object from the scorer service. For now, we'll compute a
        simple score from VRP ratio (main driver of quality).

        Simplified scoring:
        - VRP >= 7.0x: 90-100 (EXCELLENT)
        - VRP >= 4.0x: 70-89 (GOOD)
        - VRP >= 1.5x: 50-69 (MARGINAL)
        - VRP < 1.5x: 0-49 (SKIP)
        """
        vrp_ratio = self._extract_vrp_ratio(result)
        if vrp_ratio is None:
            return None

        # Simple scoring based on VRP thresholds (BALANCED mode)
        if vrp_ratio >= 1.8:
            # EXCELLENT: 70-100
            return min(100, int(70 + (vrp_ratio - 1.8) * 10))
        elif vrp_ratio >= 1.4:
            # GOOD: 55-69
            return int(55 + (vrp_ratio - 1.4) * (15/0.4))
        elif vrp_ratio >= 1.2:
            # MARGINAL: 40-54
            return int(40 + (vrp_ratio - 1.2) * (15/0.2))
        else:
            # SKIP: 0-39
            return int(vrp_ratio * 33)

    def _extract_strategies(self, result: Any) -> Optional[list]:
        """
        Extract strategy recommendations from 2.0 TickerAnalysis result.

        Returns simplified dict representation of strategies.
        """
        if not hasattr(result, 'strategies') or not result.strategies:
            return None

        # strategies is StrategyRecommendation object
        strategy_rec = result.strategies

        # Convert strategies list to dicts
        strategies_list = []
        for strategy in strategy_rec.strategies:
            strategy_dict = {
                'type': strategy.strategy_type.value if hasattr(strategy.strategy_type, 'value') else str(strategy.strategy_type),
                'strikes': strategy.strike_description if hasattr(strategy, 'strike_description') else None,
                'max_profit': float(strategy.max_profit.amount) if hasattr(strategy.max_profit, 'amount') else float(strategy.max_profit),
                'max_loss': float(strategy.max_loss.amount) if hasattr(strategy.max_loss, 'amount') else float(strategy.max_loss),
                'probability_of_profit': strategy.probability_of_profit,
                'contracts': strategy.contracts
            }
            strategies_list.append(strategy_dict)

        return strategies_list

    def get_historical_moves(
        self,
        ticker: str,
        limit: int = 12
    ) -> list:
        """
        Get historical earnings moves for ticker.

        Args:
            ticker: Stock ticker symbol
            limit: Maximum number of historical moves

        Returns:
            List of historical move records
        """
        result = self.container.get_historical_moves(ticker, limit)

        # Handle Result type if present
        if hasattr(result, 'is_err'):
            if result.is_err:
                logger.error(f"Error getting historical moves for {ticker}: {result.unwrap_err()}")
                return []
            value = result.value if hasattr(result, 'value') else result
            # Guard against Ok(None)
            return value if value is not None else []

        return result if result is not None else []

    def _get_position_limits(self, ticker: str) -> Optional[Dict[str, Any]]:
        """Get position limits for ticker if available."""
        try:
            return self.position_limits_repo.get_limits(ticker)
        except Exception as e:
            logger.debug(f"Error fetching position limits for {ticker}: {e}")
            return None

    def _check_weekly_options(self, ticker: str, earnings_date: str) -> Tuple[bool, str]:
        """
        Check if ticker has weekly options available.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Earnings date (YYYY-MM-DD)

        Returns:
            Tuple of (has_weeklies, reason)
        """
        try:
            # Get expirations from 2.0's tradier client (via inner container)
            result = self.container.container.tradier.get_expirations(ticker)
            if hasattr(result, 'is_err') and result.is_err:
                # On error, be permissive
                return True, "Unable to check expirations"

            expirations = result.value if hasattr(result, 'value') else result

            # Convert date objects to strings for has_weekly_options
            expirations_list = [exp.isoformat() for exp in expirations]

            return has_weekly_options(expirations_list, earnings_date)

        except Exception as e:
            logger.warning(f"Error checking weekly options for {ticker}: {e}")
            # On error, be permissive - don't block trading opportunities
            return True, f"Check failed: {e}"
