"""
POP for a vertical credit spread: the probability the underlying finishes beyond
the BREAKEVEN on the profitable side, not 1 - |short delta|.

Every expectation here is computed in a Black-Scholes world (zero rates, no
dividends) where the quotes, their deltas and their IVs are mutually consistent,
so there is one right answer:

    P(S_T > X) = N(d2(X)),  d2(X) = (ln(S/X) - v^2/2) / v,  v = sigma * sqrt(T)

Two things the old `1 - |delta|` got wrong, and these tests pin both:
  * delta is N(d1), not the exercise probability N(d2) -- it overstates a put
    spread's POP and understates a call spread's, by more as sigma*sqrt(T) grows;
  * it ignored the credit. The breakeven sits FURTHER from the money than the
    short strike (put: K - credit, call: K + credit), so a larger credit RAISES POP.
"""
from datetime import date, datetime, timezone
from decimal import Decimal
from math import exp, log, sqrt
from statistics import NormalDist

import pytest

from src.application.services.strategy.metrics import (
    FALLBACK_POP,
    calculate_spread_metrics,
    years_to_expiration,
)
from src.domain.types import MARKET_TZ, Money, OptionQuote, Percentage, Strike

_N = NormalDist()
S = 100.0


def bs_put_call(kind: str, strike: float, sigma: float, years: float):
    """(price, delta) for a European option under zero rates and dividends."""
    v = sigma * sqrt(years)
    d1 = (log(S / strike) + v * v / 2) / v
    d2 = d1 - v
    if kind == "call":
        return S * _N.cdf(d1) - strike * _N.cdf(d2), _N.cdf(d1)
    return strike * _N.cdf(-d2) - S * _N.cdf(-d1), _N.cdf(d1) - 1.0


def strike_for_delta(kind: str, abs_delta: float, sigma: float, years: float) -> float:
    v = sigma * sqrt(years)
    d1 = _N.inv_cdf(abs_delta) if kind == "call" else _N.inv_cdf(1.0 - abs_delta)
    return S / exp(d1 * v - v * v / 2)


def analytic_pop(kind: str, breakeven: float, sigma: float, years: float) -> float:
    """P(S_T beyond breakeven on the profitable side), straight from the price."""
    v = sigma * sqrt(years)
    d2 = (log(S / breakeven) - v * v / 2) / v
    return _N.cdf(d2) if kind == "put" else _N.cdf(-d2)


def bs_quote(kind: str, strike: float, sigma: float, years: float, mid: float = None) -> OptionQuote:
    price, delta = bs_put_call(kind, strike, sigma, years)
    mid = price if mid is None else mid
    return OptionQuote(
        bid=Money(mid - 0.05),
        ask=Money(mid + 0.05),
        implied_volatility=Percentage(sigma * 100),
        open_interest=500,
        delta=delta,
    )


def spread(kind: str, width: float, sigma: float, days: float, abs_delta: float = 0.25,
           long_mid: float = None):
    """A BS-consistent short/long pair; long_mid overrides the long leg's price."""
    years = days / 365.0
    k_short = strike_for_delta(kind, abs_delta, sigma, years)
    k_long = k_short - width if kind == "put" else k_short + width
    short_q = bs_quote(kind, k_short, sigma, years)
    long_q = bs_quote(kind, k_long, sigma, years, mid=long_mid)
    return short_q, long_q, Strike(Decimal(repr(k_short))), Strike(Decimal(repr(k_long))), years


def metrics_for(kind, width, sigma, days, **kw):
    short_q, long_q, k_s, k_l, years = spread(kind, width, sigma, days, **kw)
    result = calculate_spread_metrics(
        short_q, long_q, k_s, k_l, stock_price=Money(S), years_to_expiry=years,
    )
    return result, short_q, years


class TestPutSpreadPop:
    def test_25_delta_put_with_large_credit_is_below_delta_pop_and_matches_analytic(self):
        # 150% IV, 7 days, $10 wide, $2.30 credit (23% of width).
        result, short_q, years = metrics_for("put", 10.0, 1.5, 7)
        assert short_q.delta == pytest.approx(-0.25)
        assert float(result["net_credit"].amount) == pytest.approx(2.30, abs=0.01)

        expected = analytic_pop("put", float(result["breakeven"].amount), 1.5, years)
        assert result["pop"] < 0.75
        assert result["pop"] == pytest.approx(expected, abs=1e-6)
        assert expected == pytest.approx(0.7235, abs=5e-4)

    def test_zero_credit_put_matches_short_strike_probability(self):
        short_q, _, k_s, k_l, years = spread("put", 5.0, 1.0, 4)
        # Long leg priced identically: no credit, breakeven == short strike.
        long_q = OptionQuote(bid=short_q.bid, ask=short_q.ask, open_interest=500,
                             implied_volatility=short_q.implied_volatility, delta=-0.15)
        result = calculate_spread_metrics(
            short_q, long_q, k_s, k_l, stock_price=Money(S), years_to_expiry=years,
        )
        assert float(result["net_credit"].amount) == pytest.approx(0.0, abs=1e-9)
        short_strike_prob = analytic_pop("put", float(k_s.price), 1.0, years)
        assert result["pop"] == pytest.approx(short_strike_prob, abs=1e-6)
        # delta is N(d1); the exercise probability is N(d2) -- lower for a put.
        assert result["pop"] < 1.0 - abs(short_q.delta)

    def test_larger_credit_raises_put_pop(self):
        pops = [metrics_for("put", w, 1.0, 4)[0]["pop"] for w in (2.5, 5.0, 10.0)]
        assert pops[0] < pops[1] < pops[2]


class TestCallSpreadPop:
    def test_25_delta_call_with_large_credit_matches_analytic(self):
        result, short_q, years = metrics_for("call", 10.0, 1.5, 7)
        assert short_q.delta == pytest.approx(0.25)

        expected = analytic_pop("call", float(result["breakeven"].amount), 1.5, years)
        assert result["pop"] == pytest.approx(expected, abs=1e-6)
        # Mirror of the put case: N(d1) UNDERSTATES a call spread's POP.
        assert result["pop"] > 0.75

    def test_zero_credit_call_matches_short_strike_probability(self):
        short_q, _, k_s, k_l, years = spread("call", 5.0, 1.0, 4)
        long_q = OptionQuote(bid=short_q.bid, ask=short_q.ask, open_interest=500,
                             implied_volatility=short_q.implied_volatility, delta=0.15)
        result = calculate_spread_metrics(
            short_q, long_q, k_s, k_l, stock_price=Money(S), years_to_expiry=years,
        )
        short_strike_prob = analytic_pop("call", float(k_s.price), 1.0, years)
        assert result["pop"] == pytest.approx(short_strike_prob, abs=1e-6)
        assert result["pop"] > 1.0 - abs(short_q.delta)

    def test_larger_credit_raises_call_pop(self):
        pops = [metrics_for("call", w, 1.0, 4)[0]["pop"] for w in (2.5, 5.0, 10.0)]
        assert pops[0] < pops[1] < pops[2]


class TestPopInputs:
    @pytest.mark.parametrize("kind", ["put", "call"])
    def test_without_delta_uses_stock_price_and_iv(self, kind):
        short_q, long_q, k_s, k_l, years = spread(kind, 5.0, 0.8, 5)
        no_delta = OptionQuote(bid=short_q.bid, ask=short_q.ask, open_interest=500,
                               implied_volatility=short_q.implied_volatility)
        result = calculate_spread_metrics(
            no_delta, long_q, k_s, k_l, stock_price=Money(S), years_to_expiry=years,
        )
        expected = analytic_pop(kind, float(result["breakeven"].amount), 0.8, years)
        assert result["pop"] == pytest.approx(expected, abs=1e-6)

    def test_delta_anchors_the_strike_when_the_stock_price_disagrees(self):
        # Greeks are a snapshot; the delta already carries the vendor's own
        # spot and time convention. A different stock price must not move POP.
        short_q, long_q, k_s, k_l, years = spread("put", 5.0, 1.0, 4)
        at_100 = calculate_spread_metrics(short_q, long_q, k_s, k_l,
                                          stock_price=Money(100.0), years_to_expiry=years)
        at_103 = calculate_spread_metrics(short_q, long_q, k_s, k_l,
                                          stock_price=Money(103.0), years_to_expiry=years)
        assert at_100["pop"] == pytest.approx(at_103["pop"], abs=1e-12)

    def test_more_time_lowers_put_pop_at_fixed_strikes_and_credit(self):
        short_q, long_q, k_s, k_l, years = spread("put", 5.0, 1.0, 4)
        no_delta = OptionQuote(bid=short_q.bid, ask=short_q.ask, open_interest=500,
                               implied_volatility=short_q.implied_volatility)
        near = calculate_spread_metrics(no_delta, long_q, k_s, k_l, stock_price=Money(S),
                                        years_to_expiry=years)
        far = calculate_spread_metrics(no_delta, long_q, k_s, k_l, stock_price=Money(S),
                                       years_to_expiry=years * 4)
        assert far["pop"] < near["pop"]
        assert far["pop"] == pytest.approx(
            analytic_pop("put", float(far["breakeven"].amount), 1.0, years * 4), abs=1e-6)

    def test_degenerate_delta_uses_stock_price_instead(self):
        # 1 - 1e-17 rounds to 1.0, where the inverse normal is undefined.
        short_q, long_q, k_s, k_l, years = spread("call", 5.0, 1.0, 4)
        tiny = OptionQuote(bid=short_q.bid, ask=short_q.ask, open_interest=500,
                           implied_volatility=short_q.implied_volatility, delta=1e-17)
        result = calculate_spread_metrics(tiny, long_q, k_s, k_l,
                                          stock_price=Money(S), years_to_expiry=years)
        expected = analytic_pop("call", float(result["breakeven"].amount), 1.0, years)
        assert result["pop"] == pytest.approx(expected, abs=1e-6)

    def test_returned_keys_unchanged(self):
        result, _, _ = metrics_for("put", 5.0, 1.0, 4)
        assert set(result) == {"net_credit", "max_profit", "max_loss", "breakeven", "pop",
                               "reward_risk", "natural_credit", "natural_max_loss"}


class TestPopFallback:
    """Only where an input is genuinely missing; never more optimistic than 70%
    or than the short strike's own delta."""

    def test_fallback_is_conservative(self):
        assert FALLBACK_POP <= 0.70

    def test_missing_iv_with_delta_uses_the_lower_of_fallback_and_delta(self):
        short_q, long_q, k_s, k_l, years = spread("put", 5.0, 1.0, 4, abs_delta=0.35)
        no_iv = OptionQuote(bid=short_q.bid, ask=short_q.ask, open_interest=500, delta=short_q.delta)
        result = calculate_spread_metrics(no_iv, long_q, k_s, k_l,
                                          stock_price=Money(S), years_to_expiry=years)
        assert result["pop"] == pytest.approx(min(FALLBACK_POP, 1.0 - 0.35))

        short_q, long_q, k_s, k_l, years = spread("put", 5.0, 1.0, 4, abs_delta=0.15)
        no_iv = OptionQuote(bid=short_q.bid, ask=short_q.ask, open_interest=500, delta=short_q.delta)
        result = calculate_spread_metrics(no_iv, long_q, k_s, k_l,
                                          stock_price=Money(S), years_to_expiry=years)
        assert result["pop"] == pytest.approx(FALLBACK_POP)

    def test_missing_time_to_expiry_falls_back(self):
        short_q, long_q, k_s, k_l, _ = spread("call", 5.0, 1.0, 4)
        result = calculate_spread_metrics(short_q, long_q, k_s, k_l, stock_price=Money(S))
        assert result["pop"] == pytest.approx(FALLBACK_POP)

    def test_expired_falls_back(self):
        short_q, long_q, k_s, k_l, _ = spread("call", 5.0, 1.0, 4)
        result = calculate_spread_metrics(short_q, long_q, k_s, k_l,
                                          stock_price=Money(S), years_to_expiry=0.0)
        assert result["pop"] == pytest.approx(FALLBACK_POP)

    def test_no_delta_no_iv_falls_back(self):
        quote = OptionQuote(bid=Money(1.9), ask=Money(2.1), open_interest=500)
        long_q = OptionQuote(bid=Money(0.9), ask=Money(1.1), open_interest=500)
        result = calculate_spread_metrics(quote, long_q, Strike(95), Strike(90),
                                          stock_price=Money(S), years_to_expiry=4 / 365)
        assert result["pop"] == pytest.approx(FALLBACK_POP)

    def test_no_delta_no_stock_price_falls_back(self):
        short_q, long_q, k_s, k_l, years = spread("put", 5.0, 1.0, 4)
        no_delta = OptionQuote(bid=short_q.bid, ask=short_q.ask, open_interest=500,
                               implied_volatility=short_q.implied_volatility)
        result = calculate_spread_metrics(no_delta, long_q, k_s, k_l, years_to_expiry=years)
        assert result["pop"] == pytest.approx(FALLBACK_POP)


class TestYearsToExpiration:
    """Calendar time to the 4pm ET close on expiration day, over a 365-day year."""

    def test_monday_morning_to_friday_close(self):
        as_of = datetime(2026, 7, 13, 10, 0, tzinfo=MARKET_TZ)  # Monday
        assert years_to_expiration(date(2026, 7, 17), as_of) == pytest.approx(4.25 / 365)

    def test_weekend_counts_as_calendar_time(self):
        as_of = datetime(2026, 7, 9, 16, 0, tzinfo=MARKET_TZ)  # Thursday close
        assert years_to_expiration(date(2026, 7, 17), as_of) == pytest.approx(8 / 365)

    def test_naive_datetime_is_eastern(self):
        naive = datetime(2026, 7, 13, 10, 0)
        aware = naive.replace(tzinfo=MARKET_TZ)
        assert years_to_expiration(date(2026, 7, 17), naive) == pytest.approx(
            years_to_expiration(date(2026, 7, 17), aware))

    def test_other_timezones_are_converted(self):
        as_of_utc = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)  # 10:00 EDT
        assert years_to_expiration(date(2026, 7, 17), as_of_utc) == pytest.approx(4.25 / 365)

    def test_after_the_close_on_expiration_day_is_not_positive(self):
        as_of = datetime(2026, 7, 17, 16, 30, tzinfo=MARKET_TZ)
        assert years_to_expiration(date(2026, 7, 17), as_of) <= 0
