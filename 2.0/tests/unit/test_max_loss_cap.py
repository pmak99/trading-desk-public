"""The engine sizes within MAX_LOSS_WARN_THRESHOLD, on the natural price's max loss.

Until 2026-09-16 two numbers meant "risk per trade" and disagreed:
RISK_BUDGET_PER_TRADE x KELLY_FRACTION targets $62,500 of max loss, while
/analyze warned above $50,000, so a wide spread at full quality came out at a
size the same screen flagged. And every dollar figure was priced at leg mids, a
credit the market may not give. Now every structure the engine sizes (the
verticals, the calendar pilot, the scan's full-size position) holds contracts x
worst-case max loss per spread to the threshold, and the worst case is the
natural price: sold legs at the bid, bought legs at the ask.
"""
from datetime import date
from decimal import Decimal

import pytest

from src.application.metrics.liquidity_scorer import LiquidityScorer
from src.application.services.calendar_spread import build_calendar_spread
from src.application.services.strategy.metrics import calculate_spread_metrics
from src.application.services.strategy.sizing import (
    calculate_contracts,
    calculate_contracts_kelly,
    max_loss_warning,
)
from src.application.services.strategy.spread_builder import build_vertical_spread
from src.config.config import Config, StrategyConfig
from src.domain.enums import DirectionalBias, OptionType, Recommendation, StrategyType
from src.domain.types import (
    DEFAULT_MAX_LOSS_WARN_THRESHOLD,
    Money,
    OptionChain,
    OptionQuote,
    Percentage,
    SizingContext,
    Strike,
    VRPResult,
    full_size_contracts,
    max_loss_capped_contracts,
)

THRESHOLD = 50_000.0


def quote(bid, ask, delta=None, oi=500, iv=None):
    return OptionQuote(bid=Money(bid), ask=Money(ask), open_interest=oi, delta=delta,
                       implied_volatility=Percentage(iv) if iv else None)


class TestCappedContracts:
    def test_holds_the_position_within_the_threshold(self):
        assert max_loss_capped_contracts(50, 1400.0, THRESHOLD) == 35       # 49,000
        assert max_loss_capped_contracts(50, 350.0, THRESHOLD) == 50        # 17,500: no cut

    def test_an_exact_multiple_is_within(self):
        assert max_loss_capped_contracts(50, 1250.0, THRESHOLD) == 40       # 50,000 exactly

    @pytest.mark.parametrize("per_spread", [0.1 * 3, 333.33, 1234.567, 999.999, 4999.99])
    def test_never_over_the_line_the_warning_draws(self, per_spread):
        n = max_loss_capped_contracts(10_000, per_spread, THRESHOLD)
        assert n * per_spread <= THRESHOLD
        assert max_loss_warning(n * per_spread, THRESHOLD, n) is None

    def test_one_spread_over_the_threshold_stays_at_one_and_warns(self):
        assert max_loss_capped_contracts(50, 60_000.0, THRESHOLD) == 1
        assert max_loss_warning(60_000.0, THRESHOLD, 1) is not None

    @pytest.mark.parametrize("threshold", [0.0, -1.0])
    def test_zero_or_less_turns_it_off_like_the_warning(self, threshold):
        assert max_loss_capped_contracts(50, 3500.0, threshold) == 50
        assert max_loss_warning(175_000.0, threshold, 50) is None


class TestTheThresholdReachesEveryPath:
    def test_the_domain_default_is_the_config_default(self):
        assert DEFAULT_MAX_LOSS_WARN_THRESHOLD == StrategyConfig().max_loss_warn_threshold
        assert SizingContext().max_loss_warn_threshold == DEFAULT_MAX_LOSS_WARN_THRESHOLD

    def test_env_reaches_the_live_context(self, monkeypatch):
        monkeypatch.setenv("MAX_LOSS_WARN_THRESHOLD", "30000")
        ctx = SizingContext.from_strategy_config(Config.from_env().strategy, trr_level="HIGH")
        assert ctx.max_loss_warn_threshold == 30_000.0
        assert ctx.cap_to_max_loss(50, 1400.0) == 21

    def test_the_scan_position_is_capped(self):
        # $20-wide at a 30% credit: $1,400 max loss. 62,500 / 1,400 = 44 -> 35.
        assert full_size_contracts(StrategyConfig(), 1400.0) == 35


class TestSizerUsesTheWorstCase:
    CONFIG = StrategyConfig()

    def _size(self, mid_loss, natural_loss=None, context=None, config=None):
        return calculate_contracts_kelly(
            config or self.CONFIG,
            max_profit=Money(mid_loss),            # EV far above 5%: full quality
            max_loss=Money(mid_loss),
            probability_of_profit=0.95,
            sizing_context=context if context is not None else SizingContext(),
            worst_case_max_loss=Money(natural_loss) if natural_loss is not None else None,
        )

    def test_the_natural_max_loss_binds_when_larger(self):
        # 62,500 / 1,000 = 62 -> cap 50; at mid 50 x 1,000 = 50,000 fits, but a
        # fill at natural is 1,300 a spread: 38 x 1,300 = 49,400.
        assert self._size(1000.0) == 50
        assert self._size(1000.0, natural_loss=1300.0) == 38

    def test_a_natural_below_mid_never_loosens_it(self):
        assert self._size(1400.0, natural_loss=900.0) == 35

    def test_the_fallback_path_without_a_context_reads_config(self):
        config = StrategyConfig(max_loss_warn_threshold=20_000.0)
        n = calculate_contracts_kelly(config, Money(1400.0), Money(1400.0), 0.95)
        assert n == 14
        assert calculate_contracts(config, Money(1400.0), worst_case_max_loss=Money(1500.0)) == 13

    @pytest.mark.parametrize("mid_loss", [150.0, 400.0, 900.0, 1400.0, 2600.0, 4800.0, 9500.0])
    @pytest.mark.parametrize("pop", [0.72, 0.8, 0.95])
    def test_no_engine_size_trips_the_warning(self, mid_loss, pop):
        natural = mid_loss * 1.08
        n = calculate_contracts_kelly(
            self.CONFIG, Money(mid_loss * 0.6), Money(mid_loss), pop,
            sizing_context=SizingContext.from_strategy_config(self.CONFIG),
            worst_case_max_loss=Money(natural),
        )
        assert max_loss_warning(n * natural, self.CONFIG.max_loss_warn_threshold, n) is None


class TestNaturalPrice:
    def test_spread_metrics_price_the_package_at_natural(self):
        # Sell the 95P (1.80 x 2.20), buy the 90P (0.80 x 1.20), $5 wide.
        m = calculate_spread_metrics(
            quote(1.80, 2.20, -0.25), quote(0.80, 1.20, -0.15),
            Strike(Decimal("95")), Strike(Decimal("90")),
        )
        assert m["net_credit"].amount == Decimal("1.00")
        assert m["natural_credit"].amount == Decimal("0.60")         # 1.80 - 1.20
        assert float(m["max_loss"].amount) == pytest.approx(400.0)
        assert float(m["natural_max_loss"].amount) == pytest.approx(440.0)

    def test_a_natural_debit_raises_max_loss_past_the_width(self):
        m = calculate_spread_metrics(
            quote(0.00, 0.40, -0.25), quote(0.00, 0.30, -0.15),
            Strike(Decimal("95")), Strike(Decimal("90")),
        )
        assert m["natural_credit"].amount == Decimal("-0.30")
        assert float(m["natural_max_loss"].amount) == pytest.approx(530.0)

    def test_the_built_spread_carries_it_for_the_whole_position(self):
        chain = OptionChain(
            ticker="T", expiration=date(2026, 10, 16), stock_price=Money(100.0), calls={},
            puts={
                Strike(Decimal("90")): quote(0.80, 1.20, -0.20, iv=60.0),
                Strike(Decimal("95")): quote(1.80, 2.20, -0.25, iv=60.0),
                Strike(Decimal("100")): quote(4.50, 5.00, -0.50, iv=60.0),
            },
        )
        vrp = VRPResult(ticker="T", expiration=chain.expiration, implied_move_pct=Percentage(5.0),
                        historical_mean_move_pct=Percentage(2.5), vrp_ratio=2.0, edge_score=1.0,
                        recommendation=Recommendation.EXCELLENT)
        s = build_vertical_spread(
            StrategyConfig(), LiquidityScorer(), "T", chain, vrp,
            OptionType.PUT, StrategyType.BULL_PUT_SPREAD, True, SizingContext(),
        )
        assert s is not None
        assert s.natural_credit.amount == Decimal("0.60")
        assert float(s.natural_max_loss.amount) == pytest.approx(440.0 * s.contracts)
        assert s.worst_case_max_loss == s.natural_max_loss


class TestCalendarPilot:
    FRONT, BACK = date(2026, 10, 16), date(2026, 11, 20)

    def _chain(self, expiration, mid):
        strikes = [Strike(p) for p in (950.0, 1000.0, 1050.0)]
        quotes = {s: quote(mid - 0.05, mid + 0.05, oi=500, iv=50.0) for s in strikes}
        return OptionChain(ticker="T", expiration=expiration, stock_price=Money(1000.0),
                           calls=quotes, puts=dict(quotes))

    def test_the_debit_is_held_to_the_threshold(self):
        # Mid debit $50.00, natural $50.10: $5,010 a contract. 10 would be $50,100.
        cal = build_calendar_spread(
            self._chain(self.FRONT, 50.0), self._chain(self.BACK, 100.0), LiquidityScorer(),
            DirectionalBias.NEUTRAL, SizingContext(trr_level="HIGH"),
        ).value
        assert cal.contracts == 9
        assert float(cal.natural_credit.amount) == pytest.approx(-50.10)
        assert float(cal.natural_max_loss.amount) == pytest.approx(5010.0 * 9)
        assert max_loss_warning(float(cal.worst_case_max_loss.amount), THRESHOLD, 9) is None

    def test_a_small_debit_keeps_the_pilot_cap(self):
        cal = build_calendar_spread(
            self._chain(self.FRONT, 3.0), self._chain(self.BACK, 5.0), LiquidityScorer(),
            DirectionalBias.NEUTRAL, SizingContext(trr_level="HIGH"),
        ).value
        assert cal.contracts == 10
