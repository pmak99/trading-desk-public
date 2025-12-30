"""
Tradier API client for 3.0 ML System.
Simplified version borrowed from 2.0.
"""

import os
import time
import requests
import logging
from datetime import date
from typing import Dict, List, Optional, Callable, TypeVar
from dataclasses import dataclass
from functools import wraps

logger = logging.getLogger(__name__)

__all__ = [
    'retry_with_backoff',
    'OptionQuote',
    'OptionChain',
    'ImpliedMove',
    'TradierAPI',
]

T = TypeVar('T')


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    retryable_exceptions: tuple = (requests.exceptions.RequestException,)
) -> Callable:
    """
    Decorator for retrying functions with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay cap in seconds
        exponential_base: Base for exponential backoff
        retryable_exceptions: Tuple of exceptions to retry on
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = min(base_delay * (exponential_base ** attempt), max_delay)
                        logger.warning(
                            f"{func.__name__} failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                        time.sleep(delay)
                    else:
                        logger.error(f"{func.__name__} failed after {max_retries + 1} attempts: {e}")
            raise last_exception
        return wrapper
    return decorator


@dataclass
class OptionQuote:
    """Single option contract quote."""
    strike: float
    option_type: str  # 'call' or 'put'
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    iv: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None


@dataclass
class OptionChain:
    """Full option chain for a ticker/expiration."""
    ticker: str
    expiration: date
    stock_price: float
    calls: List[OptionQuote]
    puts: List[OptionQuote]


@dataclass
class ImpliedMove:
    """Calculated implied move from ATM straddle."""
    ticker: str
    expiration: date
    stock_price: float
    atm_strike: float
    call_mid: float
    put_mid: float
    straddle_cost: float
    implied_move_pct: float
    upper_bound: float
    lower_bound: float


class TradierAPI:
    """
    Tradier brokerage API client.
    """

    def __init__(self, api_key: Optional[str] = None):
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
        self.timeout = 10

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def get_stock_price(self, ticker: str) -> float:
        """Get current stock price."""
        response = requests.get(
            f"{self.base_url}/markets/quotes",
            params={'symbols': ticker},
            headers=self.headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()

        quotes_data = data.get('quotes') or {}
        quotes = quotes_data.get('quote', [])
        if not isinstance(quotes, list):
            quotes = [quotes]

        if not quotes or not quotes[0].get('last'):
            raise ValueError(f"No price data for {ticker}")

        return float(quotes[0]['last'])

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def get_expirations(self, ticker: str) -> List[date]:
        """Get available option expirations."""
        response = requests.get(
            f"{self.base_url}/markets/options/expirations",
            params={'symbol': ticker},
            headers=self.headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()

        exp_data = data.get('expirations') or {}
        dates = exp_data.get('date', [])
        if not isinstance(dates, list):
            dates = [dates]

        return [date.fromisoformat(d) for d in dates if d]

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def get_option_chain(self, ticker: str, expiration: date, greeks: bool = True) -> OptionChain:
        """Get option chain for ticker and expiration."""
        stock_price = self.get_stock_price(ticker)

        response = requests.get(
            f"{self.base_url}/markets/options/chains",
            params={
                'symbol': ticker,
                'expiration': expiration.isoformat(),
                'greeks': 'true' if greeks else 'false',
            },
            headers=self.headers,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()

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

    def calculate_implied_move(self, ticker: str, expiration: date) -> ImpliedMove:
        """Calculate implied move from ATM straddle."""
        chain = self.get_option_chain(ticker, expiration)

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
