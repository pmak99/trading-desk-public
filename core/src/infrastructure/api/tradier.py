"""
Tradier API client for real-time options data.

Implements OptionsDataProvider protocol with retry logic and circuit breaker
(Phase 1 enhancements).
"""

import requests
import logging
from datetime import date
from typing import Dict, Optional
from src.domain.types import Money, Strike, OptionChain, OptionQuote, Percentage
from src.domain.errors import Result, AppError, Ok, Err, ErrorCode
from src.domain.enums import OptionType

logger = logging.getLogger(__name__)

# Maximum response size to prevent OOM attacks (10MB)
MAX_RESPONSE_SIZE = 10 * 1024 * 1024  # 10MB


class TradierAPI:
    """
    Tradier brokerage API client.

    Provides real-time options chains and stock quotes.
    Sandbox: https://sandbox.tradier.com/v1
    Production: https://api.tradier.com/v1
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.tradier.com/v1",
        rate_limiter: Optional['TokenBucketRateLimiter'] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {
            'Authorization': f'Bearer {api_key}',
            'Accept': 'application/json',
        }
        self.timeout = 10
        self.rate_limiter = rate_limiter

    def __repr__(self):
        """Mask API key in repr to prevent leaking in logs."""
        return f"TradierAPI(base_url={self.base_url}, key=***)"

    def get_stock_price(self, ticker: str) -> Result[Money, AppError]:
        """
        Get current stock price.

        Args:
            ticker: Stock symbol

        Returns:
            Result with Money or AppError
        """
        # Rate limit check
        if self.rate_limiter and not self.rate_limiter.acquire():
            return Err(
                AppError(
                    ErrorCode.RATELIMIT,
                    "Tradier rate limit exceeded",
                )
            )

        try:
            response = requests.get(
                f"{self.base_url}/markets/quotes",
                params={'symbols': ticker},
                headers=self.headers,
                timeout=self.timeout,
            )
            response.raise_for_status()

            # Check response size to prevent OOM
            content_length = response.headers.get('Content-Length')
            if content_length and int(content_length) > MAX_RESPONSE_SIZE:
                return Err(
                    AppError(
                        ErrorCode.EXTERNAL,
                        f"Response too large: {content_length} bytes",
                    )
                )

            if len(response.content) > MAX_RESPONSE_SIZE:
                return Err(
                    AppError(
                        ErrorCode.EXTERNAL,
                        f"Response too large: {len(response.content)} bytes",
                    )
                )

            data = response.json()
            quotes = data.get('quotes', {}).get('quote', [])

            # Handle single quote or list
            if not isinstance(quotes, list):
                quotes = [quotes]

            if not quotes or not quotes[0].get('last'):
                return Err(
                    AppError(ErrorCode.NODATA, f"No price data for {ticker}")
                )

            last = float(quotes[0]['last'])
            logger.debug(f"Fetched price for {ticker}: ${last:.2f}")
            return Ok(Money(last))

        except requests.exceptions.Timeout:
            logger.warning(f"Timeout fetching price for {ticker}")
            return Err(AppError(ErrorCode.TIMEOUT, f"Timeout: {ticker}"))

        except requests.exceptions.RequestException as e:
            logger.error(f"Request error fetching price: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))

        except Exception as e:
            logger.error(f"Unexpected error fetching price: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))

    def get_option_chain(
        self, ticker: str, expiration: date
    ) -> Result[OptionChain, AppError]:
        """
        Get option chain for ticker and expiration.

        Args:
            ticker: Stock symbol
            expiration: Option expiration date

        Returns:
            Result with OptionChain or AppError
        """
        # Rate limit check (separate from get_stock_price call)
        if self.rate_limiter and not self.rate_limiter.acquire():
            return Err(
                AppError(
                    ErrorCode.RATELIMIT,
                    "Tradier rate limit exceeded",
                )
            )

        try:
            # First, get current stock price
            price_result = self.get_stock_price(ticker)
            if price_result.is_err:
                return Err(price_result.error)

            stock_price = price_result.value

            # Get option chain
            response = requests.get(
                f"{self.base_url}/markets/options/chains",
                params={
                    'symbol': ticker,
                    'expiration': expiration.isoformat(),
                    'greeks': 'true',
                },
                headers=self.headers,
                timeout=self.timeout,
            )
            response.raise_for_status()

            # Check response size to prevent OOM
            content_length = response.headers.get('Content-Length')
            if content_length and int(content_length) > MAX_RESPONSE_SIZE:
                return Err(
                    AppError(
                        ErrorCode.EXTERNAL,
                        f"Response too large: {content_length} bytes",
                    )
                )

            if len(response.content) > MAX_RESPONSE_SIZE:
                return Err(
                    AppError(
                        ErrorCode.EXTERNAL,
                        f"Response too large: {len(response.content)} bytes",
                    )
                )

            data = response.json()
            options = data.get('options', {}).get('option', [])

            # Handle single option or list
            if not isinstance(options, list):
                options = [options]

            if not options:
                return Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"No options for {ticker} exp {expiration}",
                    )
                )

            # Parse options into calls and puts
            calls: Dict[Strike, OptionQuote] = {}
            puts: Dict[Strike, OptionQuote] = {}

            for opt in options:
                try:
                    # Skip if no bid/ask
                    if not opt.get('bid') or not opt.get('ask'):
                        continue

                    strike = Strike(float(opt['strike']))

                    # Parse IV (may be None)
                    iv = None
                    if opt.get('greeks') and opt['greeks'].get('mid_iv'):
                        iv = Percentage(float(opt['greeks']['mid_iv']) * 100)

                    quote = OptionQuote(
                        bid=Money(float(opt['bid'])),
                        ask=Money(float(opt['ask'])),
                        implied_volatility=iv,
                        open_interest=int(opt.get('open_interest', 0)),
                        volume=int(opt.get('volume', 0)),
                    )

                    # Categorize by option type
                    if opt['option_type'] == 'call':
                        calls[strike] = quote
                    else:
                        puts[strike] = quote

                except (KeyError, ValueError) as e:
                    logger.debug(f"Skipping malformed option: {e}")
                    continue

            if not calls or not puts:
                return Err(
                    AppError(
                        ErrorCode.NODATA,
                        f"Incomplete chain (missing calls or puts)",
                    )
                )

            chain = OptionChain(
                ticker=ticker,
                expiration=expiration,
                stock_price=stock_price,
                calls=calls,
                puts=puts,
            )

            logger.info(
                f"Fetched chain for {ticker}: {len(calls)} calls, {len(puts)} puts"
            )
            return Ok(chain)

        except requests.exceptions.Timeout:
            logger.warning(f"Timeout fetching chain for {ticker}")
            return Err(AppError(ErrorCode.TIMEOUT, f"Timeout: {ticker}"))

        except requests.exceptions.RequestException as e:
            logger.error(f"Request error fetching chain: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))

        except Exception as e:
            logger.error(f"Unexpected error fetching chain: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))

    def get_expirations(self, ticker: str) -> Result[list[date], AppError]:
        """
        Get all available option expirations for ticker.

        Args:
            ticker: Stock symbol

        Returns:
            Result with list of expiration dates
        """
        try:
            response = requests.get(
                f"{self.base_url}/markets/options/expirations",
                params={'symbol': ticker},
                headers=self.headers,
                timeout=self.timeout,
            )
            response.raise_for_status()

            # Check response size to prevent OOM
            content_length = response.headers.get('Content-Length')
            if content_length and int(content_length) > MAX_RESPONSE_SIZE:
                return Err(
                    AppError(
                        ErrorCode.EXTERNAL,
                        f"Response too large: {content_length} bytes",
                    )
                )

            if len(response.content) > MAX_RESPONSE_SIZE:
                return Err(
                    AppError(
                        ErrorCode.EXTERNAL,
                        f"Response too large: {len(response.content)} bytes",
                    )
                )

            data = response.json()
            expirations = data.get('expirations', {}).get('date', [])

            if not isinstance(expirations, list):
                expirations = [expirations]

            if not expirations:
                return Err(
                    AppError(
                        ErrorCode.NODATA, f"No expirations for {ticker}"
                    )
                )

            dates = [date.fromisoformat(exp) for exp in expirations]
            logger.debug(f"Found {len(dates)} expirations for {ticker}")
            return Ok(dates)

        except requests.exceptions.Timeout:
            logger.warning(f"Timeout fetching expirations for {ticker}")
            return Err(AppError(ErrorCode.TIMEOUT, f"Timeout: {ticker}"))

        except requests.exceptions.RequestException as e:
            logger.error(f"Request error fetching expirations: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))

        except Exception as e:
            logger.error(f"Unexpected error fetching expirations: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))
