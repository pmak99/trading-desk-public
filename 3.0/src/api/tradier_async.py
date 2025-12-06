"""
Async Tradier API client for 3.0 ML System.
Enables parallel scanning of multiple tickers.
"""

import os
import asyncio
import aiohttp
import logging
from datetime import date
from typing import Dict, List, Optional, TypeVar

from src.api.tradier import OptionQuote, OptionChain, ImpliedMove

logger = logging.getLogger(__name__)

__all__ = [
    'AsyncRetryError',
    'AsyncTradierAPI',
]

T = TypeVar('T')


class AsyncRetryError(Exception):
    """Raised when all retries are exhausted."""
    pass


class AsyncTradierAPI:
    """
    Async Tradier brokerage API client.
    Enables parallel requests for faster scanning.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_concurrent: int = 10,  # Optimized from 5 based on benchmarks
    ):
        self.api_key = api_key or os.getenv('TRADIER_API_KEY')
        if not self.api_key:
            raise ValueError(
                "TRADIER_API_KEY not set. "
                "Set it via environment variable or pass api_key parameter."
            )

        self.base_url = "https://api.tradier.com/v1"
        self.headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Accept': 'application/json',
        }
        self.timeout = aiohttp.ClientTimeout(total=10)
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        """Async context manager entry."""
        # Optimized connector with connection pooling
        connector = aiohttp.TCPConnector(
            limit=self.semaphore._value,  # Match semaphore limit
            limit_per_host=self.semaphore._value,
            ttl_dns_cache=300,  # Cache DNS for 5 minutes
            enable_cleanup_closed=True,
        )
        self._session = aiohttp.ClientSession(
            headers=self.headers,
            timeout=self.timeout,
            connector=connector,
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        if self._session:
            await self._session.close()
            self._session = None

    async def _request_with_retry(self, url: str, params: Dict) -> Dict:
        """Make request with retry logic."""
        if not self._session:
            raise RuntimeError("API must be used as async context manager")

        last_exception = None
        for attempt in range(self.max_retries + 1):
            try:
                async with self.semaphore:
                    async with self._session.get(url, params=params) as response:
                        response.raise_for_status()
                        return await response.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_exception = e
                if attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    logger.warning(
                        f"Request failed (attempt {attempt + 1}/{self.max_retries + 1}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"Request failed after {self.max_retries + 1} attempts: {e}")

        raise AsyncRetryError(f"All retries exhausted: {last_exception}")

    async def get_stock_price(self, ticker: str) -> float:
        """Get current stock price for a single ticker."""
        prices = await self.get_stock_prices([ticker])
        if ticker not in prices:
            raise ValueError(f"No price data for {ticker}")
        return prices[ticker]

    async def get_stock_prices(self, tickers: List[str]) -> Dict[str, float]:
        """
        Get current stock prices for multiple tickers in a single API call.

        More efficient than calling get_stock_price for each ticker.
        Tradier API supports up to 100 symbols per request.
        """
        if not tickers:
            return {}

        # Batch into chunks of 100 (Tradier limit)
        results = {}
        for i in range(0, len(tickers), 100):
            batch = tickers[i:i+100]
            data = await self._request_with_retry(
                f"{self.base_url}/markets/quotes",
                {'symbols': ','.join(batch)},
            )

            quotes_data = data.get('quotes') or {}
            quotes = quotes_data.get('quote', [])
            if not isinstance(quotes, list):
                quotes = [quotes]

            for quote in quotes:
                if quote and quote.get('last') and quote.get('symbol'):
                    results[quote['symbol']] = float(quote['last'])

        return results

    async def get_expirations(self, ticker: str) -> List[date]:
        """Get available option expirations."""
        data = await self._request_with_retry(
            f"{self.base_url}/markets/options/expirations",
            {'symbol': ticker},
        )

        exp_data = data.get('expirations') or {}
        dates = exp_data.get('date', [])
        if not isinstance(dates, list):
            dates = [dates]

        return [date.fromisoformat(d) for d in dates if d]

    async def get_option_chain(self, ticker: str, expiration: date, greeks: bool = True) -> OptionChain:
        """Get option chain for ticker and expiration."""
        # Get stock price and options concurrently
        stock_price_task = asyncio.create_task(self.get_stock_price(ticker))
        options_task = asyncio.create_task(
            self._request_with_retry(
                f"{self.base_url}/markets/options/chains",
                {
                    'symbol': ticker,
                    'expiration': expiration.isoformat(),
                    'greeks': 'true' if greeks else 'false',
                },
            )
        )

        stock_price, data = await asyncio.gather(stock_price_task, options_task)

        options_data = data.get('options') or {}
        options = options_data.get('option', [])
        if not isinstance(options, list):
            options = [options]

        calls = []
        puts = []

        for opt in options:
            if not opt:
                continue

            quote = OptionQuote(
                strike=float(opt.get('strike', 0)),
                option_type=opt.get('option_type', ''),
                bid=float(opt.get('bid', 0) or 0),
                ask=float(opt.get('ask', 0) or 0),
                last=float(opt.get('last', 0) or 0),
                volume=int(opt.get('volume', 0) or 0),
                open_interest=int(opt.get('open_interest', 0) or 0),
                iv=float(opt['greeks']['mid_iv']) if opt.get('greeks', {}).get('mid_iv') else None,
                delta=float(opt['greeks']['delta']) if opt.get('greeks', {}).get('delta') else None,
                gamma=float(opt['greeks']['gamma']) if opt.get('greeks', {}).get('gamma') else None,
                theta=float(opt['greeks']['theta']) if opt.get('greeks', {}).get('theta') else None,
                vega=float(opt['greeks']['vega']) if opt.get('greeks', {}).get('vega') else None,
            )

            if quote.option_type == 'call':
                calls.append(quote)
            elif quote.option_type == 'put':
                puts.append(quote)

        return OptionChain(
            ticker=ticker,
            expiration=expiration,
            stock_price=stock_price,
            calls=sorted(calls, key=lambda x: x.strike),
            puts=sorted(puts, key=lambda x: x.strike),
        )

    async def calculate_implied_move(self, ticker: str, expiration: date) -> ImpliedMove:
        """Calculate implied move from ATM straddle."""
        chain = await self.get_option_chain(ticker, expiration)

        # Find ATM strike (closest to stock price)
        all_strikes = set(c.strike for c in chain.calls) | set(p.strike for p in chain.puts)
        atm_strike = min(all_strikes, key=lambda s: abs(s - chain.stock_price))

        # Get ATM call and put
        atm_call = next((c for c in chain.calls if c.strike == atm_strike), None)
        atm_put = next((p for p in chain.puts if p.strike == atm_strike), None)

        if not atm_call or not atm_put:
            raise ValueError(f"No ATM options found for {ticker} at strike {atm_strike}")

        # Calculate mid prices
        call_mid = (atm_call.bid + atm_call.ask) / 2
        put_mid = (atm_put.bid + atm_put.ask) / 2
        straddle_cost = call_mid + put_mid

        # Implied move percentage
        implied_move_pct = (straddle_cost / chain.stock_price) * 100

        return ImpliedMove(
            ticker=ticker,
            expiration=expiration,
            stock_price=chain.stock_price,
            atm_strike=atm_strike,
            call_mid=call_mid,
            put_mid=put_mid,
            straddle_cost=straddle_cost,
            implied_move_pct=implied_move_pct,
            upper_bound=chain.stock_price + straddle_cost,
            lower_bound=chain.stock_price - straddle_cost,
        )
