"""ExplanationAgent - Generates narrative explanations for VRP opportunities.

This agent automates the manual Perplexity workflow by providing context
on why VRP is elevated and what's driving the options market pricing.
"""

from typing import Dict, Any, List, Optional

from ..integration.container_2_0 import Container2_0
from ..integration.cache_4_0 import Cache4_0
from ..utils.schemas import ExplanationResponse
from .base import BaseAgent


class ExplanationAgent:
    """
    Worker agent for generating explanations.

    Provides narrative reasoning for:
    - Why is VRP elevated?
    - What's driving the move expectation?
    - Historical context and patterns
    - Key risks to consider

    This automates the manual step where user copies ticker to Perplexity
    for context analysis.

    Example:
        agent = ExplanationAgent()
        result = agent.explain(
            ticker="NVDA",
            vrp_ratio=6.2,
            liquidity_tier="GOOD"
        )
    """

    def __init__(self):
        """Initialize agent with containers."""
        self.container = Container2_0()
        self.cache = Cache4_0()

    def explain(
        self,
        ticker: str,
        vrp_ratio: float,
        liquidity_tier: str,
        earnings_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate explanation for ticker opportunity.

        Args:
            ticker: Stock ticker symbol
            vrp_ratio: VRP ratio (e.g., 6.2)
            liquidity_tier: Liquidity tier (EXCELLENT/GOOD/WARNING/REJECT)
            earnings_date: Earnings date for sentiment lookup

        Returns:
            Explanation dict conforming to ExplanationResponse schema

        Example:
            result = agent.explain("NVDA", 6.2, "GOOD")
            # Returns:
            # {
            #     "ticker": "NVDA",
            #     "explanation": "VRP is 6.2x because...",
            #     "key_factors": ["Factor 1", "Factor 2"],
            #     "historical_context": "Historical pattern..."
            # }
        """
        try:
            # Get historical context
            historical_summary = self._build_historical_summary(ticker)

            # Get sentiment context (if earnings date provided)
            sentiment_summary = ""
            if earnings_date:
                sentiment_summary = self._get_sentiment_summary(ticker, earnings_date)

            # Build explanation components
            explanation = self._build_explanation(
                ticker, vrp_ratio, historical_summary, sentiment_summary
            )

            key_factors = self._extract_key_factors(
                ticker, vrp_ratio, sentiment_summary
            )

            historical_context = self._build_historical_context(
                ticker, historical_summary
            )

            # Build response
            response_data = {
                'ticker': ticker,
                'explanation': explanation,
                'key_factors': key_factors,
                'historical_context': historical_context
            }

            # Validate response schema
            validated = ExplanationResponse(**response_data)
            return validated.dict()

        except Exception as e:
            # Return minimal valid response on error
            return {
                'ticker': ticker,
                'explanation': f"Explanation unavailable: {str(e)}",
                'key_factors': [],
                'historical_context': "Historical data unavailable"
            }

    def _build_historical_summary(self, ticker: str) -> str:
        """Build summary of historical earnings moves."""
        try:
            moves = self.container.get_historical_moves(ticker, limit=12)

            if not moves:
                return "No historical earnings data available."

            # Calculate statistics
            move_values = [abs(m.get('gap_move_pct', 0)) for m in moves]
            avg_move = sum(move_values) / len(move_values) if move_values else 0
            max_move = max(move_values) if move_values else 0

            # Count wins/losses (if direction data available)
            ups = sum(1 for m in moves if m.get('direction') == 'UP')
            downs = sum(1 for m in moves if m.get('direction') == 'DOWN')

            return (
                f"Historical average move: {avg_move:.1f}%, "
                f"max move: {max_move:.1f}% "
                f"({ups} up, {downs} down in last {len(moves)} quarters)"
            )

        except Exception:
            return "Historical data unavailable"

    def _get_sentiment_summary(self, ticker: str, earnings_date: str) -> str:
        """Get sentiment summary from cache or build generic."""
        try:
            cached = self.cache.get_cached_sentiment(ticker, earnings_date)

            if cached:
                direction = cached.get('direction', 'neutral')
                score = cached.get('score', 0)
                catalysts = cached.get('catalysts', [])

                catalysts_str = ", ".join(catalysts[:3]) if catalysts else "none noted"

                return (
                    f"Sentiment: {direction} (score: {score:.2f}). "
                    f"Key catalysts: {catalysts_str}"
                )

            return "Sentiment data not yet cached (run /prime first)"

        except Exception:
            return "Sentiment unavailable"

    def _build_explanation(
        self,
        ticker: str,
        vrp_ratio: float,
        historical_summary: str,
        sentiment_summary: str
    ) -> str:
        """Build concise narrative explanation."""
        # Template explanation
        explanation = (
            f"VRP is {vrp_ratio:.1f}x, indicating implied volatility "
            f"significantly exceeds historical average. "
            f"{historical_summary}"
        )

        if sentiment_summary and "unavailable" not in sentiment_summary.lower():
            explanation += f" {sentiment_summary}"

        return explanation

    def _extract_key_factors(
        self,
        ticker: str,
        vrp_ratio: float,
        sentiment_summary: str
    ) -> List[str]:
        """Extract key factors driving the opportunity."""
        factors = []

        # VRP factor
        if vrp_ratio >= 7.0:
            factors.append("Exceptionally high VRP (7x+) indicates strong edge")
        elif vrp_ratio >= 4.0:
            factors.append("Elevated VRP (4-7x) provides trading edge")

        # Parse sentiment factors if available
        if "catalysts:" in sentiment_summary.lower():
            catalysts_part = sentiment_summary.split("catalysts:")[1].strip()
            if catalysts_part and "none" not in catalysts_part.lower():
                factors.append(f"Market catalysts: {catalysts_part}")

        # Generic market factor if no specific catalysts
        if len(factors) == 1:
            factors.append("Options market pricing elevated move expectations")

        return factors[:5]  # Max 5 factors

    def _build_historical_context(
        self,
        ticker: str,
        historical_summary: str
    ) -> str:
        """Build historical context description."""
        return f"{ticker} historical earnings behavior: {historical_summary}"
