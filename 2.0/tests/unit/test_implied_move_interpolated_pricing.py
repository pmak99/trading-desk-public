"""
Pricing accuracy of the interpolated implied-move calculator (audit 2026-09-13, step 3.2).

Every chain here is priced by Black-Scholes (zero rates, no dividends, bid = ask = model
price), so the true at-the-money straddle at spot is known exactly. The defect these tests
pin: linearly interpolating the two bracketing straddles averages two off-ATM prices that
each carry intrinsic value, and that chord sits above the curve -- +2.5% at $152.50 on $5
strikes, +5.6% at $51.25 on $2.50 strikes, +20.8% at $52.50 on $5 strikes, all at 80% IV
and 3 DTE. The implied move feeds VRP, and VRP feeds size.

The Black-Scholes reference below is written independently of the production code
(statistics.NormalDist, separate call and put formulas), so the calculator is not graded
against its own arithmetic.
"""

import logging
import math
from datetime import date, timedelta
from itertools import pairwise
from statistics import NormalDist
from unittest.mock import Mock

import pytest

from src.application.metrics.implied_move_interpolated import (
    ImpliedMoveCalculatorInterpolated,
)
from src.domain.errors import ErrorCode, Ok
from src.domain.types import Money, OptionChain, OptionQuote, Percentage, Strike

_N = NormalDist().cdf

# 3 calendar days on a 365-day year, as in the audit. The calculator itself never reads a
# time to expiry (see TestPricesComeFromTheQuotes); the chain needs one only so the
# reference straddle is defined.
T_3DTE = 3 / 365
EXPIRATION = date.today() + timedelta(days=3)


def bs_call(spot, strike, sigma, t):
    v = sigma * math.sqrt(t)
    d1 = math.log(spot / strike) / v + v / 2
    return spot * _N(d1) - strike * _N(d1 - v)


def bs_put(spot, strike, sigma, t):
    v = sigma * math.sqrt(t)
    d1 = math.log(spot / strike) / v + v / 2
    return strike * _N(-(d1 - v)) - spot * _N(-d1)


def true_atm_straddle(spot, sigma, t):
    return bs_call(spot, spot, sigma, t) + bs_put(spot, spot, sigma, t)


def quote(price, iv_pct, open_interest=1000):
    return OptionQuote(
        bid=Money(price),
        ask=Money(price),
        implied_volatility=None if iv_pct is None else Percentage(iv_pct),
        open_interest=open_interest,
        volume=100,
    )


def bs_chain(spot, spacing, sigma_of_strike, t=T_3DTE, quoted_iv=True, strikes_each_side=6):
    """A chain priced by Black-Scholes around the strike nearest ``spot``.

    ``sigma_of_strike`` is a sticky-strike smile: each strike's volatility does not depend
    on where spot is.
    """
    centre = round(spot / spacing) * spacing
    calls, puts = {}, {}
    for i in range(-strikes_each_side, strikes_each_side + 1):
        k = centre + i * spacing
        sigma = sigma_of_strike(k)
        iv = sigma * 100 if quoted_iv else None
        calls[Strike(k)] = quote(bs_call(spot, k, sigma, t), iv)
        puts[Strike(k)] = quote(bs_put(spot, k, sigma, t), iv)
    return OptionChain(
        ticker="TEST", expiration=EXPIRATION, stock_price=Money(spot), calls=calls, puts=puts
    )


def flat(sigma):
    return lambda _k: sigma


def put_skew(k):
    """Volatility rising 0.4 points per dollar of strike below 150."""
    return 0.80 + 0.004 * (150.0 - k)


def replace_strike(chain, strike_price, call=None, put=None):
    calls, puts = dict(chain.calls), dict(chain.puts)
    if call is not None:
        calls[Strike(strike_price)] = call
    if put is not None:
        puts[Strike(strike_price)] = put
    return OptionChain(
        ticker=chain.ticker, expiration=chain.expiration, stock_price=chain.stock_price,
        calls=calls, puts=puts,
    )


def run(chain):
    provider = Mock()
    provider.get_option_chain.return_value = Ok(chain)
    return ImpliedMoveCalculatorInterpolated(provider).calculate(chain.ticker, chain.expiration)


def straddle(chain):
    result = run(chain)
    assert result.is_ok, result.error
    return float(result.value.straddle_cost.amount)


def move_pct(chain):
    result = run(chain)
    assert result.is_ok, result.error
    return result.value.implied_move_pct.value


class TestUnbiasedBetweenStrikes:
    @pytest.mark.parametrize(
        "spot, spacing, sigma",
        [
            (152.50, 5.0, 0.80),    # audit: chord +2.5%
            (51.25, 2.5, 0.80),     # audit: chord +5.6%
            (52.50, 5.0, 0.80),     # audit: chord +20.8%
            (151.00, 5.0, 0.80),    # off the midpoint
            (1012.50, 25.0, 0.45),  # high-priced name, wide strikes, lower IV
        ],
    )
    def test_straddle_matches_black_scholes_at_spot(self, spot, spacing, sigma):
        result = run(bs_chain(spot, spacing, flat(sigma)))
        assert result.is_ok, result.error
        truth = true_atm_straddle(spot, sigma, T_3DTE)
        got = float(result.value.straddle_cost.amount)
        assert got == pytest.approx(truth, rel=1e-6)
        assert result.value.implied_move_pct.value == pytest.approx(truth / spot * 100, rel=1e-6)
        assert float(result.value.upper_bound.amount) == pytest.approx(spot + got, abs=1e-9)
        assert float(result.value.lower_bound.amount) == pytest.approx(spot - got, abs=1e-9)

    def test_flat_vol_move_does_not_depend_on_where_spot_sits(self):
        """At flat vol the true ATM straddle is a fixed fraction of spot. The chord
        scallops instead: exact at each strike, peaking midway between them."""
        sigma = 0.80
        truth = true_atm_straddle(100.0, sigma, T_3DTE)  # percent of a $100 spot
        spots = [147.50 + 0.25 * i for i in range(41)]  # 147.50 .. 157.50, across 150 and 155
        moves = [move_pct(bs_chain(s, 5.0, flat(sigma))) for s in spots]
        for s, m in zip(spots, moves, strict=True):
            assert m == pytest.approx(truth, abs=1e-4), f"spot {s}"

    @pytest.mark.parametrize("smile", [flat(0.80), put_skew], ids=["flat", "skewed"])
    def test_continuous_through_the_exact_atm_path(self, smile):
        """Within a cent of a strike the calculator prices the strike's own straddle; a cent
        further it interpolates. The two must agree, or a penny of spot moves the implied
        move."""
        spots = [149.97, 149.985, 149.995, 150.0, 150.005, 150.015, 150.03]
        moves = [move_pct(bs_chain(s, 5.0, smile)) for s in spots]
        for a, b in pairwise(moves):
            assert abs(a - b) < 0.001, moves

    def test_skewed_smile_prices_the_volatility_interpolated_to_spot(self):
        """With volatility linear in strike, interpolating each bracket's volatility by the
        strike weight lands on the smile at spot, so the ATM straddle is recovered."""
        spot = 152.50
        truth = true_atm_straddle(spot, put_skew(spot), T_3DTE)
        assert straddle(bs_chain(spot, 5.0, put_skew)) == pytest.approx(truth, rel=1e-6)


class TestPricesComeFromTheQuotes:
    """The exact-ATM path prices from the quotes, so the interpolated path must too.

    Tradier's IV arrives with its greeks, which may be absent, may lag the quotes, and are
    computed on a time-to-expiry convention this engine does not share. Reading the
    volatility back out of each bracket's own straddle needs no time convention at all.
    """

    def test_no_quoted_iv_needed(self):
        spot, sigma = 52.50, 0.80
        result = run(bs_chain(spot, 5.0, flat(sigma), quoted_iv=False))
        assert result.is_ok, result.error
        truth = true_atm_straddle(spot, sigma, T_3DTE)
        assert float(result.value.straddle_cost.amount) == pytest.approx(truth, rel=1e-6)
        assert result.value.avg_iv is None

    def test_quoted_iv_does_not_override_the_quotes(self):
        spot = 152.50
        chain = bs_chain(spot, 5.0, flat(0.80))
        chain = OptionChain(
            ticker="TEST", expiration=EXPIRATION, stock_price=Money(spot),
            calls={k: quote(float(q.mid.amount), 40.0) for k, q in chain.calls.items()},
            puts={k: quote(float(q.mid.amount), 40.0) for k, q in chain.puts.items()},
        )
        result = run(chain)
        assert result.is_ok, result.error
        truth = true_atm_straddle(spot, 0.80, T_3DTE)
        assert float(result.value.straddle_cost.amount) == pytest.approx(truth, rel=1e-6)
        # avg_iv is still the vendor's volatility, interpolated -- reported, not priced.
        assert result.value.avg_iv.value == pytest.approx(40.0)


class TestOneUsableBracket:
    @pytest.mark.parametrize("dead_strike", [150.0, 155.0], ids=["lower", "upper"])
    def test_illiquid_bracket_prices_the_other_brackets_vol_at_spot(self, dead_strike):
        """The one-bracket fallback used to return that bracket's raw straddle, intrinsic
        value and all."""
        spot, sigma = 152.50, 0.80
        chain = bs_chain(spot, 5.0, flat(sigma))
        k = Strike(dead_strike)
        chain = replace_strike(
            chain, dead_strike,
            call=quote(float(chain.calls[k].mid.amount), 80.0, open_interest=0),
            put=quote(float(chain.puts[k].mid.amount), 80.0, open_interest=0),
        )
        truth = true_atm_straddle(spot, sigma, T_3DTE)
        assert straddle(chain) == pytest.approx(truth, rel=1e-6)

    def test_bracket_quoted_at_or_below_intrinsic_is_not_used(self):
        """A straddle mid at or under intrinsic value has no time value to read a volatility
        from (a stale or crossed quote); the other bracket carries the estimate."""
        spot, sigma = 152.50, 0.80
        chain = bs_chain(spot, 5.0, flat(sigma))
        chain = replace_strike(chain, 150.0, call=quote(2.40, 80.0), put=quote(0.05, 80.0))
        truth = true_atm_straddle(spot, sigma, T_3DTE)
        assert straddle(chain) == pytest.approx(truth, rel=1e-6)

    def test_bracket_quoted_above_its_no_arbitrage_ceiling_is_not_used(self):
        """A straddle cannot be worth more than spot + strike (call <= spot, put <= strike)."""
        spot, sigma = 152.50, 0.80
        chain = bs_chain(spot, 5.0, flat(sigma))
        chain = replace_strike(chain, 155.0, call=quote(200.0, 80.0), put=quote(150.0, 80.0))
        truth = true_atm_straddle(spot, sigma, T_3DTE)
        assert straddle(chain) == pytest.approx(truth, rel=1e-6)

    def test_no_time_value_at_either_bracket_is_nodata(self):
        chain = bs_chain(152.50, 5.0, flat(0.80))
        chain = replace_strike(chain, 150.0, call=quote(2.40, 80.0), put=quote(0.05, 80.0))
        chain = replace_strike(chain, 155.0, call=quote(0.05, 80.0), put=quote(2.40, 80.0))
        result = run(chain)
        assert result.is_err
        assert result.error.code == ErrorCode.NODATA


class TestStraddleCostValidation:
    """Both paths run the sanity checks the standard calculator runs."""

    def test_interpolated_path_warns_on_implausibly_high_cost(self, caplog):
        # 400% IV over 30 days: the ATM straddle is most of the stock price.
        chain = bs_chain(152.50, 5.0, flat(4.0), t=30 / 365)
        with caplog.at_level(logging.WARNING):
            result = run(chain)
        assert result.is_ok, result.error
        assert "seems too high" in caplog.text

    def test_interpolated_path_warns_on_implausibly_low_cost(self, caplog):
        # 5% IV over 3 days on $1 strikes: the straddle is about a third of a percent.
        chain = bs_chain(150.50, 1.0, flat(0.05))
        with caplog.at_level(logging.WARNING):
            result = run(chain)
        assert result.is_ok, result.error
        assert "seems too low" in caplog.text

    def test_exact_path_warns_on_implausibly_high_cost(self, caplog):
        chain = bs_chain(150.0, 5.0, flat(4.0), t=30 / 365)
        with caplog.at_level(logging.WARNING):
            result = run(chain)
        assert result.is_ok, result.error
        assert "seems too high" in caplog.text

    def test_plausible_cost_does_not_warn(self, caplog):
        with caplog.at_level(logging.WARNING):
            result = run(bs_chain(152.50, 5.0, flat(0.80)))
        assert result.is_ok, result.error
        assert "seems too" not in caplog.text

    @pytest.mark.parametrize("target_pct", [0.49, 0.51, 29.9, 30.1])
    def test_both_paths_draw_the_line_in_the_same_place(self, caplog, target_pct):
        """The interpolated path restates the common module's bounds; hold them together.

        Flat vol whose true ATM straddle is ``target_pct`` of spot: 2*erf(v / 2*sqrt2) =
        pct/100, so v = 2 * Phi^-1(1/2 + pct/400)."""
        sigma = 2 * NormalDist().inv_cdf(0.5 + target_pct / 400) / math.sqrt(T_3DTE)

        def warned(spot):
            caplog.clear()
            with caplog.at_level(logging.WARNING):
                result = run(bs_chain(spot, 1.0, flat(sigma)))
            assert result.is_ok, result.error
            assert result.value.implied_move_pct.value == pytest.approx(target_pct, rel=1e-6)
            return "seems too" in caplog.text

        assert warned(150.0) == warned(150.50)
