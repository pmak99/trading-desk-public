"""
Finnhub API client for analyst recommendations and company news.

Provides two free-tier endpoints for the council sentiment consensus.
"""

import asyncio
import time
from typing import Dict, Any, List

import httpx

from src.core.logging import log
from src.core import metrics


BASE_URL = "https://finnhub.io/api/v1"


class FinnhubClient:
    """Async Finnhub API client with connection pooling."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=30)

    async def close(self):
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def _request(self, path: str, params: Dict[str, str] = None) -> Any:
        """Make request to Finnhub API with retry handling."""
        start_time = time.time()
        url = f"{BASE_URL}{path}"
        query = {"token": self.api_key}
        if params:
            query.update(params)

        for attempt in range(3):
            try:
                response = await self._client.get(url, params=query)

                if response.status_code != 200:
                    log("warn", "Finnhub API error",
                        path=path, status=response.status_code,
                        response=response.text[:200] if response.text else "empty")
                    if response.status_code in (429, 500, 502, 503) and attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    duration_ms = (time.time() - start_time) * 1000
                    metrics.api_call("finnhub", duration_ms, success=False)
                    return {"error": f"API error: {response.status_code}"}

                duration_ms = (time.time() - start_time) * 1000
                metrics.api_call("finnhub", duration_ms, success=True)
                return response.json()

            except (httpx.TimeoutException, httpx.ConnectError) as e:
                log("warn", "Finnhub request failed", error=str(e), attempt=attempt + 1)
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
                    continue
                duration_ms = (time.time() - start_time) * 1000
                metrics.api_call("finnhub", duration_ms, success=False)
                return {"error": str(e)}

        duration_ms = (time.time() - start_time) * 1000
        metrics.api_call("finnhub", duration_ms, success=False)
        return {"error": "All retries failed"}

    async def get_recommendations(self, ticker: str) -> Dict[str, Any]:
        """
        Get analyst recommendation trends.

        Returns most recent period's buy/sell/hold breakdown.
        On error returns {"error": "..."}.
        """
        data = await self._request("/stock/recommendation", {"symbol": ticker})

        if isinstance(data, dict) and data.get("error"):
            return data

        if not isinstance(data, list) or not data:
            return {"error": "No recommendation data"}

        # Most recent period
        latest = data[0]
        return {
            "strongBuy": latest.get("strongBuy", 0),
            "buy": latest.get("buy", 0),
            "hold": latest.get("hold", 0),
            "sell": latest.get("sell", 0),
            "strongSell": latest.get("strongSell", 0),
            "period": latest.get("period", ""),
        }

    async def get_company_news(
        self, ticker: str, from_date: str, to_date: str, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get company news articles.

        Returns list of news articles, truncated to limit.
        On error returns empty list.
        """
        data = await self._request("/company-news", {
            "symbol": ticker,
            "from": from_date,
            "to": to_date,
        })

        if isinstance(data, dict) and data.get("error"):
            log("warn", "Finnhub news error", ticker=ticker, error=data["error"])
            return []

        if not isinstance(data, list):
            return []

        return [
            {
                "headline": article.get("headline", ""),
                "summary": article.get("summary", ""),
                "source": article.get("source", ""),
                "datetime": article.get("datetime", 0),
            }
            for article in data[:limit]
        ]
