"""
Tradier API client for options data.

Replaces MCP tradier integration with direct REST calls.
"""

import asyncio
import time
import httpx
from typing import Dict, List, Any, Optional

from src.core.logging import log, get_request_id
from src.core import metrics

BASE_URL = "https://api.tradier.com/v1"


class TradierRateLimitError(Exception):
    """Raised when Tradier returns 429 rate limit."""
    def __init__(self, retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__(f"Rate limited, retry after {retry_after}s")


class TradierClient:
    """Async Tradier API client with rate limit handling."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }

    async def _handle_rate_limit(self, response: httpx.Response) -> None:
        """Handle 429 rate limit response with retry-after."""
        if response.status_code == 429:
            # Parse Retry-After header (seconds to wait)
            retry_after = int(response.headers.get("Retry-After", "60"))
            log("warn", "Tradier rate limit hit", retry_after=retry_after)
            raise TradierRateLimitError(retry_after)

    async def _request(
        self,
        endpoint: str,
        params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Make authenticated request to Tradier API with retry handling."""
        start_time = time.time()
        success = False

        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    url = f"{BASE_URL}/{endpoint}"
                    # Include request ID for distributed tracing
                    headers = {**self.headers, "X-Request-ID": get_request_id()}
                    response = await client.get(url, headers=headers, params=params)

                    # Handle rate limit specially
                    if response.status_code == 429:
                        retry_after = int(response.headers.get("Retry-After", "60"))
                        log("warn", "Tradier rate limit, waiting", seconds=retry_after)
                        await asyncio.sleep(min(retry_after, 120))
                        continue

                    if response.status_code != 200:
                        log("warn", "Tradier API error",
                            endpoint=endpoint,
                            status=response.status_code,
                            response=response.text[:200] if response.text else "empty")
                        if attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        duration_ms = (time.time() - start_time) * 1000
                        metrics.api_call("tradier", duration_ms, success=False)
                        return {}

                    # Handle empty responses gracefully
                    if not response.content:
                        log("warn", "Tradier returned empty response", endpoint=endpoint)
                        duration_ms = (time.time() - start_time) * 1000
                        metrics.api_call("tradier", duration_ms, success=False)
                        return {}

                    try:
                        result = response.json()
                        duration_ms = (time.time() - start_time) * 1000
                        metrics.api_call("tradier", duration_ms, success=True)
                        return result
                    except ValueError:
                        log("error", "Tradier returned invalid JSON",
                            endpoint=endpoint,
                            status=response.status_code,
                            content=response.text[:200] if response.text else "empty")
                        duration_ms = (time.time() - start_time) * 1000
                        metrics.api_call("tradier", duration_ms, success=False)
                        return {}

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                log("warn", "Tradier request failed", endpoint=endpoint, error=str(e), attempt=attempt+1)
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                duration_ms = (time.time() - start_time) * 1000
                metrics.api_call("tradier", duration_ms, success=False)
                return {}

        duration_ms = (time.time() - start_time) * 1000
        metrics.api_call("tradier", duration_ms, success=False)
        return {}

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
