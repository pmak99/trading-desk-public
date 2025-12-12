"""
Implied move calculator from ATM straddle pricing.

Implied Move = ATM Straddle Price / Stock Price × 100
"""

from typing import Dict, Any, List, Optional, Tuple


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

    # Get IVs if available
    call_iv = call.get("greeks", {}).get("mid_iv") or call.get("greeks", {}).get("iv")
    put_iv = put.get("greeks", {}).get("mid_iv") or put.get("greeks", {}).get("iv")

    return calculate_implied_move(
        stock_price=stock_price,
        call_price=call_mid,
        put_price=put_mid,
        call_iv=call_iv,
        put_iv=put_iv,
    )
