"""
Async Tradier API client for high-performance parallel scanning.

Uses aiohttp for non-blocking HTTP requests and asyncio.Semaphore
for rate limiting without blocking other coroutines.

Performance Benefits:
- Parallel API calls across multiple tickers
- Non-blocking I/O - no thread pool overhead
- Connection pooling via aiohttp TCPConnector
- Graceful rate limiting with semaphores

Usage:
    async with AsyncTradierAPI(api_key) as api:
        price = await api.get_stock_price('AAPL')
        chain = await api.get_option_chain('AAPL', expiration)
"""

import aiohttp
import asyncio
import logging
from datetime import date
from typing import Dict, List, Optional

from src.domain.types import (
    Money,
    Strike,
    OptionChain,
    OptionQuote,
    Percentage,
    MAX_API_RESPONSE_SIZE,
)
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode

logger = logging.getLogger(__name__)


class AsyncRetryError(Exception):
    """Raised when all retries are exhausted."""
    pass


class AsyncTradierAPI:
    """
    Async Tradier brokerage API client.

    Enables non-blocking parallel requests for much faster scanning
    compared to the synchronous version.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.tradier.com/v1",
        max_concurrent: int = 10,
        max_retries: int = 3,
        base_delay: float = 1.0,
        timeout: float = 10.0,
    ):
        """
        Initialize async Tradier API client.

        Args:
            api_key: Tradier API key
            base_url: API base URL
            max_concurrent: Maximum concurrent requests (respects rate limits)
            max_retries: Maximum retry attempts on failure
            base_delay: Base delay for exponential backoff
            timeout: Request timeout in seconds
        """
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {
            'Authorization': f'Bearer {api_key}',
            'Accept': 'application/json',
        }
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        self.base_delay = base_delay

        # Semaphore for rate limiting - limits concurrent requests
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._session: Optional[aiohttp.ClientSession] = None

        # Statistics
        self._request_count = 0
        self._error_count = 0

    async def __aenter__(self) -> 'AsyncTradierAPI':
        """Async context manager entry - creates session with connection pool."""
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
        """Async context manager exit - closes session."""
        if self._session:
            await self._session.close()
            self._session = None

    def __repr__(self):
        """Mask API key in repr to prevent leaking in logs."""
        return f"AsyncTradierAPI(base_url={self.base_url}, key=***)"

    async def _request_with_retry(
        self,
        url: str,
        params: Dict,
        operation: str = "request"
    ) -> Dict:
        """
        Make request with retry logic and rate limiting.

        Uses semaphore to limit concurrent requests and exponential
        backoff on failures.
        """
        if not self._session:
            raise RuntimeError("API must be used as async context manager")

        last_exception = None
        for attempt in range(self.max_retries + 1):
            try:
                # Acquire semaphore slot (rate limiting)
                async with self.semaphore:
                    self._request_count += 1
                    async with self._session.get(url, params=params) as response:
                        # Check response size before reading
                        content_length = response.headers.get('Content-Length')
                        if content_length and int(content_length) > MAX_API_RESPONSE_SIZE:
                            raise ValueError(f"Response too large: {content_length} bytes")

                        response.raise_for_status()
                        return await response.json()

            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                last_exception = e
                self._error_count += 1

                if attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)
                    logger.warning(
                        f"{operation} failed (attempt {attempt + 1}/{self.max_retries + 1}): {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"{operation} failed after {self.max_retries + 1} attempts: {e}")

        raise AsyncRetryError(f"All retries exhausted for {operation}: {last_exception}")

    async def get_stock_price(self, ticker: str) -> Result[Money, AppError]:
        """
        Get current stock price (async).

        Args:
            ticker: Stock symbol

        Returns:
            Result with Money or AppError
        """
        try:
            data = await self._request_with_retry(
                f"{self.base_url}/markets/quotes",
                {'symbols': ticker},
                f"get_stock_price({ticker})"
            )

            quotes_data = data.get('quotes') or {}
            quotes = quotes_data.get('quote', [])

            if not isinstance(quotes, list):
                quotes = [quotes]

            if not quotes or not quotes[0].get('last'):
                return Err(AppError(ErrorCode.NODATA, f"No price data for {ticker}"))

            last = float(quotes[0]['last'])
            logger.debug(f"Fetched price for {ticker}: ${last:.2f}")
            return Ok(Money(last))

        except AsyncRetryError as e:
            return Err(AppError(ErrorCode.TIMEOUT, str(e)))
        except Exception as e:
            logger.error(f"Unexpected error fetching price for {ticker}: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))

    async def get_stock_prices_batch(
        self, tickers: List[str]
    ) -> Result[Dict[str, Money], AppError]:
        """
        Get stock prices for multiple tickers in a single API call (async).

        Tradier supports up to 100 symbols per request.

        Args:
            tickers: List of stock symbols (max 100)

        Returns:
            Result with dict mapping ticker -> Money price
        """
        if not tickers:
            return Ok({})

        try:
            if len(tickers) > 100:
                logger.warning(
                    f"Batch price request for {len(tickers)} tickers "
                    f"exceeds limit of 100. Truncating to first 100."
                )
            symbols_str = ','.join(tickers[:100])

            data = await self._request_with_retry(
                f"{self.base_url}/markets/quotes",
                {'symbols': symbols_str},
                f"get_stock_prices_batch({len(tickers)} tickers)"
            )

            quotes_data = data.get('quotes') or {}
            quotes = quotes_data.get('quote', [])

            if not isinstance(quotes, list):
                quotes = [quotes]

            prices: Dict[str, Money] = {}
            for quote in quotes:
                if quote and quote.get('symbol') and quote.get('last'):
                    ticker = quote['symbol'].upper()
                    prices[ticker] = Money(float(quote['last']))

            logger.debug(f"Batch fetched {len(prices)}/{len(tickers)} stock prices")
            return Ok(prices)

        except AsyncRetryError as e:
            return Err(AppError(ErrorCode.TIMEOUT, str(e)))
        except Exception as e:
            logger.error(f"Unexpected error fetching batch prices: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))

    async def get_expirations(self, ticker: str) -> Result[List[date], AppError]:
        """
        Get all available option expirations for ticker (async).

        Args:
            ticker: Stock symbol

        Returns:
            Result with list of expiration dates
        """
        try:
            data = await self._request_with_retry(
                f"{self.base_url}/markets/options/expirations",
                {'symbol': ticker},
                f"get_expirations({ticker})"
            )

            expirations = data.get('expirations', {}).get('date', [])

            if not isinstance(expirations, list):
                expirations = [expirations]

            if not expirations:
                return Err(AppError(ErrorCode.NODATA, f"No expirations for {ticker}"))

            dates = [date.fromisoformat(exp) for exp in expirations]
            logger.debug(f"Found {len(dates)} expirations for {ticker}")
            return Ok(dates)

        except AsyncRetryError as e:
            return Err(AppError(ErrorCode.TIMEOUT, str(e)))
        except Exception as e:
            logger.error(f"Unexpected error fetching expirations for {ticker}: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))

    async def find_nearest_expiration(
        self, ticker: str, target_date: date
    ) -> Result[date, AppError]:
        """
        Find the nearest available expiration >= target date (async).

        Args:
            ticker: Stock symbol
            target_date: Desired expiration date

        Returns:
            Result with nearest available expiration date or error
        """
        expirations_result = await self.get_expirations(ticker)
        if expirations_result.is_err:
            return Err(expirations_result.error)

        expirations = sorted(expirations_result.value)

        # Find nearest expiration >= target_date
        nearest = None
        for exp in expirations:
            if exp >= target_date:
                nearest = exp
                break

        if nearest is None:
            if expirations:
                nearest = expirations[-1]
                logger.warning(
                    f"{ticker}: No expiration >= {target_date}, using {nearest}"
                )
            else:
                return Err(AppError(ErrorCode.NODATA, f"No expirations for {ticker}"))

        if nearest != target_date:
            logger.debug(f"{ticker}: Adjusted expiration {target_date} → {nearest}")

        return Ok(nearest)

    async def get_option_chain(
        self, ticker: str, expiration: date
    ) -> Result[OptionChain, AppError]:
        """
        Get option chain for ticker and expiration (async).

        Parallelizes stock price and chain fetch for maximum performance.

        Args:
            ticker: Stock symbol
            expiration: Option expiration date

        Returns:
            Result with OptionChain or AppError
        """
        try:
            # Fetch stock price and chain in parallel
            price_coro = self.get_stock_price(ticker)
            chain_coro = self._request_with_retry(
                f"{self.base_url}/markets/options/chains",
                {
                    'symbol': ticker,
                    'expiration': expiration.isoformat(),
                    'greeks': 'true',
                },
                f"get_option_chain({ticker}, {expiration})"
            )

            price_result, chain_data = await asyncio.gather(price_coro, chain_coro)

            if price_result.is_err:
                return Err(price_result.error)

            stock_price = price_result.value

            # Parse options
            options_data = chain_data.get('options') or {}
            options = options_data.get('option', [])

            if not isinstance(options, list):
                options = [options]

            if not options:
                return Err(
                    AppError(ErrorCode.NODATA, f"No options for {ticker} exp {expiration}")
                )

            # Parse into calls and puts
            calls: Dict[Strike, OptionQuote] = {}
            puts: Dict[Strike, OptionQuote] = {}

            for opt in options:
                try:
                    if not opt.get('bid') or not opt.get('ask'):
                        continue

                    strike = Strike(float(opt['strike']))

                    # Parse Greeks
                    greeks = opt.get('greeks', {})
                    iv = None
                    delta = None
                    gamma = None
                    theta = None
                    vega = None

                    if greeks:
                        if greeks.get('mid_iv'):
                            iv = Percentage(float(greeks['mid_iv']) * 100)
                        if greeks.get('delta'):
                            delta = float(greeks['delta'])
                        if greeks.get('gamma'):
                            gamma = float(greeks['gamma'])
                        if greeks.get('theta'):
                            theta = float(greeks['theta'])
                        if greeks.get('vega'):
                            vega = float(greeks['vega'])

                    quote = OptionQuote(
                        bid=Money(float(opt['bid'])),
                        ask=Money(float(opt['ask'])),
                        implied_volatility=iv,
                        open_interest=int(opt.get('open_interest', 0)),
                        volume=int(opt.get('volume', 0)),
                        delta=delta,
                        gamma=gamma,
                        theta=theta,
                        vega=vega,
                    )

                    if opt['option_type'] == 'call':
                        calls[strike] = quote
                    else:
                        puts[strike] = quote

                except (KeyError, ValueError) as e:
                    logger.debug(f"Skipping malformed option: {e}")
                    continue

            if not calls or not puts:
                return Err(
                    AppError(ErrorCode.NODATA, "Incomplete chain (missing calls or puts)")
                )

            chain = OptionChain(
                ticker=ticker,
                expiration=expiration,
                stock_price=stock_price,
                calls=calls,
                puts=puts,
            )

            logger.debug(f"Fetched chain for {ticker}: {len(calls)} calls, {len(puts)} puts")
            return Ok(chain)

        except AsyncRetryError as e:
            return Err(AppError(ErrorCode.TIMEOUT, str(e)))
        except Exception as e:
            logger.error(f"Unexpected error fetching chain for {ticker}: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))

    def get_stats(self) -> Dict[str, int]:
        """Get API call statistics."""
        return {
            'request_count': self._request_count,
            'error_count': self._error_count,
        }
