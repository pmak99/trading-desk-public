"""
Finnhub API client for analyst recommendations, company news, and earnings calendar.

Replaces Alpha Vantage for earnings calendar data (free tier: 60 req/min, 30k/month).
"""

import asyncio
import time
from datetime import date, timedelta
from typing import Dict, Any, List, Optional

import httpx

from src.core.logging import log
from src.core import metrics


BASE_URL = "https://finnhub.io/api/v1"

_HORIZON_DAYS = {"3month": 90, "6month": 180, "12month": 365}

# Finnhub silently truncates bulk /calendar/earnings responses at 1,500
# entries, keeping the LATEST dates — wide windows lose the nearest days.
# Mirrors 2.0/src/infrastructure/api/finnhub.py (fix both together).
FINNHUB_BULK_CAP = 1500
BULK_CHUNK_DAYS = 7


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

    async def get_earnings_calendar(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        symbol: Optional[str] = None,
        horizon: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Get upcoming earnings calendar.

        Args:
            from_date: Start date YYYY-MM-DD (default: today)
            to_date: End date YYYY-MM-DD (default: today + 30 days)
            symbol: Filter by ticker (optional)
            horizon: Convenience alias — "3month" / "6month" / "12month"

        Returns:
            List of dicts with keys: symbol, report_date, estimate, hour
        """
        today = date.today()
        if horizon:
            days = _HORIZON_DAYS.get(horizon, 90)
            start = from_date or today.isoformat()
            end = to_date or (today + timedelta(days=days)).isoformat()
        else:
            start = from_date or today.isoformat()
            end = to_date or (today + timedelta(days=30)).isoformat()

        if symbol:
            calendar = await self._fetch_calendar_window(start, end, symbol)
        else:
            # Bulk fetches must be chunked: Finnhub caps the response at
            # FINNHUB_BULK_CAP entries keeping the LATEST dates, so a wide
            # window silently drops the nearest days (found Jul 2026 — a
            # 30-day digest window can miss same-day earnings in season).
            calendar = await self._fetch_calendar_chunked(
                date.fromisoformat(start), date.fromisoformat(end)
            )

        seen = set()
        results = []
        for item in calendar:
            if not (item.get("symbol") and item.get("date")):
                continue
            key = (item["symbol"], item["date"])
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "symbol": item.get("symbol", ""),
                    "report_date": item.get("date", ""),
                    "name": "",
                    "estimate": str(item["epsEstimate"]) if item.get("epsEstimate") is not None else "",
                    "currency": "USD",
                    "fiscal_date_ending": "",
                    "hour": item.get("hour", ""),
                }
            )
        return results

    async def _fetch_calendar_window(
        self, start: str, end: str, symbol: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Single /calendar/earnings request. Returns raw entries (may be empty)."""
        params: Dict[str, str] = {"from": start, "to": end}
        if symbol:
            params["symbol"] = symbol

        data = await self._request("/calendar/earnings", params)

        if isinstance(data, dict) and data.get("error"):
            log("warn", "Finnhub earnings calendar error", error=data["error"])
            return []

        return data.get("earningsCalendar", []) if isinstance(data, dict) else []

    async def _fetch_calendar_chunked(
        self, from_date: date, to_date: date
    ) -> List[Dict[str, Any]]:
        """
        Bulk fetch in BULK_CHUNK_DAYS windows, splitting any window that comes
        back at FINNHUB_BULK_CAP entries (truncated) until it fits or is a
        single day.
        """
        entries: List[Dict[str, Any]] = []
        start = from_date
        while start <= to_date:
            chunk_end = min(start + timedelta(days=BULK_CHUNK_DAYS - 1), to_date)
            chunk = await self._fetch_calendar_window(
                start.isoformat(), chunk_end.isoformat()
            )

            if len(chunk) >= FINNHUB_BULK_CAP and chunk_end > start:
                mid = start + timedelta(days=(chunk_end - start).days // 2)
                entries.extend(await self._fetch_calendar_chunked(start, mid))
                entries.extend(
                    await self._fetch_calendar_chunked(mid + timedelta(days=1), chunk_end)
                )
            else:
                if len(chunk) >= FINNHUB_BULK_CAP:
                    log(
                        "warn",
                        "Finnhub calendar single-day response at cap — may be truncated",
                        date=start.isoformat(),
                        entries=len(chunk),
                    )
                entries.extend(chunk)

            start = chunk_end + timedelta(days=1)
        return entries

    async def get_earnings_for_date(self, target_date: str) -> List[Dict[str, Any]]:
        """Get earnings for a specific date (from and to are the same day)."""
        return await self.get_earnings_calendar(from_date=target_date, to_date=target_date)
