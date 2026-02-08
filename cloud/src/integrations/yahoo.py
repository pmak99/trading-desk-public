"""
Yahoo Finance client for stock data.

Free fallback for prices and historical data.
Uses yfinance library with retry handling for rate limits and transient errors.
"""

import asyncio
import atexit
import json
import time
from typing import Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf

from src.core.logging import log

# Rate limit protection
_last_request_time = 0
_rate_limit_lock = asyncio.Lock()
MIN_REQUEST_INTERVAL = 1.0  # 1 second between requests

# Errors that indicate transient API issues (retry-able)
TRANSIENT_ERROR_PATTERNS = [
    "Expecting value",  # JSON parse error from empty response
    "JSONDecodeError",
    "No data found",
    "404",
    "ConnectionError",
    "Timeout",
    "SSLError",
]


def _is_transient_error(error: Exception) -> bool:
    """Check if an error is transient and worth retrying."""
    error_str = str(error)
    error_type = type(error).__name__

    # Check error message patterns
    for pattern in TRANSIENT_ERROR_PATTERNS:
        if pattern in error_str or pattern in error_type:
            return True

    # Also check for json.JSONDecodeError specifically
    if isinstance(error, json.JSONDecodeError):
        return True

    return False


class YahooFinanceClient:
    """Async wrapper around yfinance (sync library)."""

    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=4)
        # Register cleanup on process exit
        atexit.register(self.close)

    def close(self):
        """Shutdown thread pool executor."""
        if self._executor:
            self._executor.shutdown(wait=False)
            self._executor = None

    async def _run_sync(self, func, symbol: str = "unknown", *args, **kwargs):
        """Run sync yfinance function in thread pool with rate limiting and retry."""
        if not self._executor:
            raise RuntimeError("YahooFinanceClient has been closed")

        global _last_request_time

        # Rate limit protection (lock prevents concurrent requests racing)
        async with _rate_limit_lock:
            now = time.time()
            wait_time = MIN_REQUEST_INTERVAL - (now - _last_request_time)
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            _last_request_time = time.time()

        loop = asyncio.get_event_loop()

        # Retry logic for rate limits and transient errors
        last_error = None
        for attempt in range(3):
            try:
                return await loop.run_in_executor(
                    self._executor,
                    lambda: func(*args, **kwargs)
                )
            except Exception as e:
                last_error = e
                error_str = str(e)

                # Rate limit - longer backoff
                if "429" in error_str or "Too Many Requests" in error_str:
                    wait = (attempt + 1) * 5  # 5, 10, 15 seconds
                    log("warn", f"Yahoo rate limited, waiting {wait}s",
                        symbol=symbol, attempt=attempt+1)
                    await asyncio.sleep(wait)
                    _last_request_time = time.time()
                    continue

                # Transient errors - shorter backoff
                if _is_transient_error(e):
                    wait = (attempt + 1) * 2  # 2, 4, 6 seconds
                    log("debug", f"Yahoo transient error, retrying in {wait}s",
                        symbol=symbol, error=error_str[:100], attempt=attempt+1)
                    await asyncio.sleep(wait)
                    _last_request_time = time.time()
                    continue

                # Non-retryable error - log and return None
                log("debug", "Yahoo API error (non-retryable)",
                    symbol=symbol, error=error_str[:100])
                return None

        # All retries exhausted
        log("warn", "Yahoo API failed after retries",
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
            interval: Data interval (1m, 5m, 15m, 1h, 1d, 1wk, 1mo)

        Returns:
            Dict with OHLCV data or None if unavailable
        """
        log("debug", "Fetching stock history", symbol=symbol, period=period)

        def _fetch():
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval)
            if df.empty:
                return None
            return df.to_dict()

        return await self._run_sync(_fetch, symbol=symbol)

    async def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Get current stock price.

        Args:
            symbol: Stock symbol

        Returns:
            Current price or None if unavailable
        """
        log("debug", "Fetching current price", symbol=symbol)

        def _fetch():
            ticker = yf.Ticker(symbol)
            info = ticker.info
            if not info:
                return None
            # Try regularMarketPrice first, then previousClose
            return info.get("regularMarketPrice") or info.get("previousClose")

        return await self._run_sync(_fetch, symbol=symbol)

    async def get_earnings_info(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get earnings calendar info for symbol.

        Args:
            symbol: Stock symbol

        Returns:
            Dict with calendar and earnings data or None if unavailable
        """
        log("debug", "Fetching earnings info", symbol=symbol)

        def _fetch():
            ticker = yf.Ticker(symbol)
            result = {"symbol": symbol}

            # Get calendar (next earnings date)
            try:
                calendar = ticker.calendar
                if calendar is not None and hasattr(calendar, "to_dict"):
                    result["calendar"] = calendar.to_dict()
            except Exception:
                pass

            # Get basic info
            try:
                info = ticker.info
                if info:
                    result["sector"] = info.get("sector", "")
                    result["industry"] = info.get("industry", "")
                    result["market_cap"] = info.get("marketCap", 0)
            except Exception:
                pass

            return result

        return await self._run_sync(_fetch, symbol=symbol)

    async def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Get full quote data.

        Args:
            symbol: Stock symbol

        Returns:
            Dict with quote data (price, volume, bid/ask, etc.) or None if unavailable
        """
        log("debug", "Fetching quote", symbol=symbol)

        def _fetch():
            ticker = yf.Ticker(symbol)
            info = ticker.info
            if not info:
                return None
            return {
                "symbol": symbol,
                "price": info.get("regularMarketPrice") or info.get("previousClose"),
                "open": info.get("open"),
                "high": info.get("dayHigh"),
                "low": info.get("dayLow"),
                "volume": info.get("volume"),
                "previous_close": info.get("previousClose"),
                "market_cap": info.get("marketCap"),
                "pe_ratio": info.get("trailingPE"),
            }

        return await self._run_sync(_fetch, symbol=symbol)
