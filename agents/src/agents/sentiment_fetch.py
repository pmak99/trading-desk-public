"""SentimentFetchAgent - Fetches AI sentiment from Perplexity.

This agent calls Perplexity API directly to fetch sentiment data for
upcoming earnings, enabling pre-caching for fast /whisper lookups.
"""

import logging
import asyncio
from typing import Dict, Any, Optional
from pydantic import ValidationError

from ..integration.cache_4_0 import Cache4_0
from ..integration.perplexity_5_0 import Perplexity5_0
from ..utils.schemas import SentimentFetchResponse
from .base import BaseAgent

logger = logging.getLogger(__name__)


class SentimentFetchAgent:
    """
    Worker agent for fetching sentiment data.

    Uses Perplexity API to research:
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
        """Initialize agent with cache and Perplexity client."""
        self.cache = Cache4_0()
        try:
            self.perplexity = Perplexity5_0()
        except ValueError as e:
            logger.warning(f"Perplexity client initialization failed: {e}")
            self.perplexity = None

    async def fetch_sentiment(
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
            result = await agent.fetch_sentiment("NVDA", "2026-02-05")
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
                        return validated.model_dump()
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

            # Check if Perplexity client is available
            if not self.perplexity:
                return {
                    'ticker': ticker,
                    'direction': None,
                    'score': None,
                    'catalysts': [],
                    'risks': [],
                    'error': 'Perplexity client not initialized (check PERPLEXITY_API_KEY)'
                }

            # Fetch sentiment via Perplexity API
            sentiment_data = await self._fetch_via_api(ticker, earnings_date)

            # Validate with schema BEFORE recording budget/caching
            # This ensures we only track successful, valid responses
            validated = SentimentFetchResponse(**sentiment_data)

            # Only record API call after successful validation
            self.cache.record_call(cost=0.006)

            # Cache the validated result
            self.cache.cache_sentiment(ticker, earnings_date, sentiment_data)

            # Return dict with success field (property not included by default)
            result = validated.model_dump()
            result['success'] = validated.success
            return result

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

    # Number of retries for transient API failures
    _API_RETRY_COUNT = 1
    _API_RETRY_DELAY_SECONDS = 2.0

    async def _fetch_via_api(
        self,
        ticker: str,
        earnings_date: str
    ) -> Dict[str, Any]:
        """
        Fetch sentiment via Perplexity API with retry for transient failures.

        Calls Perplexity API directly to get sentiment analysis
        for the ticker's upcoming earnings. Retries once on transient failure.

        Args:
            ticker: Stock ticker symbol
            earnings_date: Earnings date (YYYY-MM-DD)

        Returns:
            Raw sentiment data dict matching SentimentFetchResponse schema

        Raises:
            Exception: If API call fails after retries
        """
        last_error = None

        for attempt in range(1 + self._API_RETRY_COUNT):
            try:
                # Call Perplexity API (async)
                result = await self.perplexity.get_sentiment(ticker, earnings_date)

                # Check if API call was successful
                if not result.get('success'):
                    raise Exception(result.get('error', 'Unknown API error'))

                # Convert API response to sentiment data format
                sentiment_data = {
                    'ticker': ticker,
                    'direction': result['direction'],
                    'score': result['score'],
                    'catalysts': result.get('tailwinds', '').split('\n') if result.get('tailwinds') else [],
                    'risks': result.get('headwinds', '').split('\n') if result.get('headwinds') else [],
                    'error': None
                }

                # Clean up lists (remove empty strings)
                sentiment_data['catalysts'] = [c.strip() for c in sentiment_data['catalysts'] if c.strip()]
                sentiment_data['risks'] = [r.strip() for r in sentiment_data['risks'] if r.strip()]

                return sentiment_data

            except Exception as e:
                last_error = e
                if attempt < self._API_RETRY_COUNT:
                    logger.warning(
                        f"Perplexity API attempt {attempt + 1} failed for {ticker}, "
                        f"retrying in {self._API_RETRY_DELAY_SECONDS}s: {e}"
                    )
                    await asyncio.sleep(self._API_RETRY_DELAY_SECONDS)

        raise Exception(f"Perplexity API call failed after {1 + self._API_RETRY_COUNT} attempts: {last_error}")

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
