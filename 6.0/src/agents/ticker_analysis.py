"""TickerAnalysisAgent - Executes 2.0 analysis for single ticker.

This agent wraps 2.0's analyzer to provide VRP calculation, liquidity scoring,
and strategy generation for a single ticker.
"""

from typing import Dict, Any, Optional
from datetime import datetime, timedelta

from ..integration.container_2_0 import Container2_0
from ..utils.schemas import TickerAnalysisResponse
from .base import BaseAgent


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
        """Initialize agent with 2.0 container."""
        self.container = Container2_0()

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

            # Call 2.0's analyzer
            result = self.container.analyze_ticker(
                ticker=ticker,
                earnings_date=earnings_date,
                expiration=expiration,
                generate_strategies=generate_strategies
            )

            # Extract key fields from 2.0's result
            # Note: Adjust field names based on actual 2.0 analyzer output
            response_data = {
                'ticker': ticker,
                'vrp_ratio': self._extract_vrp_ratio(result),
                'recommendation': self._extract_recommendation(result),
                'liquidity_tier': self._extract_liquidity_tier(result),
                'score': self._extract_score(result),
                'strategies': self._extract_strategies(result) if generate_strategies else None,
                'error': None
            }

            # Validate response schema
            validated = TickerAnalysisResponse(**response_data)
            return validated.dict()

        except Exception as e:
            # Return error response
            return BaseAgent.create_error_response(
                agent_type="TickerAnalysisAgent",
                error_message=str(e),
                ticker=ticker
            )

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
        """Extract VRP ratio from 2.0 result."""
        # TODO: Adjust based on actual 2.0 result structure
        if hasattr(result, 'vrp_ratio'):
            return result.vrp_ratio
        elif isinstance(result, dict):
            return result.get('vrp_ratio')
        return None

    def _extract_recommendation(self, result: Any) -> Optional[str]:
        """Extract VRP recommendation from 2.0 result."""
        # TODO: Adjust based on actual 2.0 result structure
        if hasattr(result, 'vrp_recommendation'):
            return result.vrp_recommendation
        elif isinstance(result, dict):
            return result.get('vrp_recommendation') or result.get('recommendation')
        return None

    def _extract_liquidity_tier(self, result: Any) -> Optional[str]:
        """Extract liquidity tier from 2.0 result."""
        # TODO: Adjust based on actual 2.0 result structure
        if hasattr(result, 'liquidity_tier'):
            return result.liquidity_tier
        elif isinstance(result, dict):
            return result.get('liquidity_tier')
        return None

    def _extract_score(self, result: Any) -> Optional[int]:
        """Extract composite score from 2.0 result."""
        # TODO: Adjust based on actual 2.0 result structure
        if hasattr(result, 'composite_score'):
            return int(result.composite_score)
        elif isinstance(result, dict):
            score = result.get('composite_score') or result.get('score')
            return int(score) if score is not None else None
        return None

    def _extract_strategies(self, result: Any) -> Optional[list]:
        """Extract strategy recommendations from 2.0 result."""
        # TODO: Adjust based on actual 2.0 result structure
        if hasattr(result, 'strategies'):
            return result.strategies
        elif isinstance(result, dict):
            return result.get('strategies')
        return None

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
        return self.container.get_historical_moves(ticker, limit)
