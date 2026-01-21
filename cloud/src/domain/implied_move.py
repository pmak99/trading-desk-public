"""
Implied move calculator from ATM straddle pricing.

Implied Move = ATM Straddle Price / Stock Price × 100

This module provides the shared implied move calculation logic used throughout
the application (handlers.py, main.py endpoints):

Core Functions:
- calculate_implied_move(): Calculate from known straddle prices
- calculate_implied_move_from_chain(): Calculate from options chain data
- find_atm_straddle(): Find ATM call/put from options chain

Shared Helpers (used by all job handlers and endpoints):
- fetch_real_implied_move(): Async helper to fetch real implied move from Tradier
  (calls quote, expirations, options_chain APIs - 3 calls per ticker)
- get_implied_move_with_fallback(): Extract result or fall back to estimate
- IMPLIED_MOVE_FALLBACK_MULTIPLIER: 1.5x fallback when real data unavailable
"""

from typing import Dict, Any, List, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from src.integrations.tradier import TradierClient

from src.core.logging import log

# Weekly options detection (synced from 2.0)
from src.application.filters.weekly_options import has_weekly_options


def calculate_implied_move(
    stock_price: float,
    call_price: float,
    put_price: float,
    call_iv: Optional[float] = None,
    put_iv: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Calculate implied move from straddle prices.

    Args:
        stock_price: Current stock price
        call_price: ATM call mid price
        put_price: ATM put mid price
        call_iv: Call implied volatility (optional)
        put_iv: Put implied volatility (optional)

    Returns:
        Dict with implied_move_pct, straddle_price, avg_iv
    """
    straddle_price = call_price + put_price
    implied_move_pct = (straddle_price / stock_price) * 100

    result = {
        "implied_move_pct": round(implied_move_pct, 2),
        "straddle_price": round(straddle_price, 2),
        "call_price": call_price,
        "put_price": put_price,
        "stock_price": stock_price,
    }

    if call_iv is not None and put_iv is not None:
        result["avg_iv"] = (call_iv + put_iv) / 2
        result["call_iv"] = call_iv
        result["put_iv"] = put_iv

    return result


def find_atm_straddle(
    chain: List[Dict[str, Any]],
    stock_price: float,
) -> Tuple[Optional[Dict], Optional[Dict]]:
    """
    Find ATM call and put from options chain.

    Args:
        chain: List of option contracts
        stock_price: Current stock price

    Returns:
        Tuple of (atm_call, atm_put) or (None, None) if not found
    """
    if not chain:
        return None, None

    # Get unique strikes
    strikes = sorted(set(opt["strike"] for opt in chain))

    if not strikes:
        return None, None

    # Find closest strike to stock price
    atm_strike = min(strikes, key=lambda s: abs(s - stock_price))

    # Find call and put at ATM strike
    atm_call = None
    atm_put = None

    for opt in chain:
        if opt["strike"] == atm_strike:
            if opt.get("option_type", "").lower() == "call":
                atm_call = opt
            elif opt.get("option_type", "").lower() == "put":
                atm_put = opt

    return atm_call, atm_put


def calculate_implied_move_from_chain(
    chain: List[Dict[str, Any]],
    stock_price: float,
) -> Optional[Dict[str, Any]]:
    """
    Calculate implied move directly from options chain.

    Args:
        chain: Options chain from Tradier
        stock_price: Current stock price

    Returns:
        Implied move data or None if ATM straddle not found
    """
    call, put = find_atm_straddle(chain, stock_price)

    if not call or not put:
        return None

    # Use mid prices
    call_mid = (call.get("bid", 0) + call.get("ask", 0)) / 2
    put_mid = (put.get("bid", 0) + put.get("ask", 0)) / 2

    # Validate mid prices are positive (prevents invalid 0% implied move)
    if call_mid <= 0 or put_mid <= 0:
        return None  # Trigger fallback to estimate

    # Get IVs if available
    call_iv = call.get("greeks", {}).get("mid_iv") or call.get("greeks", {}).get("iv")
    put_iv = put.get("greeks", {}).get("mid_iv") or put.get("greeks", {}).get("iv")

    result = calculate_implied_move(
        stock_price=stock_price,
        call_price=call_mid,
        put_price=put_mid,
        call_iv=call_iv,
        put_iv=put_iv,
    )

    # Add ATM strike for debugging/logging
    result["atm_strike"] = call.get("strike")

    return result


# Fallback multiplier when real options data unavailable
IMPLIED_MOVE_FALLBACK_MULTIPLIER = 1.5


async def fetch_real_implied_move(
    tradier: "TradierClient",
    ticker: str,
    earnings_date: str,
    price: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Fetch real implied move from Tradier options chain.

    Uses ATM straddle pricing for accurate implied move calculation.
    Falls back to estimate only if real data unavailable.

    Args:
        tradier: TradierClient instance
        ticker: Stock ticker symbol
        earnings_date: Earnings date (YYYY-MM-DD) to find expiration after
        price: Current stock price (fetched from Tradier if not provided)

    Returns:
        Dict with:
            - implied_move_pct: The implied move percentage
            - used_real_data: True if from options chain, False if estimated
            - atm_strike: ATM strike price (if real data)
            - straddle_price: ATM straddle price (if real data)
            - expiration: Options expiration used (if real data)
            - price: Stock price used for calculation
            - error: Error message (if fallback used)
    """
    result = {
        "implied_move_pct": None,
        "used_real_data": False,
        "atm_strike": None,
        "straddle_price": None,
        "expiration": None,
        "expirations": [],  # Full list of expirations for weekly check
        "has_weekly_options": True,  # Default to True (permissive on error)
        "weekly_reason": "",
        "price": None,  # Stock price used for calculation
        "error": None,
    }

    try:
        # Get price if not provided
        if price is None:
            quote = await tradier.get_quote(ticker)
            price = quote.get("last") or quote.get("close") or quote.get("prevclose")

        if not price:
            result["error"] = "No price available"
            return result

        result["price"] = price

        # Get expirations and find nearest one after earnings
        expirations = await tradier.get_expirations(ticker)

        # Store expirations for weekly check
        result["expirations"] = expirations

        # Check for weekly options availability
        has_weeklies, weekly_reason = has_weekly_options(expirations, earnings_date)
        result["has_weekly_options"] = has_weeklies
        result["weekly_reason"] = weekly_reason

        nearest_exp = None
        for exp in expirations:
            if exp >= earnings_date:
                nearest_exp = exp
                break

        if not nearest_exp:
            result["error"] = "No expiration after earnings date"
            return result

        # Fetch options chain
        chain = await tradier.get_options_chain(ticker, nearest_exp)

        # Validate chain is a non-empty list
        if not chain or not isinstance(chain, list) or len(chain) == 0:
            result["error"] = "Empty or invalid options chain"
            return result

        # Calculate implied move from ATM straddle
        im_data = calculate_implied_move_from_chain(chain, price)

        if im_data and im_data.get("implied_move_pct"):
            result["implied_move_pct"] = im_data["implied_move_pct"]
            result["used_real_data"] = True
            result["atm_strike"] = im_data.get("atm_strike")
            result["straddle_price"] = im_data.get("straddle_price")
            result["expiration"] = nearest_exp

            log("debug", "Fetched real implied move",
                ticker=ticker,
                implied_move=result["implied_move_pct"],
                atm_strike=result["atm_strike"],
                straddle_price=result["straddle_price"],
                expiration=nearest_exp)
        else:
            result["error"] = "Could not calculate implied move from chain"

    except Exception as e:
        result["error"] = str(e)
        log("debug", "Options data unavailable",
            ticker=ticker, error=str(e))

    return result


def get_implied_move_with_fallback(
    real_result: Dict[str, Any],
    historical_avg: float,
) -> Tuple[float, bool]:
    """
    Get implied move from real data or fall back to estimate.

    Args:
        real_result: Result from fetch_real_implied_move()
        historical_avg: Historical average move for fallback calculation

    Returns:
        Tuple of (implied_move_pct, used_real_data)
    """
    if real_result.get("used_real_data") and real_result.get("implied_move_pct"):
        return real_result["implied_move_pct"], True

    # Fallback to estimate
    return historical_avg * IMPLIED_MOVE_FALLBACK_MULTIPLIER, False
