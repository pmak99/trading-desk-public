"""SentimentFetchAgent - Fetches AI sentiment from Perplexity.

This agent calls Perplexity API via MCP to fetch sentiment data for
upcoming earnings, enabling pre-caching for fast /whisper lookups.
"""

import logging
from typing import Dict, Any, Optional
from pydantic import ValidationError

from ..integration.cache_4_0 import Cache4_0
from ..utils.schemas import SentimentFetchResponse
from .base import BaseAgent

logger = logging.getLogger(__name__)


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
                    try:
                        # Validate cached data with schema
                        validated = SentimentFetchResponse(**cached)
                        return validated.dict()
                    except Exception as e:
                        # Cache corruption - log and treat as cache miss
                        logger.warning(f"Invalid cached data for {ticker}, refetching: {e}")
                        # Continue to fetch fresh data below

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
            sentiment_data = self._fetch_via_mcp(ticker, earnings_date)

            # Validate with schema BEFORE recording budget/caching
            # This ensures we only track successful, valid responses
            validated = SentimentFetchResponse(**sentiment_data)

            # Only record API call after successful validation
            self.cache.record_call("perplexity", cost=0.006)

            # Cache the validated result
            self.cache.cache_sentiment(ticker, earnings_date, sentiment_data)

            return validated.dict()

        except ValidationError as e:
            # Pydantic validation failed - schema mismatch
            return BaseAgent.create_error_response(
                agent_type="SentimentFetchAgent",
                error_message=f"Validation error: {str(e)}",
                ticker=ticker
            )
        except Exception as e:
            # Catch-all for unexpected errors (MCP failures, network issues, etc.)
            error_type = type(e).__name__
            return BaseAgent.create_error_response(
                agent_type="SentimentFetchAgent",
                error_message=f"{error_type}: {str(e)}",
                ticker=ticker
            )

    def _fetch_via_mcp(
        self,
        ticker: str,
        earnings_date: str
    ) -> Dict[str, Any]:
        """
        Fetch sentiment via MCP Perplexity tools.

        Calls mcp__perplexity__perplexity_ask to get sentiment analysis
        for the ticker's upcoming earnings.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Earnings date (YYYY-MM-DD)

        Returns:
            Raw sentiment data dict matching SentimentFetchResponse schema

        Raises:
            Exception: If MCP call fails or response parsing fails
        """
        import re
        import json

        # Build prompt for Perplexity
        prompt = f"""
Analyze sentiment for {ticker} earnings on {earnings_date}.

Research:
1. Recent news and announcements (last 2 weeks)
2. Analyst sentiment and earnings expectations
3. Key catalysts that could drive post-earnings move
4. Key risks that could negatively impact stock

Focus on THIS SPECIFIC earnings event, not general stock sentiment.

Return ONLY valid JSON in this exact format:
{{
  "direction": "bullish|bearish|neutral",
  "score": <float between -1.0 and +1.0>,
  "catalysts": ["Catalyst 1", "Catalyst 2", "Catalyst 3"],
  "risks": ["Risk 1", "Risk 2"]
}}

Keep catalysts/risks concise (max 10 words each).
"""

        try:
            # Call MCP Perplexity tool
            # The tool is available as a global function in the Claude runtime
            response = self._call_perplexity_mcp(prompt)

            # Extract JSON from response (may be wrapped in markdown)
            response_text = response if isinstance(response, str) else str(response)

            # Try to find JSON in code blocks first
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
            if json_match:
                json_text = json_match.group(1)
            else:
                # Try to find raw JSON
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    json_text = json_match.group(0)
                else:
                    raise ValueError(f"No JSON found in response: {response_text[:200]}")

            # Parse JSON
            sentiment_data = json.loads(json_text)

            # Add ticker to response
            sentiment_data['ticker'] = ticker
            sentiment_data['error'] = None

            # Ensure required fields exist
            if 'direction' not in sentiment_data:
                sentiment_data['direction'] = 'neutral'
            if 'score' not in sentiment_data:
                sentiment_data['score'] = 0.0
            if 'catalysts' not in sentiment_data:
                sentiment_data['catalysts'] = []
            if 'risks' not in sentiment_data:
                sentiment_data['risks'] = []

            return sentiment_data

        except json.JSONDecodeError as e:
            raise Exception(f"Failed to parse JSON from Perplexity response: {e}")
        except ValueError as e:
            # JSON extraction failed
            raise Exception(f"Failed to extract JSON from response: {e}")
        except Exception as e:
            # Catch-all for MCP errors, network errors, etc.
            raise Exception(f"MCP Perplexity call failed: {e}")

    def _call_perplexity_mcp(self, prompt: str) -> str:
        """
        Call Perplexity MCP tool with the given prompt.

        This method handles the actual MCP tool invocation. In production,
        this calls the mcp__perplexity__perplexity_ask tool which is
        available in the Claude runtime environment.

        Args:
            prompt: The prompt to send to Perplexity

        Returns:
            Response text from Perplexity

        Raises:
            Exception: If MCP tool is not available or call fails
        """
        # In actual Claude runtime, mcp__perplexity__perplexity_ask is available
        # For testing/development, this will raise an exception
        try:
            # This function is available in Claude's runtime when MCP is configured
            response = mcp__perplexity__perplexity_ask(
                messages=[
                    {"role": "user", "content": prompt}
                ]
            )
            return response
        except NameError:
            # MCP tool not available (likely in test environment)
            raise Exception(
                "MCP Perplexity tool (mcp__perplexity__perplexity_ask) not available. "
                "Ensure Perplexity MCP server is configured in Claude."
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
