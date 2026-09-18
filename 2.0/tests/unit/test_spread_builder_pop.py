"""
build_vertical_spread threads the chain's stock price and expiration into POP,
so the POP on the Strategy -- and the POP handed to Kelly sizing -- is the
probability of finishing beyond the breakeven, not 1 - |short delta|.
"""
from datetime import date, datetime
from decimal import Decimal
from math import log, sqrt
from statistics import NormalDist
from unittest.mock import MagicMock, patch

import pytest

from src.application.services.strategy import spread_builder
from src.application.services.strategy.spread_builder import build_vertical_spread
from src.config.config import StrategyConfig
from src.domain.enums import OptionType, Recommendation, StrategyType
from src.domain.types import (
    MARKET_TZ,
    Money,
    OptionChain,
    OptionQuote,
    Percentage,
    Strike,
    VRPResult,
)

_N = NormalDist()
S = 100.0
SIGMA = 1.0
EXPIRATION = date(2026, 7, 17)                                  # Friday
AS_OF = datetime(2026, 7, 10, 16, 0, tzinfo=MARKET_TZ)          # exactly 7 days before the close
YEARS = 7 / 365


def bs(kind: str, strike: float, years: float = YEARS):
    v = SIGMA * sqrt(years)
    d1 = (log(S / strike) + v * v / 2) / v
    d2 = d1 - v
    if kind == "call":
        return S * _N.cdf(d1) - strike * _N.cdf(d2), _N.cdf(d1)
    return strike * _N.cdf(-d2) - S * _N.cdf(-d1), _N.cdf(d1) - 1.0


def analytic_pop(kind: str, breakeven: float, years: float = YEARS) -> float:
    v = SIGMA * sqrt(years)
    d2 = (log(S / breakeven) - v * v / 2) / v
    return _N.cdf(d2) if kind == "put" else _N.cdf(-d2)


def bs_chain() -> OptionChain:
    puts, calls = {}, {}
    for k in range(70, 131):
        for kind, side in (("put", puts), ("call", calls)):
            price, delta = bs(kind, float(k))
            if price < 0.10:
                continue
            side[Strike(Decimal(k))] = OptionQuote(
                bid=Money(round(price - 0.05, 4)), ask=Money(round(price + 0.05, 4)),
                implied_volatility=Percentage(SIGMA * 100), open_interest=500, volume=50,
                delta=delta, gamma=0.01, theta=-0.05, vega=0.10,
            )
    return OptionChain(ticker="TEST", expiration=EXPIRATION, stock_price=Money(S),
                       calls=calls, puts=puts)


def make_vrp() -> VRPResult:
    return VRPResult(
        ticker="TEST", expiration=EXPIRATION, implied_move_pct=Percentage(5.0),
        historical_mean_move_pct=Percentage(2.5), vrp_ratio=2.0, edge_score=0.5,
        recommendation=Recommendation.EXCELLENT,
    )


@pytest.fixture
def liquidity_scorer():
    scorer = MagicMock()
    scorer.classify_option_tier.return_value = "EXCELLENT"
    scorer.calculate_spread_pct.return_value = 5.0
    return scorer


def build(kind, liquidity_scorer, as_of=AS_OF, config=None):
    is_put = kind == "put"
    return build_vertical_spread(
        config or StrategyConfig(), liquidity_scorer, "TEST", bs_chain(), make_vrp(),
        OptionType.PUT if is_put else OptionType.CALL,
        StrategyType.BULL_PUT_SPREAD if is_put else StrategyType.BEAR_CALL_SPREAD,
        below=is_put, as_of=as_of,
    )


@pytest.mark.parametrize("kind", ["put", "call"])
def test_strategy_pop_is_probability_beyond_breakeven(kind, liquidity_scorer):
    strategy = build(kind, liquidity_scorer)
    assert strategy is not None
    breakeven = float(strategy.breakeven[0].amount)
    assert strategy.probability_of_profit == pytest.approx(analytic_pop(kind, breakeven), abs=1e-6)


def test_kelly_sizing_receives_the_breakeven_pop(liquidity_scorer):
    with patch.object(spread_builder, "calculate_contracts_kelly", return_value=7) as kelly:
        strategy = build("call", liquidity_scorer)
    assert strategy.contracts == 7
    passed = kelly.call_args.kwargs["probability_of_profit"]
    assert passed == strategy.probability_of_profit
    assert passed == pytest.approx(analytic_pop("call", float(strategy.breakeven[0].amount)), abs=1e-6)


def test_as_of_sets_time_to_expiry(liquidity_scorer):
    # Same quotes, same strikes, same credit: more time left means a wider
    # distribution and a lower put-spread POP.
    near = build("put", liquidity_scorer, as_of=AS_OF)
    far = build("put", liquidity_scorer, as_of=datetime(2026, 6, 19, 16, 0, tzinfo=MARKET_TZ))
    assert near.breakeven[0].amount == far.breakeven[0].amount
    assert far.probability_of_profit < near.probability_of_profit


def test_default_as_of_is_now(liquidity_scorer):
    fixed_now = datetime(2026, 7, 10, 16, 0, tzinfo=MARKET_TZ)
    with patch.object(spread_builder, "now_et", return_value=fixed_now):
        strategy = build("put", liquidity_scorer, as_of=None)
    assert strategy.probability_of_profit == pytest.approx(
        analytic_pop("put", float(strategy.breakeven[0].amount)), abs=1e-6)
