"""
Unit tests for IV term-structure analysis and the calendar spread pilot
(Jun 2026 — term_structure.py, calendar_spread.py).
"""

import pytest
from datetime import date

from src.application.metrics.term_structure import (
    select_back_expiration,
    atm_iv,
    analyze_term_structure,
    classify_slope_ratio,
)
from src.application.services.calendar_spread import (
    build_calendar_spread,
    PILOT_MAX_CONTRACTS,
)
from src.domain.enums import DirectionalBias, OptionType, StrategyType
from src.domain.types import (
    Money,
    OptionChain,
    OptionQuote,
    Percentage,
    SizingContext,
    Strike,
)

FRONT = date(2026, 6, 19)
BACK = date(2026, 7, 17)


def make_quote(mid: float, iv: float = 50.0, oi: int = 500) -> OptionQuote:
    return OptionQuote(
        bid=Money(mid - 0.05),
        ask=Money(mid + 0.05),
        implied_volatility=Percentage(iv),
        open_interest=oi,
        volume=100,
    )


def make_chain(
    expiration: date,
    stock_price: float = 100.0,
    call_iv: float = 50.0,
    put_iv: float = 50.0,
    call_mid: float = 3.0,
    put_mid: float = 3.0,
    oi: int = 500,
) -> OptionChain:
    strikes = [Strike(p) for p in (95.0, 100.0, 105.0)]
    return OptionChain(
        ticker="TEST",
        expiration=expiration,
        stock_price=Money(stock_price),
        calls={s: make_quote(call_mid, call_iv, oi) for s in strikes},
        puts={s: make_quote(put_mid, put_iv, oi) for s in strikes},
    )


# ============================================================================
# select_back_expiration
# ============================================================================


class TestSelectBackExpiration:
    def test_picks_closest_to_30_days_out(self):
        expirations = [FRONT, date(2026, 6, 26), date(2026, 7, 17), date(2026, 8, 21)]
        assert select_back_expiration(expirations, FRONT) == date(2026, 7, 17)

    def test_excludes_expirations_within_min_gap(self):
        # Only the front and a 2-day-later expiry exist — too close, no back leg
        expirations = [FRONT, date(2026, 6, 21)]
        assert select_back_expiration(expirations, FRONT) is None

    def test_empty_list_returns_none(self):
        assert select_back_expiration([], FRONT) is None


# ============================================================================
# atm_iv / analyze_term_structure
# ============================================================================


class TestTermStructure:
    def test_atm_iv_averages_call_and_put(self):
        chain = make_chain(FRONT, call_iv=60.0, put_iv=50.0)
        assert atm_iv(chain) == pytest.approx(55.0)

    def test_backwardation_detected(self):
        front = make_chain(FRONT, call_iv=80.0, put_iv=80.0)
        back = make_chain(BACK, call_iv=50.0, put_iv=50.0)
        result = analyze_term_structure(front, back)
        assert result.is_ok
        ts = result.value
        assert ts.is_backwardation
        assert ts.slope == pytest.approx(-30.0)
        assert ts.slope_ratio == pytest.approx(1.6)
        assert ts.expirations == [FRONT, BACK]

    def test_contango_detected(self):
        front = make_chain(FRONT, call_iv=40.0, put_iv=40.0)
        back = make_chain(BACK, call_iv=50.0, put_iv=50.0)
        result = analyze_term_structure(front, back)
        assert result.is_ok
        assert not result.value.is_backwardation
        assert result.value.slope_ratio == pytest.approx(0.8)

    def test_missing_iv_returns_err(self):
        front = make_chain(FRONT)
        # Back chain with no IVs at all
        strikes = [Strike(100.0)]
        back = OptionChain(
            ticker="TEST", expiration=BACK, stock_price=Money(100.0),
            calls={s: OptionQuote(bid=Money(2.0), ask=Money(2.1)) for s in strikes},
            puts={s: OptionQuote(bid=Money(2.0), ask=Money(2.1)) for s in strikes},
        )
        assert analyze_term_structure(front, back).is_err

    def test_classification_bands(self):
        assert classify_slope_ratio(1.5) == "STEEP_BACKWARDATION"
        assert classify_slope_ratio(1.2) == "BACKWARDATION"
        assert classify_slope_ratio(1.05) == "MILD"
        assert classify_slope_ratio(0.9) == "FLAT_OR_CONTANGO"
        assert classify_slope_ratio(None) == "UNKNOWN"


# ============================================================================
# build_calendar_spread
# ============================================================================


class TestCalendarSpreadPilot:
    def test_builds_call_calendar_for_neutral_bias(self):
        front = make_chain(FRONT, call_mid=3.0)
        back = make_chain(BACK, call_mid=5.0)
        result = build_calendar_spread(front, back, DirectionalBias.NEUTRAL)
        assert result.is_ok
        cal = result.value
        assert cal.strategy_type == StrategyType.CALENDAR_SPREAD
        assert cal.contracts == PILOT_MAX_CONTRACTS
        assert all(leg.option_type == OptionType.CALL for leg in cal.legs)
        short = next(leg for leg in cal.legs if leg.is_short)
        long = next(leg for leg in cal.legs if leg.is_long)
        assert short.expiration == FRONT
        assert long.expiration == BACK
        assert short.strike == long.strike
        # Debit = back mid - front mid = 2.0; max loss = 2.0 * 100 * 10
        assert float(cal.max_loss.amount) == pytest.approx(2000.0)
        assert float(cal.net_credit.amount) == pytest.approx(-2.0)

    def test_bearish_bias_builds_put_calendar(self):
        front = make_chain(FRONT, put_mid=3.0)
        back = make_chain(BACK, put_mid=5.0)
        result = build_calendar_spread(front, back, DirectionalBias.BEARISH)
        assert result.is_ok
        assert all(leg.option_type == OptionType.PUT for leg in result.value.legs)

    def test_non_positive_debit_rejected(self):
        # Front more expensive than back (extreme event pricing) — calendar
        # would be a credit; that is a different structure, reject.
        front = make_chain(FRONT, call_mid=6.0)
        back = make_chain(BACK, call_mid=5.0)
        assert build_calendar_spread(front, back, DirectionalBias.NEUTRAL).is_err

    def test_illiquid_legs_rejected(self):
        front = make_chain(FRONT, call_mid=3.0, oi=0)
        back = make_chain(BACK, call_mid=5.0)
        assert build_calendar_spread(front, back, DirectionalBias.NEUTRAL).is_err

    def test_contracts_capped_by_pilot_not_trr(self):
        # Even with a permissive sizing context, pilot cap (10) binds
        front = make_chain(FRONT, call_mid=3.0)
        back = make_chain(BACK, call_mid=5.0)
        ctx = SizingContext(trr_level='NORMAL')  # cap 100
        result = build_calendar_spread(front, back, DirectionalBias.NEUTRAL, ctx)
        assert result.is_ok
        assert result.value.contracts == PILOT_MAX_CONTRACTS

    def test_strike_description_renders(self):
        front = make_chain(FRONT, call_mid=3.0)
        back = make_chain(BACK, call_mid=5.0)
        cal = build_calendar_spread(front, back, DirectionalBias.NEUTRAL).value
        desc = cal.strike_description
        assert "Sell" in desc and "Buy" in desc
        assert str(BACK) in desc
