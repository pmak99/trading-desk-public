"""
Yahoo Finance client for stock data.

Free fallback for prices and historical data.
Uses yfinance library.
"""

import asyncio
import atexit
from typing import Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf

from src.core.logging import log


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

    async def _run_sync(self, func, *args, **kwargs):
        """Run sync yfinance function in thread pool."""
        if not self._executor:
            raise RuntimeError("YahooFinanceClient has been closed")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            self._executor,
            lambda: func(*args, **kwargs)
        )

    async def get_stock_history(
        self,
        symbol: str,
        period: str = "1mo",
        interval: str = "1d"
    ) -> Dict[str, Any]:
        """
        Get historical stock prices.

        Args:
            symbol: Stock symbol
            period: Time period (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, ytd, max)
            interval: Data interval (1m, 5m, 15m, 1h, 1d, 1wk, 1mo)

        Returns:
            Dict with OHLCV data
        """
        log("debug", "Fetching stock history", symbol=symbol, period=period)

        def _fetch():
            ticker = yf.Ticker(symbol)
            df = ticker.history(period=period, interval=interval)
            return df.to_dict()

        return await self._run_sync(_fetch)

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
            # Try regularMarketPrice first, then previousClose
            return info.get("regularMarketPrice") or info.get("previousClose")

        return await self._run_sync(_fetch)

    async def get_earnings_info(self, symbol: str) -> Dict[str, Any]:
        """
        Get earnings calendar info for symbol.

        Args:
            symbol: Stock symbol

        Returns:
            Dict with calendar and earnings data
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
                result["sector"] = info.get("sector", "")
                result["industry"] = info.get("industry", "")
                result["market_cap"] = info.get("marketCap", 0)
            except Exception:
                pass

            return result

        return await self._run_sync(_fetch)

    async def get_quote(self, symbol: str) -> Dict[str, Any]:
        """
        Get full quote data.

        Args:
            symbol: Stock symbol

        Returns:
            Dict with quote data (price, volume, bid/ask, etc.)
        """
        log("debug", "Fetching quote", symbol=symbol)

        def _fetch():
            ticker = yf.Ticker(symbol)
            info = ticker.info
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

        return await self._run_sync(_fetch)
