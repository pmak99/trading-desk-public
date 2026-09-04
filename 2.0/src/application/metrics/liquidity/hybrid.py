"""Hybrid C-then-B liquidity tier classification and dynamic threshold calculation."""
from typing import Optional, Tuple, Dict, Any
from src.application.metrics.liquidity.score import LiquidityThresholds
from src.application.metrics.liquidity.scoring import calculate_spread_pct
from src.domain.types import OptionChain, Strike, OptionQuote
from src.utils.market_hours import get_market_status


def calculate_dynamic_thresholds(
    stock_price: float,
    max_loss_budget: float = 20000.0,
    credit_ratio: float = 0.30,
) -> Dict[str, Any]:
    if stock_price >= 1000:
        spread_width = 100.0
        price_tier = "$1000+"
    elif stock_price >= 500:
        spread_width = 50.0
        price_tier = "$500-1000"
    elif stock_price >= 200:
        spread_width = 20.0
        price_tier = "$200-500"
    elif stock_price >= 100:
        spread_width = 10.0
        price_tier = "$100-200"
    elif stock_price >= 20:
        spread_width = 5.0
        price_tier = "$20-100"
    else:
        spread_width = 2.50
        price_tier = "<$20"

    credit_estimate = spread_width * credit_ratio
    max_loss_per_spread = (spread_width - credit_estimate) * 100
    contracts = int(max_loss_budget / max_loss_per_spread)
    min_oi = contracts * 1
    warning_oi = contracts * 2
    good_oi = contracts * 5

    return {
        'spread_width': spread_width,
        'contracts': contracts,
        'min_oi': min_oi,
        'warning_oi': warning_oi,
        'good_oi': good_oi,
        'max_loss_budget': max_loss_budget,
        'price_tier': price_tier,
    }


def find_strike_outside_move(
    chain: OptionChain,
    implied_move_pct: float,
    is_call: bool,
) -> Optional[Tuple[Strike, OptionQuote]]:
    stock_price = float(chain.stock_price.amount)
    move_decimal = implied_move_pct / 100.0

    if is_call:
        target_strike = stock_price * (1 + move_decimal)
        strikes = sorted(chain.calls.keys(), key=lambda s: float(s.price))
        for strike in strikes:
            if float(strike.price) >= target_strike:
                option = chain.calls.get(strike)
                if option:
                    return (strike, option)
    else:
        target_strike = stock_price * (1 - move_decimal)
        strikes = sorted(chain.puts.keys(), key=lambda s: float(s.price), reverse=True)
        for strike in strikes:
            if float(strike.price) <= target_strike:
                option = chain.puts.get(strike)
                if option:
                    return (strike, option)
    return None


def find_delta_strike(
    chain: OptionChain,
    target_delta: float,
    is_call: bool,
) -> Optional[Tuple[Strike, OptionQuote]]:
    options = chain.calls if is_call else chain.puts
    best_result = None
    best_delta_diff = float('inf')

    for strike, option in options.items():
        if option.delta is None:
            continue
        delta = abs(float(option.delta))
        delta_diff = abs(delta - target_delta)
        if delta_diff < best_delta_diff:
            best_delta_diff = delta_diff
            best_result = (strike, option)
    return best_result


def classify_hybrid_tier(
    chain: OptionChain,
    implied_move_pct: float,
    t: LiquidityThresholds,
    stock_price: Optional[float] = None,
    max_loss_budget: float = 20000.0,
    use_dynamic_thresholds: bool = True,
) -> Tuple[str, Dict[str, Any]]:
    if stock_price is None:
        stock_price = float(chain.stock_price.amount)

    if use_dynamic_thresholds:
        thresholds = calculate_dynamic_thresholds(stock_price, max_loss_budget)
        min_oi = thresholds['min_oi']
        warning_oi = thresholds['warning_oi']
        good_oi = thresholds['good_oi']
    else:
        min_oi = t.min_oi
        warning_oi = t.min_oi * 2
        good_oi = t.excellent_oi
        thresholds = {'contracts': 'N/A', 'spread_width': 'N/A'}

    market_open, market_reason = get_market_status()

    details: Dict[str, Any] = {
        'method': None, 'call_strike': None, 'put_strike': None,
        'call_oi': 0, 'put_oi': 0, 'min_oi': 0,
        'call_spread_pct': 100.0, 'put_spread_pct': 100.0,
        'market_open': market_open, 'market_reason': market_reason,
        'thresholds': thresholds, 'fallback_used': False,
    }

    call_c = find_strike_outside_move(chain, implied_move_pct, is_call=True)
    put_c = find_strike_outside_move(chain, implied_move_pct, is_call=False)

    c_valid = (
        call_c is not None and put_c is not None and
        (call_c[1].open_interest or 0) > 0 and
        (put_c[1].open_interest or 0) > 0
    )

    if c_valid:
        call_strike, call_option = call_c
        put_strike, put_option = put_c
        details['method'] = 'C (outside implied move)'
    else:
        call_b = find_delta_strike(chain, target_delta=0.20, is_call=True)
        put_b = find_delta_strike(chain, target_delta=0.20, is_call=False)
        if call_b is not None and put_b is not None:
            call_strike, call_option = call_b
            put_strike, put_option = put_b
            details['method'] = 'B (20-delta fallback)'
            details['fallback_used'] = True
        else:
            details['method'] = 'FAILED'
            return ("REJECT", details)

    call_oi = call_option.open_interest or 0
    put_oi = put_option.open_interest or 0
    min_oi_found = min(call_oi, put_oi)
    call_spread_pct = calculate_spread_pct(call_option)
    put_spread_pct = calculate_spread_pct(put_option)
    max_spread = max(call_spread_pct, put_spread_pct)

    details['call_strike'] = float(call_strike.price)
    details['put_strike'] = float(put_strike.price)
    details['call_oi'] = call_oi
    details['put_oi'] = put_oi
    details['min_oi'] = min_oi_found
    details['call_spread_pct'] = call_spread_pct
    details['put_spread_pct'] = put_spread_pct
    details['oi_ratio'] = (
        min_oi_found / thresholds['contracts']
        if thresholds['contracts'] != 'N/A' and thresholds['contracts'] > 0
        else None
    )

    if min_oi_found < min_oi:
        oi_tier = "REJECT"
    elif min_oi_found < warning_oi:
        oi_tier = "WARNING"
    elif min_oi_found < good_oi:
        oi_tier = "GOOD"
    else:
        oi_tier = "EXCELLENT"

    if max_spread > t.max_spread_pct:
        spread_tier = "REJECT"
    elif max_spread >= t.warning_spread_pct:
        spread_tier = "WARNING"
    elif max_spread >= t.good_spread_pct:
        spread_tier = "GOOD"
    else:
        spread_tier = "EXCELLENT"

    tier_order = {"REJECT": 0, "WARNING": 1, "GOOD": 2, "EXCELLENT": 3}
    tier = min([oi_tier, spread_tier], key=lambda x: tier_order[x])
    details['oi_tier'] = oi_tier
    details['spread_tier'] = spread_tier

    return (tier, details)


def classify_hybrid_tier_market_aware(
    chain: OptionChain,
    implied_move_pct: float,
    t: LiquidityThresholds,
    stock_price: Optional[float] = None,
    max_loss_budget: float = 20000.0,
    use_dynamic_thresholds: bool = True,
) -> Tuple[str, bool, str, Dict[str, Any]]:
    tier, details = classify_hybrid_tier(
        chain=chain, implied_move_pct=implied_move_pct, t=t,
        stock_price=stock_price, max_loss_budget=max_loss_budget,
        use_dynamic_thresholds=use_dynamic_thresholds,
    )
    return (tier, details['market_open'], details['market_reason'], details)
