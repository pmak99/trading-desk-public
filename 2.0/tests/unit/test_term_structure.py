"""
Unit tests for IV term-structure analysis and the calendar spread pilot
(Jun 2026 — term_structure.py, calendar_spread.py).
"""

from datetime import date
from unittest.mock import Mock

import pytest

from src.application.metrics.liquidity_scorer import LiquidityScorer
from src.application.metrics.term_structure import (
    analyze_term_structure,
    atm_iv,
    classify_slope_ratio,
    select_back_expiration,
)
from src.application.services.calendar_spread import (
    PILOT_MAX_CONTRACTS,
    build_calendar_spread,
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

# The tiers the container's scorer grades with (ThresholdsConfig defaults): open
# interest from 200 EXCELLENT, 100 GOOD, 50 WARNING, below 50 REJECT. make_quote's
# 0.10 wide market is under the 12% spread bar at both legs' mids.
SCORER = LiquidityScorer()
OI_FOR_TIER = {"EXCELLENT": 500, "GOOD": 150, "WARNING": 75, "REJECT": 20}


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
        result = build_calendar_spread(front, back, SCORER, DirectionalBias.NEUTRAL)
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
        result = build_calendar_spread(front, back, SCORER, DirectionalBias.BEARISH)
        assert result.is_ok
        assert all(leg.option_type == OptionType.PUT for leg in result.value.legs)

    def test_non_positive_debit_rejected(self):
        # Front more expensive than back (extreme event pricing) — calendar
        # would be a credit; that is a different structure, reject.
        front = make_chain(FRONT, call_mid=6.0)
        back = make_chain(BACK, call_mid=5.0)
        assert build_calendar_spread(front, back, SCORER, DirectionalBias.NEUTRAL).is_err

    def test_illiquid_legs_rejected(self):
        front = make_chain(FRONT, call_mid=3.0, oi=0)
        back = make_chain(BACK, call_mid=5.0)
        assert build_calendar_spread(front, back, SCORER, DirectionalBias.NEUTRAL).is_err

    def test_contracts_capped_by_pilot_not_trr(self):
        # Even with a permissive sizing context, pilot cap (10) binds
        front = make_chain(FRONT, call_mid=3.0)
        back = make_chain(BACK, call_mid=5.0)
        ctx = SizingContext(trr_level='NORMAL')  # cap 100
        result = build_calendar_spread(front, back, SCORER, DirectionalBias.NEUTRAL, ctx)
        assert result.is_ok
        assert result.value.contracts == PILOT_MAX_CONTRACTS

    def test_strike_description_renders(self):
        front = make_chain(FRONT, call_mid=3.0)
        back = make_chain(BACK, call_mid=5.0)
        cal = build_calendar_spread(front, back, SCORER, DirectionalBias.NEUTRAL).value
        desc = cal.strike_description
        assert "Sell" in desc and "Buy" in desc
        assert str(BACK) in desc

    def test_carries_the_liquidity_of_both_legs(self):
        """The builder measured both legs and dropped the numbers until 2026-09-14.
        A calendar is two expirations, so both legs count, not only the short one
        a vertical's liquidity reads: the thinner OI and the wider spread."""
        front = make_chain(FRONT, call_mid=3.0, oi=500)   # spread 0.10 / 3.00 = 3.33%
        back = make_chain(BACK, call_mid=5.0, oi=300)     # spread 0.10 / 5.00 = 2.00%
        cal = build_calendar_spread(front, back, SCORER, DirectionalBias.NEUTRAL).value
        assert cal.min_open_interest == 300
        assert cal.max_spread_pct == pytest.approx(10 / 3)

    def test_analyze_shows_the_pilots_liquidity(self):
        """/analyze prints the pilot for a human to check at the broker (CLAUDE.md §2);
        the legs' liquidity is what that check needs."""
        from scripts.analyze import _log_calendar_pilot

        front = make_chain(FRONT, call_mid=3.0, oi=500)
        back = make_chain(BACK, call_mid=5.0, oi=300)
        cal = build_calendar_spread(front, back, SCORER, DirectionalBias.NEUTRAL).value
        lines = []
        _log_calendar_pilot(lines.append, cal, max_loss_warn_threshold=50000.0)
        assert "   Liquidity: EXCELLENT · min OI 300 · widest spread 3.3% (both legs)" in lines


class TestCalendarPilotLiquiditySizing:
    """CLAUDE.md §2: WARNING liquidity -> half size, REJECT -> skip. Until 2026-09-14
    the builder refused only a leg failing OptionQuote.is_liquid (any open interest,
    a spread under 50%), so a WARNING leg, or a REJECT one with OI 20, took the full
    10. The legs are graded by the scorer a vertical's are, and the cut is
    SizingContext's rule-3 reduction, the one a vertical's cap takes."""

    @staticmethod
    def _build(front_tier="EXCELLENT", back_tier="EXCELLENT", ctx=None):
        front = make_chain(FRONT, call_mid=3.0, oi=OI_FOR_TIER[front_tier])
        back = make_chain(BACK, call_mid=5.0, oi=OI_FOR_TIER[back_tier])
        return build_calendar_spread(front, back, SCORER, DirectionalBias.NEUTRAL, ctx)

    @pytest.mark.parametrize("tier", ["EXCELLENT", "GOOD"])
    def test_excellent_or_good_legs_take_the_pilot_cap(self, tier):
        cal = self._build(tier, tier, SizingContext(trr_level='HIGH')).value
        assert cal.contracts == PILOT_MAX_CONTRACTS == 10
        assert cal.liquidity_tier == "EXCELLENT"

    @pytest.mark.parametrize("ctx", [None, SizingContext(trr_level='HIGH')])
    @pytest.mark.parametrize("front_tier,back_tier", [("WARNING", "EXCELLENT"),
                                                      ("EXCELLENT", "WARNING")])
    def test_a_warning_leg_halves_the_pilot_cap(self, front_tier, back_tier, ctx):
        cal = self._build(front_tier, back_tier, ctx).value
        assert cal.contracts == 5
        assert cal.liquidity_tier == "WARNING"
        # Every dollar figure follows the count: debit 2.00 x 100 x 5.
        assert float(cal.max_loss.amount) == pytest.approx(1000.0)
        assert float(cal.capital_required.amount) == pytest.approx(1000.0)
        assert all(leg.contracts == 5 for leg in cal.legs)

    @pytest.mark.parametrize("front_tier,back_tier", [("REJECT", "EXCELLENT"),
                                                      ("EXCELLENT", "REJECT")])
    def test_a_reject_leg_builds_nothing(self, front_tier, back_tier):
        result = self._build(front_tier, back_tier, SizingContext(trr_level='HIGH'))
        assert result.is_err
        assert "REJECT" in result.error.message

    def test_the_cut_is_the_sizing_contexts_reduction(self):
        """SIZING_REDUCTION_FACTOR moves it, as it moves a vertical's cut."""
        ctx = SizingContext(trr_level='HIGH', reduction_factor=0.4)
        assert self._build("WARNING", "EXCELLENT", ctx).value.contracts == 4

    def test_marginal_vrp_halves_it_and_does_not_stack_with_warning(self):
        """Rule 3 is MARGINAL VRP or WARNING liquidity: one 50% cut, not two."""
        ctx = SizingContext(trr_level='HIGH', vrp_marginal=True)
        assert self._build("EXCELLENT", "EXCELLENT", ctx).value.contracts == 5
        assert self._build("WARNING", "EXCELLENT", ctx).value.contracts == 5

    def test_the_analyzer_grades_the_legs_with_the_containers_scorer(self):
        """The analyzer swallows any exception in the term-structure step and
        returns no calendar, so a wiring mistake would be silent."""
        from src.application.services.analyzer import TickerAnalyzer
        from src.container import Container
        from src.domain.errors import Ok
        from src.infrastructure.cache.memory_cache import CachedOptionsDataProvider

        front = make_chain(FRONT, call_mid=3.0, call_iv=80.0, put_iv=80.0,
                           oi=OI_FOR_TIER["WARNING"])
        back = make_chain(BACK, call_mid=5.0, oi=OI_FOR_TIER["EXCELLENT"])
        provider = Mock(spec=CachedOptionsDataProvider)
        provider.get_expirations.return_value = Ok([FRONT, BACK])
        provider.get_option_chain.return_value = Ok(back)
        container = Mock(spec=Container)
        container.cached_options_provider = provider
        container.liquidity_scorer = SCORER

        ts, cal = TickerAnalyzer(container)._analyze_term_structure(
            "TEST", FRONT, front, skew=None, sizing_ctx=SizingContext(trr_level='HIGH'))
        assert ts.slope_ratio == pytest.approx(1.6)
        assert cal is not None
        assert cal.contracts == 5
