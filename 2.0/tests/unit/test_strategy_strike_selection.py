"""
Tests for strategy/strike_selection.py — the strike selection functions.

That directional bias never moves the deltas is tested end to end in
test_strategy_generator.py (TestShortStrikeIgnoresBias).
"""
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from src.application.services.strategy.spread_builder import build_vertical_spread
from src.application.services.strategy.strike_selection import (
    find_nearest_strike,
    select_strikes_delta_based,
    select_strikes_distance_based,
    select_strikes_for_spread,
    verify_strikes_outside_implied_move,
)
from src.config.config import StrategyConfig
from src.domain.enums import OptionType, Recommendation, StrategyType
from src.domain.types import (
    Money,
    OptionChain,
    OptionQuote,
    Percentage,
    Strike,
    VRPResult,
)


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


class TestSelectStrikeDeltaBased:
    def test_selects_strikes_closest_to_target_delta(self, config):
        # Put chain: strikes at 90, 95, 97, 98 with increasing deltas
        chain = make_chain(100.0, [
            (90.0, -0.10), (95.0, -0.15), (97.0, -0.20), (98.0, -0.25),
        ])
        result = select_strikes_delta_based(config, chain, OptionType.PUT)
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


def _sparse_chain(option_type: OptionType, drop_beyond: float = None) -> OptionChain:
    """$250 stock on $10 strikes, where 25Δ and 20Δ land on one listed strike.

    Seen live on FDX. Puts: $230 sits at 22Δ, nearest both 25Δ (0.03 away) and
    20Δ (0.02 away; $220 is 0.08 away). Calls: $270 at 23Δ, likewise.
    `drop_beyond` removes every strike further OTM than that price.
    """
    rows = {
        OptionType.PUT: [(250.0, -0.50, 9.00), (240.0, -0.35, 5.50), (230.0, -0.22, 3.00),
                         (220.0, -0.12, 1.40), (210.0, -0.06, 0.60)],
        OptionType.CALL: [(250.0, 0.50, 9.00), (260.0, 0.36, 5.50), (270.0, 0.23, 3.00),
                          (280.0, 0.13, 1.40), (290.0, 0.07, 0.60)],
    }[option_type]
    put = option_type == OptionType.PUT
    side = {
        make_strike(price): OptionQuote(bid=Money(mid - 0.05), ask=Money(mid + 0.05),
                                        open_interest=500, delta=delta)
        for price, delta, mid in rows
        if drop_beyond is None or (price >= drop_beyond if put else price <= drop_beyond)
    }
    return OptionChain(
        ticker="TEST", expiration=date(2026, 7, 18), stock_price=Money(250.0),
        puts=side if put else {}, calls={} if put else side,
    )


BOTH_SIDES = pytest.mark.parametrize("option_type, below", [
    (OptionType.PUT, True), (OptionType.CALL, False),
])


class TestSparseChainNeverZeroWidth:
    """When 25Δ and 20Δ map to one listed strike, the long leg steps out a strike.

    Delta-based selection chose each leg independently, so on a sparse chain both
    legs came back as the same strike. That zero-width pair passed the implied-move
    check, so the distance-based fallback never ran, and the builder refused it for
    zero credit: no spread was offered at all (FDX). The short stays nearest 25Δ;
    the long is the strike nearest 20Δ among those strictly further OTM. Only when
    no such strike exists does delta-based selection return None, and the module's
    existing fallback to distance-based selection runs. Distance-based selection
    had the same flaw (its $5 long target rounded back to the short on a $10 chain)
    and gets the same rule.
    """

    @BOTH_SIDES
    def test_delta_based_long_leg_is_the_next_strike_out(self, config, option_type, below):
        short_s, long_s = select_strikes_delta_based(config, _sparse_chain(option_type), option_type)
        assert (float(short_s.price), float(long_s.price)) == (
            (230.0, 220.0) if below else (270.0, 280.0))

    @BOTH_SIDES
    def test_the_builder_offers_the_spread(self, config, option_type, below):
        scorer = MagicMock()
        scorer.classify_option_tier.return_value = "EXCELLENT"
        strategy = build_vertical_spread(
            config, scorer, "TEST", _sparse_chain(option_type),
            make_vrp(2.0, implied_move_pct=4.0), option_type,
            StrategyType.BULL_PUT_SPREAD if below else StrategyType.BEAR_CALL_SPREAD, below=below,
        )
        assert strategy is not None
        legs = {leg.action: float(leg.strike.price) for leg in strategy.legs}
        assert legs == ({"SELL": 230.0, "BUY": 220.0} if below else {"SELL": 270.0, "BUY": 280.0})
        assert float(strategy.net_credit.amount) == pytest.approx(1.60)

    @BOTH_SIDES
    def test_delta_based_with_nothing_beyond_the_short_is_none(self, config, option_type, below):
        chain = _sparse_chain(option_type, drop_beyond=230.0 if below else 270.0)
        assert select_strikes_delta_based(config, chain, option_type) is None

    @BOTH_SIDES
    def test_distance_based_long_leg_is_the_next_strike_out(self, config, option_type, below):
        # A 3% move is $7.50; with the 10% buffer the short target is $241.75
        # ($258.25), nearest $240 ($260), and the $5-wide long target rounds back
        # to that same strike on a $10 chain.
        short_s, long_s = select_strikes_distance_based(
            config, _sparse_chain(option_type), make_vrp(2.0, implied_move_pct=3.0),
            option_type, below=below,
        )
        assert (float(short_s.price), float(long_s.price)) == (
            (240.0, 230.0) if below else (260.0, 270.0))

    @BOTH_SIDES
    def test_distance_based_with_nothing_beyond_the_short_is_none(self, config, option_type, below):
        chain = _sparse_chain(option_type, drop_beyond=240.0 if below else 260.0)
        vrp = make_vrp(2.0, implied_move_pct=3.0)
        assert select_strikes_distance_based(config, chain, vrp, option_type, below=below) is None

    @BOTH_SIDES
    def test_when_delta_based_cannot_pair_the_distance_fallback_runs(self, config, option_type, below):
        # Nothing listed beyond the 25Δ strike: delta-based gives up, and
        # distance-based pairs $240/$230 ($260/$270) on the strikes that remain.
        chain = _sparse_chain(option_type, drop_beyond=230.0 if below else 270.0)
        short_s, long_s = select_strikes_for_spread(
            config, "TEST", chain, make_vrp(2.0, implied_move_pct=3.0), option_type, below=below,
        )
        assert (float(short_s.price), float(long_s.price)) == (
            (240.0, 230.0) if below else (260.0, 270.0))

    @BOTH_SIDES
    @pytest.mark.parametrize("move", [1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0])
    def test_selection_never_returns_a_zero_width_pair(self, config, option_type, below, move):
        strikes = select_strikes_for_spread(
            config, "TEST", _sparse_chain(option_type), make_vrp(2.0, implied_move_pct=move),
            option_type, below=below,
        )
        if strikes is not None:
            short_s, long_s = strikes
            assert (long_s < short_s) if below else (short_s < long_s)
