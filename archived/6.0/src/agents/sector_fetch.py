"""SectorFetchAgent - Fetches company sector/industry from Finnhub.

This agent retrieves company profile data from the Finnhub API
and caches it in the ticker_metadata table.
"""

from typing import Dict, Any, Optional
import logging
import os
import requests

from ..integration.ticker_metadata import TickerMetadataRepository

logger = logging.getLogger(__name__)

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"


class SectorFetchAgent:
    """
    Agent for fetching sector/industry data from Finnhub.

    Workflow:
    1. Check local cache (ticker_metadata table)
    2. If not cached, fetch from Finnhub via MCP
    3. Cache result for future use
    4. Return sector/industry data

    Example:
        agent = SectorFetchAgent()
        result = agent.fetch("NVDA")
        # Returns:
        # {
        #     'ticker': 'NVDA',
        #     'company_name': 'NVIDIA Corporation',
        #     'sector': 'Technology',
        #     'industry': 'Semiconductors',
        #     'cached': True
        # }
    """

    def __init__(self):
        """Initialize agent with metadata repository."""
        self.metadata_repo = TickerMetadataRepository()

    def fetch(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Fetch sector/industry data for ticker.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Dict with sector data or None if not found
        """
        ticker = ticker.upper()

        # Step 1: Check cache
        cached = self.metadata_repo.get_metadata(ticker)
        if cached:
            cached['cached'] = True
            return cached

        # Step 2: Fetch from Finnhub
        finnhub_data = self._fetch_from_finnhub(ticker)
        if finnhub_data is None:
            return None

        # Step 3: Cache result
        self.metadata_repo.save_metadata(
            ticker=ticker,
            company_name=finnhub_data.get('name', ticker),
            sector=finnhub_data.get('sector', 'Other'),
            industry=finnhub_data.get('industry', 'Unknown'),
            market_cap=finnhub_data.get('market_cap')
        )

        return {
            'ticker': ticker,
            'company_name': finnhub_data.get('name', ticker),
            'sector': finnhub_data.get('sector', 'Other'),
            'industry': finnhub_data.get('industry', 'Unknown'),
            'market_cap': finnhub_data.get('market_cap'),
            'cached': False
        }

    def _fetch_from_finnhub(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Fetch company profile from Finnhub REST API.

        Returns:
            Dict with name, sector, industry, market_cap or None on failure.
        """
        api_key = os.environ.get('FINNHUB_API_KEY', '')
        if not api_key:
            logger.warning("FINNHUB_API_KEY not set, skipping fetch")
            return None

        try:
            resp = requests.get(
                f"{FINNHUB_BASE_URL}/stock/profile2",
                params={"symbol": ticker, "token": api_key},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            if not data or not data.get('name'):
                logger.debug(f"No Finnhub profile for {ticker}")
                return None

            industry = data.get('finnhubIndustry', 'Unknown')
            sector = TickerMetadataRepository.map_industry_to_sector(industry)

            return {
                'name': data['name'],
                'sector': sector,
                'industry': industry,
                'market_cap': data.get('marketCapitalization'),
            }

        except requests.RequestException as e:
            logger.warning(f"Finnhub API error for {ticker}: {e}")
            return None

    def fetch_batch(self, tickers: list, delay_seconds: float = 1.0) -> Dict[str, Any]:
        """
        Fetch sector data for multiple tickers.

        Args:
            tickers: List of ticker symbols
            delay_seconds: Delay between API calls (rate limiting)

        Returns:
            Dict with results and errors
        """
        import time

        results = {}
        errors = []

        for ticker in tickers:
            try:
                result = self.fetch(ticker)
                if result:
                    results[ticker] = result
                else:
                    errors.append(ticker)

                # Rate limiting (only if we made an API call)
                if result and not result.get('cached'):
                    time.sleep(delay_seconds)

            except Exception as e:
                logger.error(f"Error fetching {ticker}: {e}")
                errors.append(ticker)

        return {
            'results': results,
            'errors': errors,
            'cached_count': sum(1 for r in results.values() if r.get('cached')),
            'fetched_count': sum(1 for r in results.values() if not r.get('cached'))
        }
