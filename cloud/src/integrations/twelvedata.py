"""
Twelve Data client for stock data.

Reliable source for historical prices and current quotes.
Free tier: 800 API calls/day, 8 calls/minute.
"""

import asyncio
import os
import time
from typing import Dict, Any, Optional, List
from datetime import date, timedelta

import httpx

from src.core.logging import log

# Rate limit protection (8 calls/min = 1 call per 7.5 seconds)
MIN_REQUEST_INTERVAL = 7.5

# API Configuration
TWELVE_DATA_KEY = os.environ.get("TWELVE_DATA_KEY", "")
BASE_URL = "https://api.twelvedata.com"


class TwelveDataClient:
    """Async client for Twelve Data API."""

    def __init__(self, api_key: Optional[str] = None):
        self._api_key = api_key or TWELVE_DATA_KEY
        self._client: Optional[httpx.AsyncClient] = None
        self._rate_lock = asyncio.Lock()
        self._last_request_time = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _request(self, endpoint: str, params: Dict[str, Any], symbol: str = "unknown") -> Optional[Dict]:
        """Make rate-limited API request with retry."""
        if not self._api_key:
            log("error", "TWELVE_DATA_KEY not configured")
            return None

        # Thread-safe rate limit protection
        async with self._rate_lock:
            now = time.time()
            wait_time = MIN_REQUEST_INTERVAL - (now - self._last_request_time)
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._last_request_time = time.time()

        params["apikey"] = self._api_key
        url = f"{BASE_URL}/{endpoint}"

        client = await self._get_client()

        # Retry logic
        last_error = None
        for attempt in range(3):
            try:
                response = await client.get(url, params=params)
                data = response.json()

                # Check for API errors
                if data.get("status") == "error":
                    error_msg = data.get("message", "Unknown error")
                    if "API key" in error_msg:
                        log("error", "Twelve Data API key invalid", symbol=symbol)
                        return None
                    if "rate limit" in error_msg.lower():
                        wait = (attempt + 1) * 10
                        log("warn", f"Twelve Data rate limited, waiting {wait}s",
                            symbol=symbol, attempt=attempt + 1)
                        await asyncio.sleep(wait)
                        async with self._rate_lock:
                            self._last_request_time = time.time()
                        continue
                    log("warn", "Twelve Data API error", symbol=symbol, error=error_msg[:100])
                    return None

                return data

            except httpx.TimeoutException:
                wait = (attempt + 1) * 5
                log("debug", f"Twelve Data timeout, retrying in {wait}s",
                    symbol=symbol, attempt=attempt + 1)
                await asyncio.sleep(wait)
                last_error = "Timeout"
                continue

            except Exception as e:
                last_error = str(e)
                log("warn", "Twelve Data request error",
                    symbol=symbol, error=last_error[:100])
                return None

        log("warn", "Twelve Data failed after retries",
            symbol=symbol, error=str(last_error)[:100])
        return None

    async def get_stock_history(
        self,
        symbol: str,
        period: str = "1mo",
        interval: str = "1d"
    ) -> Optional[Dict[str, Any]]:
        """
        Get historical stock prices.

        Args:
            symbol: Stock symbol
            period: Time period (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, ytd, max)
            interval: Data interval (1min, 5min, 15min, 1h, 1day, 1week, 1month)

        Returns:
            Dict with OHLCV data in yfinance-compatible format or None if unavailable
        """
        log("debug", "Fetching stock history from Twelve Data", symbol=symbol, period=period)

        # Convert period to start_date
        period_days = {
            "1d": 1, "5d": 5, "1mo": 30, "3mo": 90,
            "6mo": 180, "1y": 365, "2y": 730, "5y": 1825,
            "ytd": (date.today() - date(date.today().year, 1, 1)).days,
            "max": 5000
        }
        days = period_days.get(period, 30)
        start_date = (date.today() - timedelta(days=days)).isoformat()

        # Convert interval to Twelve Data format
        interval_map = {"1d": "1day", "1wk": "1week", "1mo": "1month"}
        td_interval = interval_map.get(interval, interval)

        params = {
            "symbol": symbol,
            "interval": td_interval,
            "start_date": start_date,
            "outputsize": 5000,
        }

        data = await self._request("time_series", params, symbol=symbol)
        if not data or "values" not in data:
            return None

        # Convert to yfinance-compatible format
        values = data["values"]
        result = {
            "Open": {},
            "High": {},
            "Low": {},
            "Close": {},
            "Volume": {},
        }

        for v in values:
            dt = v["datetime"]
            result["Open"][dt] = float(v["open"])
            result["High"][dt] = float(v["high"])
            result["Low"][dt] = float(v["low"])
            result["Close"][dt] = float(v["close"])
            result["Volume"][dt] = int(v["volume"])

        return result

    async def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Get current stock price.

        Args:
            symbol: Stock symbol

        Returns:
            Current price or None if unavailable
        """
        log("debug", "Fetching current price from Twelve Data", symbol=symbol)

        params = {"symbol": symbol}
        data = await self._request("price", params, symbol=symbol)

        if not data or "price" not in data:
            return None

        try:
            return float(data["price"])
        except (ValueError, TypeError):
            return None

    async def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get full quote data.

        Args:
            symbol: Stock symbol

        Returns:
            Dict with quote data (price, volume, etc.) or None if unavailable
        """
        log("debug", "Fetching quote from Twelve Data", symbol=symbol)

        params = {"symbol": symbol}
        data = await self._request("quote", params, symbol=symbol)

        if not data or "symbol" not in data:
            return None

        return {
            "symbol": symbol,
            "price": float(data.get("close", 0)) if data.get("close") else None,
            "open": float(data.get("open", 0)) if data.get("open") else None,
            "high": float(data.get("high", 0)) if data.get("high") else None,
            "low": float(data.get("low", 0)) if data.get("low") else None,
            "volume": int(data.get("volume", 0)) if data.get("volume") else None,
            "previous_close": float(data.get("previous_close", 0)) if data.get("previous_close") else None,
            "change": float(data.get("change", 0)) if data.get("change") else None,
            "percent_change": float(data.get("percent_change", 0)) if data.get("percent_change") else None,
        }
