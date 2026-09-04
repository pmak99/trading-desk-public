"""Option scoring and tier classification — module-level functions taking LiquidityThresholds."""
from typing import Optional, Tuple
from src.application.metrics.liquidity.score import LiquidityScore, LiquidityThresholds
from src.domain.types import OptionQuote, Money
from src.utils.market_hours import get_market_status


def calculate_spread_pct(option: OptionQuote) -> float:
    """Bid-ask spread as % of mid. Returns 100.0 if no bid/ask."""
    if option.bid and option.ask:
        spread = float(option.ask.amount - option.bid.amount)
        mid = float(option.mid.amount)
        return (spread / mid * 100) if mid > 0 else 100.0
    return 100.0


def score_open_interest(oi: int, t: LiquidityThresholds) -> float:
    if oi >= t.excellent_oi:
        return 100.0
    elif oi >= t.good_oi:
        ratio = (oi - t.good_oi) / (t.excellent_oi - t.good_oi)
        return 80.0 + (ratio * 20.0)
    elif oi >= t.min_oi:
        ratio = (oi - t.min_oi) / (t.good_oi - t.min_oi)
        return 50.0 + (ratio * 30.0)
    else:
        ratio = oi / t.min_oi
        return ratio * 50.0


def score_volume(volume: int, t: LiquidityThresholds) -> float:
    if volume >= t.excellent_volume:
        return 100.0
    elif volume >= t.good_volume:
        ratio = (volume - t.good_volume) / (t.excellent_volume - t.good_volume)
        return 80.0 + (ratio * 20.0)
    elif volume >= t.min_volume:
        ratio = (volume - t.min_volume) / (t.good_volume - t.min_volume)
        return 50.0 + (ratio * 30.0)
    else:
        ratio = volume / t.min_volume if t.min_volume > 0 else 0
        return ratio * 50.0


def score_spread(spread_pct: float, t: LiquidityThresholds) -> float:
    if spread_pct <= t.excellent_spread_pct:
        return 100.0
    elif spread_pct <= t.good_spread_pct:
        ratio = (spread_pct - t.excellent_spread_pct) / (t.good_spread_pct - t.excellent_spread_pct)
        return 100.0 - (ratio * 20.0)
    elif spread_pct <= t.max_spread_pct:
        ratio = (spread_pct - t.good_spread_pct) / (t.max_spread_pct - t.good_spread_pct)
        return 80.0 - (ratio * 30.0)
    else:
        ratio = min(1.0, (spread_pct - t.max_spread_pct) / t.max_spread_pct)
        return 50.0 * (1.0 - ratio)


def score_depth(bid_size: Optional[int], ask_size: Optional[int]) -> float:
    if bid_size is None or ask_size is None:
        return 50.0
    min_size = min(bid_size, ask_size)
    if min_size >= 50:
        return 100.0
    elif min_size >= 10:
        ratio = (min_size - 10) / 40
        return 80.0 + (ratio * 20.0)
    elif min_size >= 5:
        ratio = (min_size - 5) / 5
        return 60.0 + (ratio * 20.0)
    else:
        return (min_size / 5) * 60.0


def redistribute_weights_without_depth(t: LiquidityThresholds) -> dict:
    total = t.oi_weight + t.volume_weight + t.spread_weight
    return {
        'oi': t.oi_weight / total,
        'volume': t.volume_weight / total,
        'spread': t.spread_weight / total,
    }


def classify_tier(oi: int, volume: int, spread_pct: float, t: LiquidityThresholds) -> str:
    if oi < t.min_oi or volume < t.min_volume:
        oi_tier = "REJECT"
    elif oi < t.warning_oi:
        oi_tier = "REJECT"
    elif oi < t.good_oi:
        oi_tier = "WARNING"
    elif oi < t.excellent_oi:
        oi_tier = "GOOD"
    else:
        oi_tier = "EXCELLENT"

    if spread_pct > t.max_spread_pct:
        spread_tier = "REJECT"
    elif spread_pct >= t.warning_spread_pct:
        spread_tier = "WARNING"
    elif spread_pct >= t.good_spread_pct:
        spread_tier = "GOOD"
    else:
        spread_tier = "EXCELLENT"

    tier_order = {"REJECT": 0, "WARNING": 1, "GOOD": 2, "EXCELLENT": 3}
    return min([oi_tier.upper(), spread_tier.upper()], key=lambda x: tier_order[x])


def classify_tier_oi_only(oi: int, spread_pct: float, t: LiquidityThresholds) -> str:
    if oi < t.min_oi:
        oi_tier = "REJECT"
    elif oi < t.warning_oi:
        oi_tier = "REJECT"
    elif oi < t.good_oi:
        oi_tier = "WARNING"
    elif oi < t.excellent_oi:
        oi_tier = "GOOD"
    else:
        oi_tier = "EXCELLENT"

    if spread_pct > t.max_spread_pct:
        spread_tier = "REJECT"
    elif spread_pct >= t.warning_spread_pct:
        spread_tier = "WARNING"
    elif spread_pct >= t.good_spread_pct:
        spread_tier = "GOOD"
    else:
        spread_tier = "EXCELLENT"

    tier_order = {"REJECT": 0, "WARNING": 1, "GOOD": 2, "EXCELLENT": 3}
    return min([oi_tier.upper(), spread_tier.upper()], key=lambda x: tier_order[x])


def score_option(option: OptionQuote, t: LiquidityThresholds) -> LiquidityScore:
    oi = option.open_interest or 0
    volume = option.volume or 0
    spread_pct = calculate_spread_pct(option)

    if option.bid and option.ask:
        spread = float(option.ask.amount - option.bid.amount)
        effective_spread = Money(spread)
    else:
        effective_spread = Money(999.99)

    oi_score = score_open_interest(oi, t)
    volume_score = score_volume(volume, t)
    spread_score = score_spread(spread_pct, t)

    depth_score = None
    if hasattr(option, 'bid_size') and hasattr(option, 'ask_size'):
        depth_score = score_depth(option.bid_size, option.ask_size)

    if depth_score is not None:
        overall = (
            oi_score * t.oi_weight +
            volume_score * t.volume_weight +
            spread_score * t.spread_weight +
            depth_score * t.depth_weight
        )
    else:
        weights = redistribute_weights_without_depth(t)
        overall = (
            oi_score * weights['oi'] +
            volume_score * weights['volume'] +
            spread_score * weights['spread']
        )

    tier = classify_tier(oi, volume, spread_pct, t)
    is_liquid = (
        oi >= t.min_oi and
        volume >= t.min_volume and
        spread_pct <= t.max_spread_pct
    )

    return LiquidityScore(
        overall_score=overall, oi_score=oi_score, volume_score=volume_score,
        spread_score=spread_score, depth_score=depth_score,
        open_interest=oi, volume=volume, bid_ask_spread_pct=spread_pct,
        effective_spread=effective_spread, is_liquid=is_liquid, liquidity_tier=tier,
    )


def classify_option_tier(option: OptionQuote, t: LiquidityThresholds) -> str:
    oi = option.open_interest or 0
    volume = option.volume or 0
    spread_pct = calculate_spread_pct(option)
    return classify_tier(oi, volume, spread_pct, t)


def classify_option_tier_oi_only(option: OptionQuote, t: LiquidityThresholds) -> str:
    oi = option.open_interest or 0
    spread_pct = calculate_spread_pct(option)
    return classify_tier_oi_only(oi, spread_pct, t)


def classify_straddle_tier(call: OptionQuote, put: OptionQuote, t: LiquidityThresholds) -> str:
    call_tier = classify_option_tier(call, t)
    put_tier = classify_option_tier(put, t)
    tier_order = {"REJECT": 0, "WARNING": 1, "GOOD": 2, "EXCELLENT": 3}
    return min([call_tier, put_tier], key=lambda x: tier_order[x])


def classify_straddle_tier_market_aware(
    call: OptionQuote, put: OptionQuote, t: LiquidityThresholds
) -> Tuple[str, bool, str]:
    market_open, market_reason = get_market_status()
    if market_open:
        tier = classify_straddle_tier(call, put, t)
    else:
        call_tier = classify_option_tier_oi_only(call, t)
        put_tier = classify_option_tier_oi_only(put, t)
        tier_order = {"REJECT": 0, "WARNING": 1, "GOOD": 2, "EXCELLENT": 3}
        tier = min([call_tier, put_tier], key=lambda x: tier_order[x])
    return (tier, market_open, market_reason)


def score_strategy_legs(legs: list, t: LiquidityThresholds) -> dict:
    if not legs:
        return {'min_score': 0, 'avg_score': 0, 'all_liquid': False, 'scores': []}
    scores = [score_option(leg, t) for leg in legs]
    min_score = min(s.overall_score for s in scores)
    avg_score = sum(s.overall_score for s in scores) / len(scores)
    all_liquid = all(s.is_liquid for s in scores)
    return {'min_score': min_score, 'avg_score': avg_score, 'all_liquid': all_liquid, 'scores': scores}
