"""
Tests for strategy/strike_selection.py — delta constants and all strike selection functions.
"""
import pytest
from decimal import Decimal
from datetime import date

from src.application.services.strategy.strike_selection import (
    find_nearest_strike,
    verify_strikes_outside_implied_move,
    get_asymmetric_deltas,
    select_strikes_delta_based,
    select_strikes_distance_based,
    select_strikes_for_spread,
    DELTA_ADJUSTMENT_STRONG, DELTA_ADJUSTMENT_MODERATE, DELTA_ADJUSTMENT_WEAK,
    MIN_DELTA, MAX_DELTA, MIN_SPREAD,
)
from src.config.config import StrategyConfig
from src.domain.types import Money, Strike, OptionQuote, OptionChain, VRPResult, Percentage
from src.domain.enums import DirectionalBias, OptionType, Recommendation


def make_strike(price: float) -> Strike:
    return Strike(price=Decimal(str(price)))


def make_quote(bid: float, ask: float, delta: float = None, oi: int = 100) -> OptionQuote:
    return OptionQuote(bid=Money(bid), ask=Money(ask), open_interest=oi, delta=delta)


def make_vrp(ratio: float = 2.0, implied_move_pct: float = 5.0) -> VRPResult:
    return VRPResult(
        ticker="TEST",
        expiration=date(2026, 7, 18),
        implied_move_pct=Percentage(implied_move_pct),
        historical_mean_move_pct=Percentage(implied_move_pct / ratio),
        vrp_ratio=ratio,
        edge_score=0.5,
        recommendation=Recommendation.EXCELLENT,
    )


def make_chain(stock_price: float, strikes_and_deltas: list, option_type: OptionType = OptionType.PUT) -> OptionChain:
    """Build a minimal OptionChain with given (price, delta) pairs."""
    chain = {}
    for price, delta in strikes_and_deltas:
        strike = make_strike(price)
        chain[strike] = make_quote(bid=1.0, ask=1.5, delta=delta)
    puts = chain if option_type == OptionType.PUT else {}
    calls = chain if option_type == OptionType.CALL else {}
    return OptionChain(
        ticker="TEST",
        expiration=date(2026, 7, 18),
        stock_price=Money(stock_price),
        calls=calls,
        puts=puts,
    )


@pytest.fixture
def config():
    return StrategyConfig()


class TestConstants:
    def test_strong_adjustment_is_10pct(self):
        assert DELTA_ADJUSTMENT_STRONG == 0.10

    def test_min_delta_floor(self):
        assert MIN_DELTA == 0.10

    def test_min_spread_gap(self):
        assert MIN_SPREAD == 0.05


class TestFindNearestStrike:
    def test_prefers_whole_dollar(self):
        strikes = [make_strike(94.5), make_strike(95.0), make_strike(95.5)]
        result = find_nearest_strike(strikes, 95.2)
        assert float(result.price) == 95.0  # nearest whole dollar

    def test_falls_back_to_fractional_when_no_whole_dollar(self):
        strikes = [make_strike(94.5), make_strike(95.5)]
        result = find_nearest_strike(strikes, 95.2)
        assert float(result.price) == 95.5

    def test_empty_list_returns_none(self):
        assert find_nearest_strike([], 95.0) is None


class TestVerifyStrikesOutsideImpliedMove:
    def test_put_spread_inside_implied_move_returns_none(self):
        chain = make_chain(100.0, [(97.0, -0.25), (93.0, -0.15)])
        vrp = make_vrp(2.0, implied_move_pct=5.0)  # 5% = $5 move, lower_bound=$95
        short_strike = make_strike(97.0)  # $97 > $95 → inside move → reject
        long_strike = make_strike(93.0)
        result = verify_strikes_outside_implied_move(
            "TEST", (short_strike, long_strike), chain, vrp, below=True
        )
        assert result is None

    def test_put_spread_outside_implied_move_returns_strikes(self):
        chain = make_chain(100.0, [(93.0, -0.15), (88.0, -0.10)])
        vrp = make_vrp(2.0, implied_move_pct=5.0)  # lower_bound=$95
        short_strike = make_strike(93.0)  # $93 < $95 → outside → ok
        long_strike = make_strike(88.0)
        result = verify_strikes_outside_implied_move(
            "TEST", (short_strike, long_strike), chain, vrp, below=True
        )
        assert result is not None

    def test_call_spread_inside_implied_move_returns_none(self):
        chain = make_chain(100.0, [], option_type=OptionType.CALL)
        vrp = make_vrp(2.0, implied_move_pct=5.0)  # upper_bound=$105
        short_strike = make_strike(104.0)  # $104 < $105 → inside → reject
        long_strike = make_strike(109.0)
        result = verify_strikes_outside_implied_move(
            "TEST", (short_strike, long_strike), chain, vrp, below=False
        )
        assert result is None


class TestGetAsymmetricDeltas:
    def test_neutral_bias_returns_config_defaults(self, config):
        short_d, long_d = get_asymmetric_deltas(config, OptionType.PUT, DirectionalBias.NEUTRAL)
        assert abs(short_d - config.target_delta_short) < 0.001
        assert abs(long_d - config.target_delta_long) < 0.001

    def test_strong_bullish_put_spread_lowers_deltas(self, config):
        short_d, long_d = get_asymmetric_deltas(config, OptionType.PUT, DirectionalBias.STRONG_BULLISH)
        # Bullish → put spread safer → lower deltas
        assert short_d < config.target_delta_short

    def test_strong_bearish_put_spread_raises_deltas(self, config):
        short_d, long_d = get_asymmetric_deltas(config, OptionType.PUT, DirectionalBias.STRONG_BEARISH)
        assert short_d > config.target_delta_short

    def test_spread_invariant_short_always_greater_than_long(self, config):
        for bias in DirectionalBias:
            for opt_type in [OptionType.PUT, OptionType.CALL]:
                s, l = get_asymmetric_deltas(config, opt_type, bias)
                assert s > l, f"short={s} <= long={l} for bias={bias.value}, type={opt_type.value}"

    def test_deltas_within_bounds(self, config):
        for bias in DirectionalBias:
            for opt_type in [OptionType.PUT, OptionType.CALL]:
                s, l = get_asymmetric_deltas(config, opt_type, bias)
                assert MIN_DELTA <= s <= MAX_DELTA
                assert MIN_DELTA <= l <= MAX_DELTA


class TestSelectStrikeDeltaBased:
    def test_selects_strikes_closest_to_target_delta(self, config):
        # Put chain: strikes at 90, 95, 97, 98 with increasing deltas
        chain = make_chain(100.0, [
            (90.0, -0.10), (95.0, -0.15), (97.0, -0.20), (98.0, -0.25),
        ])
        result = select_strikes_delta_based(
            config, chain, OptionType.PUT,
            target_delta_short=0.25, target_delta_long=0.20
        )
        assert result is not None
        short_s, long_s = result
        # short should be closer to ATM (higher price), long further OTM
        assert float(short_s.price) > float(long_s.price)

    def test_no_deltas_returns_none(self, config):
        chain = make_chain(100.0, [(95.0, None), (90.0, None)])
        result = select_strikes_delta_based(config, chain, OptionType.PUT)
        assert result is None


class TestSelectStrikesDistanceBased:
    def test_put_spread_below_implied_move(self, config):
        # Stock at $100, 5% implied move = $5, 10% buffer → target short=$94.5.
        # Exclude $95 from chain so nearest whole-dollar is clearly below target.
        strikes = [(s, None) for s in [80.0, 85.0, 90.0, 93.0, 97.0, 100.0]]
        chain = make_chain(100.0, strikes)
        vrp = make_vrp(2.0, implied_move_pct=5.0)
        result = select_strikes_distance_based(config, chain, vrp, OptionType.PUT, below=True)
        assert result is not None
        short_s, long_s = result
        # Short should be closer to ATM (higher price) than long
        assert float(short_s.price) > float(long_s.price)
        # Short strike must be near the target ($94.5), i.e. below the implied-move lower bound
        assert float(short_s.price) <= 95.0

    def test_call_spread_above_implied_move(self, config):
        from src.domain.types import OptionChain
        # Stock at $100, 5% implied move → target short≈$105.5, long≈$110.5.
        call_chain = {}
        for price in [100.0, 103.0, 105.0, 108.0, 110.0, 115.0, 120.0]:
            sk = make_strike(price)
            call_chain[sk] = make_quote(bid=1.0, ask=1.5)
        chain = OptionChain(
            ticker="TEST", expiration=date(2026, 7, 18), stock_price=Money(100.0),
            calls=call_chain, puts={},
        )
        vrp = make_vrp(2.0, implied_move_pct=5.0)
        result = select_strikes_distance_based(config, chain, vrp, OptionType.CALL, below=False)
        assert result is not None
        short_s, long_s = result
        # For call spread: long strike must be above short (further OTM)
        assert float(long_s.price) > float(short_s.price)
