"""
Tradier API client for real-time options data.

Implements the OptionsDataProvider protocol. Every request goes through one
requests.Session that retries 429 and 5xx responses (see _TradierRetry) -- except
a chain, which is sent once and comes back as a retryable error (CHAINS_PATH);
there is no circuit breaker. Calls wait on the shared limiter first (Container
gives every client the same one).
"""

import logging
import time
from datetime import date
from typing import Callable, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.domain.errors import AppError, Err, ErrorCode, Ok, Result
from src.domain.types import (
    MAX_API_RESPONSE_SIZE,
    Money,
    OptionChain,
    OptionQuote,
    Percentage,
    Strike,
)
from src.utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Longest we will sleep on one 429 before retrying. Tradier's window is a minute.
MAX_RATE_LIMIT_WAIT_SECONDS = 65.0

# Chain requests, whose session adapter retries nothing. A chain is priced on the
# spot sent with it (get_option_chain's stock_price), and a resend inside the
# session carries that spot as old as the retries took: up to 65 s x 3 after a
# 429, and 36 s after three read timeouts (10 s each, plus the 0, 2, 4 s backoff).
# A chain that gets a 429, a server error, no answer in time or no connection
# comes back once as a retryable error (AppError.retryable), after Tradier's
# window or CHAIN_RESEND_WAIT_SECONDS; CachedOptionsDataProvider takes a new slot
# and re-reads the quote before it sends the chain again. The session resent a
# chain on its old spot, and outside the limiter, after a 429 until 2026-09-14
# and after the rest until 2026-09-15.
CHAINS_PATH = "/markets/options/chains"

# Server errors the session retries for every other call, and that come back
# retryable on a chain. 501 and 505 are not transient.
RETRYABLE_SERVER_ERRORS = (500, 502, 503, 504)

# Seconds a chain waits after a server error or failed connection before it is
# handed back, unless the answer carries Retry-After. The session's backoff waited
# 0, 2 and 4 s before its three retries; the resend's slot and quote add their own
# time. A timeout is not waited again: it has already taken 10 s.
CHAIN_RESEND_WAIT_SECONDS = 2.0


def _rate_limit_wait(headers, status: int) -> Optional[float]:
    """Seconds a refused request is asked to wait, capped at MAX_RATE_LIMIT_WAIT_SECONDS.

    Retry-After when present; otherwise, on a 429, Tradier's own X-Ratelimit-Expiry
    (epoch milliseconds when the current 1-minute window resets). None when neither.
    """
    retry_after = headers.get("Retry-After")
    if retry_after is not None:
        return min(Retry().parse_retry_after(retry_after), MAX_RATE_LIMIT_WAIT_SECONDS)
    expiry = headers.get("X-Ratelimit-Expiry")
    if status == 429 and expiry:
        try:
            wait = int(expiry) / 1000.0 - time.time()
        except ValueError:
            return None
        return min(max(wait, 0.0), MAX_RATE_LIMIT_WAIT_SECONDS)
    return None


class _TradierRetry(Retry):
    """urllib3 Retry that also understands Tradier's own rate-limit header.

    Tradier documents X-Ratelimit-Expiry, not Retry-After. Without this a 429 would
    be retried on exponential backoff -- 0s, then 2s -- inside the same exhausted
    window, and fail again.
    """

    def get_retry_after(self, response):
        return _rate_limit_wait(response.headers, response.status)


def _request_error(e: requests.exceptions.RequestException) -> AppError:
    """A final 429 is a rate-limit error, not a generic external failure."""
    response = getattr(e, "response", None)
    if response is not None and response.status_code == 429:
        return AppError(ErrorCode.RATELIMIT, f"Tradier rate limit (429, retries exhausted): {e}")
    return AppError(ErrorCode.EXTERNAL, str(e))


# Max seconds a call waits for a slot in the shared 120-per-60s window before it
# is refused. A refusal is Err(RATELIMIT) and the scanner drops that ticker, so
# the budget must outlast a whole window: a scan that spends its 120 calls in the
# first seconds waits up to ~60 s for the oldest to age out, plus queueing behind
# the other workers. At 30 s (until 2026-09-14) a simulated 60-ticker concurrent
# scan lost 30 tickers.
RATE_LIMIT_WAIT_TIMEOUT_SEC = 120.0

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
        rate_limiter: Optional[RateLimiter] = None,
        max_retries: int = 3,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {
            'Authorization': f'Bearer {api_key}',
            'Accept': 'application/json',
        }
        self.timeout = 10
        self.rate_limiter = rate_limiter
        # ⚠️ Until 2026-09-13 every call was a bare requests.get, so one 429 or
        # 503 dropped the ticker from a scan. GET only, so a retry is always safe.
        # raise_on_status=False hands the last response back, and
        # raise_for_status() below turns a final 429 into ErrorCode.RATELIMIT.
        retry = _TradierRetry(
            total=max_retries,
            backoff_factor=1,
            status_forcelist=[429, *RETRYABLE_SERVER_ERRORS],
            allowed_methods=["GET"],
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        self.session = requests.Session()
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=16)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        # Chains (CHAINS_PATH; the longest mounted prefix wins) are sent once:
        # requests' default Retry(0, read=False), under which a read timeout is
        # raised as ReadTimeout rather than wrapped as a failed connection.
        self.session.mount(f"{base_url}{CHAINS_PATH}", HTTPAdapter(pool_maxsize=16))
        self.rate_limit_wait_timeout = RATE_LIMIT_WAIT_TIMEOUT_SEC
        # Waits out a refused chain's window; tests pass a fake clock's.
        self.sleep: Callable[[float], None] = time.sleep

    def __repr__(self):
        """Mask API key in repr to prevent leaking in logs."""
        return f"TradierAPI(base_url={self.base_url}, key=***)"

    def admit(self) -> bool:
        """
        Wait for one slot in the shared limiter, within the wait budget.

        Every call does this before its request. A caller that must choose what to
        send only once the slot is taken calls it itself and passes admitted=True
        to the one call it then makes: CachedOptionsDataProvider reads a chain's
        quote after the chain's slot comes, so the wait cannot age it
        (protocols.RateLimitedOptionsProvider).
        """
        return self.rate_limiter is None or self.rate_limiter.wait_for_token(
            timeout=self.rate_limit_wait_timeout
        )

    def get_stock_price(self, ticker: str, admitted: bool = False) -> Result[Money, AppError]:
        """
        Get current stock price.

        Args:
            ticker: Stock symbol
            admitted: The caller took this call's slot with admit()

        Returns:
            Result with Money or AppError
        """
        # Rate limit: wait for a token instead of failing immediately —
        # the bucket refills continuously, so a short wait almost always
        # succeeds where a hard fail would silently drop the ticker.
        if not admitted and not self.admit():
            return Err(
                AppError(
                    ErrorCode.RATELIMIT,
                    "Tradier rate limit exceeded",
                )
            )

        try:
            response = self.session.get(
                f"{self.base_url}/markets/quotes",
                params={'symbols': ticker},
                headers=self.headers,
                timeout=self.timeout,
            )
            response.raise_for_status()

            # Check response size to prevent OOM
            content_length = response.headers.get('Content-Length')
            if content_length and int(content_length) > MAX_API_RESPONSE_SIZE:
                return Err(
                    AppError(
                        ErrorCode.EXTERNAL,
                        f"Response too large: {content_length} bytes",
                    )
                )

            if len(response.content) > MAX_API_RESPONSE_SIZE:
                return Err(
                    AppError(
                        ErrorCode.EXTERNAL,
                        f"Response too large: {len(response.content)} bytes",
                    )
                )

            data = response.json()

            # Handle case where 'quotes' key exists but value is None
            quotes_data = data.get('quotes') or {}
            quotes = quotes_data.get('quote', [])

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
            logger.error(f"Request error fetching price for {ticker}: {e}")
            return Err(_request_error(e))

        except Exception as e:
            logger.error(f"Unexpected error fetching price for {ticker}: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))

    def get_stock_prices_batch(
        self, tickers: list[str], admitted: bool = False
    ) -> Result[Dict[str, Money], AppError]:
        """
        Get stock prices for multiple tickers in a single API call.

        Tradier supports up to 100 symbols per request.
        This reduces API calls from N to ceil(N/100) for batch operations.

        Args:
            tickers: List of stock symbols (max 100)
            admitted: The caller took this call's slot with admit()

        Returns:
            Result with dict mapping ticker -> Money price
        """
        if not tickers:
            return Ok({})

        # Rate limit: wait for a token instead of failing immediately
        if not admitted and not self.admit():
            return Err(
                AppError(
                    ErrorCode.RATELIMIT,
                    "Tradier rate limit exceeded",
                )
            )

        try:
            # Tradier accepts comma-separated symbols (max 100)
            if len(tickers) > 100:
                logger.warning(
                    f"Batch price request for {len(tickers)} tickers "
                    f"exceeds limit of 100. Truncating to first 100."
                )
            symbols_str = ','.join(tickers[:100])

            response = self.session.get(
                f"{self.base_url}/markets/quotes",
                params={'symbols': symbols_str},
                headers=self.headers,
                timeout=self.timeout * 2,  # Double timeout for batch
            )
            response.raise_for_status()

            # Check response size to prevent OOM
            if len(response.content) > MAX_API_RESPONSE_SIZE:
                return Err(
                    AppError(
                        ErrorCode.EXTERNAL,
                        f"Response too large: {len(response.content)} bytes",
                    )
                )

            data = response.json()

            # Handle case where 'quotes' key exists but value is None
            quotes_data = data.get('quotes') or {}
            quotes = quotes_data.get('quote', [])

            # Handle single quote or list
            if not isinstance(quotes, list):
                quotes = [quotes]

            # Build result dict
            prices: Dict[str, Money] = {}
            for quote in quotes:
                if quote and quote.get('symbol') and quote.get('last'):
                    ticker = quote['symbol'].upper()
                    prices[ticker] = Money(float(quote['last']))

            logger.info(
                f"Batch fetched {len(prices)}/{len(tickers)} stock prices"
            )
            return Ok(prices)

        except requests.exceptions.Timeout:
            logger.warning("Timeout fetching batch prices")
            return Err(AppError(ErrorCode.TIMEOUT, "Batch quote timeout"))

        except requests.exceptions.RequestException as e:
            logger.error(f"Request error fetching batch prices: {e}")
            return Err(_request_error(e))

        except Exception as e:
            logger.error(f"Unexpected error fetching batch prices: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))

    def get_option_chain(
        self, ticker: str, expiration: date,
        stock_price: Optional[Money] = None,
        admitted: bool = False,
    ) -> Result[OptionChain, AppError]:
        """
        Get option chain for ticker and expiration.

        Args:
            ticker: Stock symbol
            expiration: Option expiration date
            stock_price: Known current price; when provided, skips the
                internal price fetch (saves one rate-limit token — callers
                with a cached price should pass it)
            admitted: The caller took this call's slot with admit(); it must
                pass stock_price, since one slot cannot pay for a quote too

        Returns:
            Result with OptionChain or AppError. The chain is sent once, never
            resent here (CHAINS_PATH): a 429 is RATELIMIT once Tradier's window has
            been waited out, a server error or failed connection EXTERNAL after
            CHAIN_RESEND_WAIT_SECONDS (or Retry-After), no answer in time TIMEOUT --
            each retryable.

        Without stock_price this quotes, then waits for the chain's slot, and the
        quote ages by that wait. CachedOptionsDataProvider, which every scan and
        analysis fetches through, prices its chains after the slot instead.
        """
        if admitted and stock_price is None:
            return Err(AppError(
                ErrorCode.INVALID,
                f"{ticker}: an admitted chain call needs its stock_price",
            ))
        try:
            if stock_price is None:
                # Get current stock price (includes its own rate limit check)
                price_result = self.get_stock_price(ticker)
                if price_result.is_err:
                    return Err(price_result.error)

                stock_price = price_result.value

            # Rate limit for chain request (get_stock_price already consumed 1 token).
            # Wait for a token instead of failing immediately.
            if not admitted and not self.admit():
                return Err(
                    AppError(
                        ErrorCode.RATELIMIT,
                        "Tradier rate limit exceeded",
                    )
                )

            # Get option chain
            response = self.session.get(
                f"{self.base_url}{CHAINS_PATH}",
                params={
                    'symbol': ticker,
                    'expiration': expiration.isoformat(),
                    'greeks': 'true',
                },
                headers=self.headers,
                timeout=self.timeout,
            )
            status = response.status_code
            if status == 429:
                wait = _rate_limit_wait(response.headers, 429) or 0.0
                logger.warning(f"{ticker}: Tradier refused the chain (429); waiting "
                               f"{wait:.1f}s and handing it back, not resending on this spot")
                self.sleep(wait)
                return Err(AppError(
                    ErrorCode.RATELIMIT,
                    f"Tradier rate limit (429) on the {ticker} {expiration} chain",
                    retryable=True,
                ))
            if status in RETRYABLE_SERVER_ERRORS:
                asked = _rate_limit_wait(response.headers, status)
                backoff = CHAIN_RESEND_WAIT_SECONDS if asked is None else asked
                logger.warning(f"{ticker}: Tradier answered the chain {status}; waiting "
                               f"{backoff:.1f}s and handing it back, not resending on this spot")
                self.sleep(backoff)
                return Err(AppError(
                    ErrorCode.EXTERNAL,
                    f"Tradier server error ({status}) on the {ticker} {expiration} chain",
                    retryable=True,
                ))
            response.raise_for_status()

            # Check response size to prevent OOM
            content_length = response.headers.get('Content-Length')
            if content_length and int(content_length) > MAX_API_RESPONSE_SIZE:
                return Err(
                    AppError(
                        ErrorCode.EXTERNAL,
                        f"Response too large: {content_length} bytes",
                    )
                )

            if len(response.content) > MAX_API_RESPONSE_SIZE:
                return Err(
                    AppError(
                        ErrorCode.EXTERNAL,
                        f"Response too large: {len(response.content)} bytes",
                    )
                )

            data = response.json()

            # Handle case where 'options' key exists but value is None
            options_data = data.get('options') or {}
            options = options_data.get('option', [])

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

                    # Parse Greeks (may be None if not available)
                    greeks = opt.get('greeks', {})
                    iv = None
                    delta = None
                    gamma = None
                    theta = None
                    vega = None

                    if greeks:
                        if greeks.get('mid_iv'):
                            iv = Percentage(float(greeks['mid_iv']) * 100)
                        # Delta: probability ITM (~0.0 to ~1.0 for calls, ~-1.0 to ~0.0 for puts)
                        if greeks.get('delta'):
                            delta = float(greeks['delta'])
                        # Gamma: rate of change of delta
                        if greeks.get('gamma'):
                            gamma = float(greeks['gamma'])
                        # Theta: time decay per day
                        if greeks.get('theta'):
                            theta = float(greeks['theta'])
                        # Vega: sensitivity to 1% change in IV
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
                        "Incomplete chain (missing calls or puts)",
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
            logger.warning(f"Timeout fetching chain for {ticker}; handing it back, "
                           f"not resending on this spot")
            return Err(AppError(ErrorCode.TIMEOUT, f"Timeout: {ticker}", retryable=True))

        except requests.exceptions.SSLError as e:      # a certificate does not clear on a resend
            logger.error(f"Request error fetching chain: {e}")
            return Err(_request_error(e))

        except requests.exceptions.ConnectionError as e:
            logger.warning(f"{ticker}: chain connection failed ({e}); waiting "
                           f"{CHAIN_RESEND_WAIT_SECONDS:.1f}s and handing it back")
            self.sleep(CHAIN_RESEND_WAIT_SECONDS)
            return Err(AppError(ErrorCode.EXTERNAL, str(e), retryable=True))

        except requests.exceptions.RequestException as e:
            logger.error(f"Request error fetching chain: {e}")
            return Err(_request_error(e))

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
        # Rate limit: this call previously bypassed the limiter entirely,
        # letting bursts run past the intended budget uncounted.
        if not self.admit():
            return Err(
                AppError(
                    ErrorCode.RATELIMIT,
                    "Tradier rate limit exceeded",
                )
            )

        try:
            response = self.session.get(
                f"{self.base_url}/markets/options/expirations",
                params={'symbol': ticker},
                headers=self.headers,
                timeout=self.timeout,
            )
            response.raise_for_status()

            # Check response size to prevent OOM
            content_length = response.headers.get('Content-Length')
            if content_length and int(content_length) > MAX_API_RESPONSE_SIZE:
                return Err(
                    AppError(
                        ErrorCode.EXTERNAL,
                        f"Response too large: {content_length} bytes",
                    )
                )

            if len(response.content) > MAX_API_RESPONSE_SIZE:
                return Err(
                    AppError(
                        ErrorCode.EXTERNAL,
                        f"Response too large: {len(response.content)} bytes",
                    )
                )

            data = response.json()
            # Handle case where 'expirations' key exists but has None value (preferred stocks)
            expirations_obj = data.get('expirations') or {}
            expirations = expirations_obj.get('date', [])

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
            return Err(_request_error(e))

        except Exception as e:
            logger.error(f"Unexpected error fetching expirations: {e}")
            return Err(AppError(ErrorCode.EXTERNAL, str(e)))

    def find_nearest_expiration(
        self, ticker: str, target_date: date
    ) -> Result[date, AppError]:
        """
        Find the nearest available expiration >= target date.

        Args:
            ticker: Stock symbol
            target_date: Desired expiration date

        Returns:
            Result with nearest available expiration date or error
        """
        expirations_result = self.get_expirations(ticker)
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
            # No expiration >= target, use the furthest available
            if expirations:
                nearest = expirations[-1]
                logger.warning(
                    f"{ticker}: No expiration >= {target_date}, using {nearest}"
                )
            else:
                return Err(
                    AppError(ErrorCode.NODATA, f"No expirations for {ticker}")
                )

        if nearest != target_date:
            logger.info(
                f"{ticker}: Adjusted expiration {target_date} → {nearest}"
            )

        return Ok(nearest)
