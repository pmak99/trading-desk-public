"""
Tradier API client for options data.

Replaces MCP tradier integration with direct REST calls.
"""

import httpx
from typing import Dict, List, Any, Optional
from tenacity import retry, stop_after_attempt, wait_exponential

from src.core.logging import log

BASE_URL = "https://api.tradier.com/v1"


class TradierClient:
    """Async Tradier API client."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def _request(
        self,
        endpoint: str,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make authenticated request to Tradier API."""
        async with httpx.AsyncClient(timeout=30) as client:
            url = f"{BASE_URL}/{endpoint}"
            response = await client.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            return response.json()

    async def get_quote(self, symbol: str) -> Dict[str, Any]:
        """Get stock quote."""
        log("debug", "Fetching quote", symbol=symbol)
        data = await self._request("markets/quotes", {"symbols": symbol})

        quote = data.get("quotes", {}).get("quote", {})
        if isinstance(quote, list):
            quote = quote[0] if quote else {}

        return quote

    async def get_options_chain(
        self,
        symbol: str,
        expiration: str,
        greeks: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get options chain for symbol and expiration.

        Args:
            symbol: Stock symbol
            expiration: Expiration date (YYYY-MM-DD)
            greeks: Include Greeks in response

        Returns:
            List of option contracts
        """
        log("debug", "Fetching options chain", symbol=symbol, expiration=expiration)

        params = {
            "symbol": symbol,
            "expiration": expiration,
            "greeks": str(greeks).lower(),
        }

        data = await self._request("markets/options/chains", params)

        options = data.get("options", {}).get("option", [])
        if not isinstance(options, list):
            options = [options] if options else []

        return options

    async def get_expirations(self, symbol: str) -> List[str]:
        """Get available expiration dates."""
        log("debug", "Fetching expirations", symbol=symbol)
        data = await self._request("markets/options/expirations", {"symbol": symbol})

        expirations = data.get("expirations", {}).get("date", [])
        if not isinstance(expirations, list):
            expirations = [expirations] if expirations else []

        return expirations
