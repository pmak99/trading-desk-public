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
            #     "historical_context": "Historical pattern...",
            #     "win_probability": 0.68
            # }
        """
        try:
            # Get historical context
            historical_summary = self._build_historical_summary(ticker)

            # Calculate win probability
            win_probability = self._calculate_win_probability(ticker, vrp_ratio)

            # Get sentiment context (if earnings date provided)
            sentiment_summary = ""
            if earnings_date:
                sentiment_summary = self._get_sentiment_summary(ticker, earnings_date)

            # Build explanation components
            explanation = self._build_explanation(
                ticker, vrp_ratio, historical_summary, sentiment_summary, win_probability
            )

            key_factors = self._extract_key_factors(
                ticker, vrp_ratio, sentiment_summary, win_probability
            )

            historical_context = self._build_historical_context(
                ticker, historical_summary
            )

            # Build response
            response_data = {
                'ticker': ticker,
                'explanation': explanation,
                'key_factors': key_factors,
                'historical_context': historical_context,
                'win_probability': win_probability
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
                'historical_context': "Historical data unavailable",
                'win_probability': None
            }

    def _build_historical_summary(self, ticker: str) -> str:
        """Build summary of historical earnings moves with pattern analysis."""
        try:
            moves = self.container.get_historical_moves(ticker, limit=12)

            if not moves:
                return "No historical earnings data available."

            # Calculate statistics
            move_values = [abs(m.get('gap_move_pct', 0)) for m in moves]
            avg_move = sum(move_values) / len(move_values) if move_values else 0
            max_move = max(move_values) if move_values else 0
            min_move = min(move_values) if move_values else 0

            # Count wins/losses (if direction data available)
            ups = sum(1 for m in moves if m.get('direction') == 'UP')
            downs = sum(1 for m in moves if m.get('direction') == 'DOWN')

            # Detect patterns
            patterns = []

            # Directional consistency
            if len(moves) >= 4:
                recent_4 = [m.get('direction') for m in moves[:4]]
                if recent_4.count('UP') >= 3:
                    patterns.append("consistently bullish (3/4 recent quarters up)")
                elif recent_4.count('DOWN') >= 3:
                    patterns.append("consistently bearish (3/4 recent quarters down)")

            # Volatility consistency (tight range vs wide range)
            if len(move_values) >= 4:
                recent_moves = move_values[:4]
                avg_recent = sum(recent_moves) / len(recent_moves)
                if max(recent_moves) / min(recent_moves) < 1.5:
                    patterns.append(f"stable moves around {avg_recent:.1f}%")
                elif max(recent_moves) / min(recent_moves) > 3:
                    patterns.append("high volatility with unpredictable moves")

            # Build summary
            summary = (
                f"Avg move: {avg_move:.1f}%, max: {max_move:.1f}%, min: {min_move:.1f}% "
                f"({ups} up, {downs} down in {len(moves)} qtrs)"
            )

            if patterns:
                summary += f". Pattern: {', '.join(patterns)}"

            return summary

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

    def _calculate_win_probability(self, ticker: str, vrp_ratio: float) -> Optional[float]:
        """
        Calculate estimated win probability based on VRP and historical patterns.

        Higher VRP generally correlates with higher win probability since
        we're selling overpriced volatility.

        Baseline probability from VRP:
        - VRP >= 7.0x: 75% base
        - VRP >= 4.0x: 65% base
        - VRP >= 3.0x: 55% base
        - VRP < 3.0x: 45% base

        Adjusted by historical consistency patterns.
        """
        try:
            # Base probability from VRP
            if vrp_ratio >= 7.0:
                base_prob = 0.75
            elif vrp_ratio >= 4.0:
                base_prob = 0.65
            elif vrp_ratio >= 3.0:
                base_prob = 0.55
            else:
                base_prob = 0.45

            # Adjust based on historical move consistency
            moves = self.container.get_historical_moves(ticker, limit=8)
            if not moves or len(moves) < 4:
                return base_prob  # Not enough data for adjustment

            move_values = [abs(m.get('gap_move_pct', 0)) for m in moves]
            if not move_values:
                return base_prob

            # Calculate coefficient of variation (std/mean)
            avg = sum(move_values) / len(move_values)
            if avg == 0:
                return base_prob

            variance = sum((x - avg) ** 2 for x in move_values) / len(move_values)
            std_dev = variance ** 0.5
            cv = std_dev / avg

            # Lower CV = more consistent moves = higher confidence
            # CV < 0.3: Very consistent (+5%)
            # CV > 0.7: Very volatile (-5%)
            if cv < 0.3:
                adjustment = 0.05
            elif cv > 0.7:
                adjustment = -0.05
            else:
                adjustment = 0.0

            # Cap at 80% max, 40% min
            final_prob = max(0.40, min(0.80, base_prob + adjustment))
            return round(final_prob, 2)

        except Exception:
            return None

    def _build_explanation(
        self,
        ticker: str,
        vrp_ratio: float,
        historical_summary: str,
        sentiment_summary: str,
        win_probability: Optional[float]
    ) -> str:
        """Build concise narrative explanation."""
        # Template explanation
        explanation = (
            f"VRP is {vrp_ratio:.1f}x, indicating implied volatility "
            f"significantly exceeds historical average. "
            f"{historical_summary}"
        )

        if win_probability:
            explanation += f" Estimated win probability: {win_probability:.0%}"

        if sentiment_summary and "unavailable" not in sentiment_summary.lower():
            explanation += f" {sentiment_summary}"

        return explanation

    def _extract_key_factors(
        self,
        ticker: str,
        vrp_ratio: float,
        sentiment_summary: str,
        win_probability: Optional[float]
    ) -> List[str]:
        """Extract key factors driving the opportunity."""
        factors = []

        # VRP factor with win probability
        if vrp_ratio >= 7.0:
            prob_str = f" ({win_probability:.0%} win prob)" if win_probability else ""
            factors.append(f"Exceptionally high VRP (7x+) indicates strong edge{prob_str}")
        elif vrp_ratio >= 4.0:
            prob_str = f" ({win_probability:.0%} win prob)" if win_probability else ""
            factors.append(f"Elevated VRP (4-7x) provides trading edge{prob_str}")

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
