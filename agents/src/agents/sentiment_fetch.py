"""SentimentFetchAgent - Fetches AI sentiment from Perplexity.

This agent calls Perplexity API via MCP to fetch sentiment data for
upcoming earnings, enabling pre-caching for fast /whisper lookups.
"""

from typing import Dict, Any, Optional

from ..integration.cache_4_0 import Cache4_0
from ..utils.schemas import SentimentFetchResponse
from .base import BaseAgent


class SentimentFetchAgent:
    """
    Worker agent for fetching sentiment data.

    Uses MCP Perplexity tools to research:
    1. Recent news and announcements
    2. Analyst sentiment and expectations
    3. Key catalysts driving earnings expectations
    4. Key risks that could impact the stock

    Results are cached in 4.0's sentiment cache for 3 hours.

    Example:
        agent = SentimentFetchAgent()
        result = agent.fetch_sentiment("NVDA", "2026-02-05")
    """

    def __init__(self):
        """Initialize agent with cache."""
        self.cache = Cache4_0()

    def fetch_sentiment(
        self,
        ticker: str,
        earnings_date: str,
        force_refresh: bool = False
    ) -> Dict[str, Any]:
        """
        Fetch sentiment data for ticker earnings.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Earnings date (YYYY-MM-DD)
            force_refresh: If True, bypass cache and fetch fresh data

        Returns:
            Sentiment data dict conforming to SentimentFetchResponse schema

        Example:
            result = agent.fetch_sentiment("NVDA", "2026-02-05")
            # Returns:
            # {
            #     "ticker": "NVDA",
            #     "direction": "bullish",
            #     "score": 0.65,
            #     "catalysts": ["Datacenter demand strong", "AI growth accelerating"],
            #     "risks": ["Competition from AMD", "Supply constraints"],
            #     "error": None
            # }
        """
        try:
            # Check cache first (unless force refresh)
            if not force_refresh:
                cached = self.cache.get_cached_sentiment(ticker, earnings_date)
                if cached:
                    # Validate cached data with schema
                    validated = SentimentFetchResponse(**cached)
                    return validated.dict()

            # Check if budget allows API call
            if not self.cache.can_call_perplexity():
                return {
                    'ticker': ticker,
                    'direction': None,
                    'score': None,
                    'catalysts': [],
                    'risks': [],
                    'error': 'Perplexity API budget limit reached (40 calls/day)'
                }

            # Fetch sentiment via MCP Perplexity tools
            # NOTE: This is where MCP integration happens
            # For now, this is a placeholder for the actual MCP call
            sentiment_data = self._fetch_via_mcp(ticker, earnings_date)

            # Record API call for budget tracking
            self.cache.record_call("perplexity", cost=0.006)

            # Cache the result
            self.cache.cache_sentiment(ticker, earnings_date, sentiment_data)

            # Validate with schema
            validated = SentimentFetchResponse(**sentiment_data)
            return validated.dict()

        except Exception as e:
            # Return error response
            return BaseAgent.create_error_response(
                agent_type="SentimentFetchAgent",
                error_message=str(e),
                ticker=ticker
            )

    def _fetch_via_mcp(
        self,
        ticker: str,
        earnings_date: str
    ) -> Dict[str, Any]:
        """
        Fetch sentiment via MCP Perplexity tools.

        This is where we call mcp__perplexity__perplexity_ask to get
        sentiment analysis for the ticker's upcoming earnings.

        NOTE: This is a placeholder for actual MCP integration.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Earnings date (YYYY-MM-DD)

        Returns:
            Raw sentiment data dict

        Raises:
            NotImplementedError: MCP integration not yet complete
        """
        # TODO: Integrate with actual MCP Perplexity tool
        # This would call mcp__perplexity__perplexity_ask with a prompt like:
        #
        # prompt = f"""
        # Analyze sentiment for {ticker} earnings on {earnings_date}.
        #
        # Research:
        # 1. Recent news and announcements (last 2 weeks)
        # 2. Analyst sentiment and earnings expectations
        # 3. Key catalysts that could drive post-earnings move
        # 4. Key risks that could negatively impact stock
        #
        # Focus on THIS SPECIFIC earnings event, not general stock sentiment.
        #
        # Provide:
        # - Direction: bullish/bearish/neutral
        # - Score: -1.0 (very bearish) to +1.0 (very bullish)
        # - 2-3 key catalysts (max 10 words each)
        # - 1-2 key risks (max 10 words each)
        # """
        #
        # response = mcp__perplexity__perplexity_ask(messages=[
        #     {"role": "user", "content": prompt}
        # ])
        #
        # Then parse response into structured format

        raise NotImplementedError(
            "MCP Perplexity integration not yet implemented. "
            "This method will be implemented when integrating with MCP "
            "perplexity_ask tool."
        )

    def get_cached_sentiment(
        self,
        ticker: str,
        earnings_date: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get cached sentiment if available.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Earnings date (YYYY-MM-DD)

        Returns:
            Cached sentiment dict or None if not cached/expired
        """
        return self.cache.get_cached_sentiment(ticker, earnings_date)

    def check_budget_available(self) -> bool:
        """
        Check if Perplexity API budget allows more calls.

        Returns:
            True if budget allows calls, False if limit reached
        """
        return self.cache.can_call_perplexity()

    def get_budget_status(self) -> Dict[str, Any]:
        """
        Get current budget status.

        Returns:
            Budget status dict with daily/monthly usage
        """
        return self.cache.get_budget_status()
