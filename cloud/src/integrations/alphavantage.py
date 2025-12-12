"""
Alpha Vantage API client for earnings calendar.

Primary source for upcoming earnings dates.
"""

import asyncio
import csv
import io
import httpx
from typing import Dict, List, Any, Optional
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_result,
)

from src.core.logging import log

BASE_URL = "https://www.alphavantage.co/query"


def _is_rate_limited(response_text: str) -> bool:
    """Check if response indicates rate limiting."""
    if not response_text:
        return False
    # Alpha Vantage returns JSON error on rate limit
    return "rate limit" in response_text.lower() or "api call frequency" in response_text.lower()


class AlphaVantageClient:
    """Async Alpha Vantage API client."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
    )
    async def _request(
        self,
        function: str,
        params: Optional[Dict] = None
    ) -> str:
        """Make request to Alpha Vantage API with rate limit handling."""
        async with httpx.AsyncClient(timeout=30) as client:
            request_params = {
                "function": function,
                "apikey": self.api_key,
            }
            if params:
                request_params.update(params)

            response = await client.get(BASE_URL, params=request_params)

            # Handle 429 explicitly
            if response.status_code == 429:
                log("warn", "Alpha Vantage rate limited, waiting...")
                await asyncio.sleep(60)  # Wait 60s on rate limit
                raise httpx.HTTPStatusError(
                    "Rate limited",
                    request=response.request,
                    response=response
                )

            response.raise_for_status()
            text = response.text

            # Check for soft rate limit in response body
            if _is_rate_limited(text):
                log("warn", "Alpha Vantage soft rate limit detected")
                await asyncio.sleep(30)
                raise httpx.HTTPStatusError(
                    "Soft rate limit",
                    request=response.request,
                    response=response
                )

            return text

    def _parse_earnings_csv(self, csv_text: str) -> List[Dict[str, Any]]:
        """Parse CSV earnings calendar response."""
        reader = csv.DictReader(io.StringIO(csv_text))
        results = []

        for row in reader:
            results.append({
                "symbol": row.get("symbol", ""),
                "name": row.get("name", ""),
                "report_date": row.get("reportDate", ""),
                "fiscal_date_ending": row.get("fiscalDateEnding", ""),
                "estimate": row.get("estimate", ""),
                "currency": row.get("currency", ""),
            })

        return results

    async def get_earnings_calendar(
        self,
        symbol: Optional[str] = None,
        horizon: str = "3month"
    ) -> List[Dict[str, Any]]:
        """
        Get earnings calendar.

        Args:
            symbol: Filter by specific symbol (optional)
            horizon: "3month", "6month", or "12month" (default 3month)

        Returns:
            List of earnings records with symbol, name, report_date, estimate
        """
        log("debug", "Fetching earnings calendar", symbol=symbol, horizon=horizon)

        params = {"horizon": horizon}
        if symbol:
            params["symbol"] = symbol

        csv_text = await self._request("EARNINGS_CALENDAR", params)
        return self._parse_earnings_csv(csv_text)

    async def get_earnings_for_date(self, date: str) -> List[Dict[str, Any]]:
        """
        Get earnings for specific date.

        Args:
            date: Target date (YYYY-MM-DD)

        Returns:
            List of companies with earnings on that date
        """
        log("debug", "Fetching earnings for date", date=date)

        # Fetch full calendar and filter
        all_earnings = await self.get_earnings_calendar()
        return [e for e in all_earnings if e["report_date"] == date]
