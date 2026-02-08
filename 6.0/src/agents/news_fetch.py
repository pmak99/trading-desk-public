"""NewsFetchAgent - Fetches recent company news via Finnhub API.

Provides recent headlines for context during analysis. Non-critical agent —
pipeline continues even if news fetch fails.
"""

import os
import logging
from typing import Dict, Any
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Finnhub news endpoint
_FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/company-news"
_REQUEST_TIMEOUT = 10.0
_MAX_HEADLINES = 5


class NewsFetchAgent:
    """Fetches recent company news headlines from Finnhub.

    Uses httpx for async HTTP calls. Gracefully degrades on any failure
    (missing API key, network error, etc.) — news is supplementary context,
    never a blocking dependency.

    Example:
        agent = NewsFetchAgent()
        result = await agent.fetch_news("PLTR", days_back=7)
        # Returns: {'ticker': 'PLTR', 'headlines': [...], 'count': 5, 'error': None}
    """

    def __init__(self):
        """Initialize with Finnhub API key from environment."""
        self.api_key = os.environ.get('FINNHUB_API_KEY', '')

    async def fetch_news(
        self,
        ticker: str,
        days_back: int = 7
    ) -> Dict[str, Any]:
        """Fetch recent news for a ticker.

        Args:
            ticker: Stock ticker symbol
            days_back: Number of days to look back for news

        Returns:
            Dict conforming to NewsFetchResponse schema
        """
        if not self.api_key:
            return {
                'ticker': ticker,
                'headlines': [],
                'count': 0,
                'error': 'FINNHUB_API_KEY not set - news unavailable'
            }

        try:
            import httpx

            today = datetime.now()
            from_date = (today - timedelta(days=days_back)).strftime('%Y-%m-%d')
            to_date = today.strftime('%Y-%m-%d')

            params = {
                'symbol': ticker,
                'from': from_date,
                'to': to_date,
                'token': self.api_key,
            }

            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                response = await client.get(_FINNHUB_NEWS_URL, params=params)
                response.raise_for_status()

                articles = response.json()

            # Extract top headlines
            headlines = []
            for article in articles[:_MAX_HEADLINES]:
                headline = {
                    'title': article.get('headline', ''),
                    'source': article.get('source', ''),
                    'url': article.get('url', ''),
                    'datetime': (
                        datetime.fromtimestamp(article['datetime']).strftime('%Y-%m-%d %H:%M')
                        if article.get('datetime')
                        else None
                    ),
                }
                if headline['title']:
                    headlines.append(headline)

            return {
                'ticker': ticker,
                'headlines': headlines,
                'count': len(headlines),
                'error': None
            }

        except ImportError:
            return {
                'ticker': ticker,
                'headlines': [],
                'count': 0,
                'error': 'httpx not installed'
            }
        except Exception as e:
            logger.warning(f"News fetch failed for {ticker}: {type(e).__name__}: {e}")
            return {
                'ticker': ticker,
                'headlines': [],
                'count': 0,
                'error': f'{type(e).__name__}: {e}'
            }
