"""Tests for scoring.factors pure factor functions."""
from datetime import date

from src.domain.enums import StrategyType
from src.domain.scoring.factors import (
    calculate_greeks_score,
    calculate_kelly_edge_score,
    calculate_liquidity_score,
)
from src.domain.scoring.weights import ScoringWeights
from src.domain.types import Money, Strategy


def make_strategy(
    strategy_type=StrategyType.BULL_PUT_SPREAD,
    breakeven=None,
    stock_price=100.0,
    liquidity_tier=None,
    position_theta=None,
    position_vega=None,
    pop=0.80,
    rr=0.25,
    contracts=50,
) -> Strategy:
    return Strategy(
        ticker="AAPL",
        strategy_type=strategy_type,
        expiration=date(2026, 7, 18),
        legs=[],
        stock_price=Money(stock_price),
        net_credit=Money(1.00),
        max_profit=Money(1.00),
        max_loss=Money(4.00),
        breakeven=breakeven if breakeven is not None else [Money(96.0)],
        probability_of_profit=pop,
        reward_risk_ratio=rr,
        contracts=contracts,
        capital_required=Money(20000),
        commission_per_contract=0.65,
        total_commission=Money(10.0),
        net_profit_after_fees=Money(90.0),
        profitability_score=0.0,
        risk_score=0.0,
        overall_score=0.0,
        rationale="",
        liquidity_tier=liquidity_tier,
        position_theta=position_theta,
        position_vega=position_vega,
    )


class TestCalculateGreeksScore:
    def setup_method(self):
        self.weights = ScoringWeights()

    def test_no_greeks_returns_zero(self):
        s = make_strategy(position_theta=None, position_vega=None)
        assert calculate_greeks_score(s, self.weights) == 0.0

    def test_positive_theta_scores_positive(self):
        s = make_strategy(position_theta=50.0, position_vega=None)
        assert calculate_greeks_score(s, self.weights) > 0

    def test_negative_theta_scores_zero(self):
        s = make_strategy(position_theta=-20.0, position_vega=None)
        assert calculate_greeks_score(s, self.weights) == 0.0

    def test_negative_vega_scores_positive(self):
        s = make_strategy(position_theta=None, position_vega=-50.0)
        assert calculate_greeks_score(s, self.weights) > 0

    def test_high_positive_vega_scores_zero(self):
        s = make_strategy(position_theta=None, position_vega=50.0)
        assert calculate_greeks_score(s, self.weights) == 0.0

    def test_near_zero_positive_vega_scores_partial(self):
        s = make_strategy(position_theta=None, position_vega=5.0)
        score = calculate_greeks_score(s, self.weights)
        assert 0 < score < self.weights.greeks_weight / 2

    def test_score_bounded(self):
        s = make_strategy(position_theta=1000.0, position_vega=-1000.0)
        score = calculate_greeks_score(s, self.weights)
        assert 0 <= score <= self.weights.greeks_weight


class TestCalculateLiquidityScore:
    def setup_method(self):
        self.weights = ScoringWeights()
        self.full = self.weights.liquidity_weight

    def test_none_tier_returns_full(self):
        s = make_strategy(liquidity_tier=None)
        assert calculate_liquidity_score(s, self.weights) == self.full

    def test_excellent_returns_full(self):
        s = make_strategy(liquidity_tier="EXCELLENT")
        assert calculate_liquidity_score(s, self.weights) == self.full

    def test_good_returns_full(self):
        s = make_strategy(liquidity_tier="GOOD")
        assert calculate_liquidity_score(s, self.weights) == self.full

    def test_warning_returns_half(self):
        s = make_strategy(liquidity_tier="WARNING")
        assert calculate_liquidity_score(s, self.weights) == self.full * 0.5

    def test_reject_returns_zero(self):
        s = make_strategy(liquidity_tier="REJECT")
        assert calculate_liquidity_score(s, self.weights) == 0.0

    def test_case_insensitive(self):
        s = make_strategy(liquidity_tier="warning")
        assert calculate_liquidity_score(s, self.weights) == self.full * 0.5


class TestCalculateKellyEdgeScore:
    def test_negative_edge_returns_zero(self):
        # IC: 59.5% POP, 0.38 R/R → edge = 0.595×0.38 - 0.405 = -0.179 → 0
        score = calculate_kelly_edge_score(pop=0.595, rr=0.38, weight=13.0)
        assert score == 0.0

    def test_positive_edge_returns_positive(self):
        # BPS: 84.6% POP, 0.21 R/R → edge > 0
        score = calculate_kelly_edge_score(pop=0.846, rr=0.21, weight=13.0)
        assert score > 0

    def test_break_even_returns_zero(self):
        # pop=0.5, rr=1.0 → edge = 0.5×1.0 - 0.5 = 0.0
        score = calculate_kelly_edge_score(pop=0.5, rr=1.0, weight=13.0)
        assert score == 0.0

    def test_capped_at_weight(self):
        score = calculate_kelly_edge_score(pop=0.99, rr=10.0, weight=13.0)
        assert score <= 13.0

    def test_weight_scales_result(self):
        score_half = calculate_kelly_edge_score(pop=0.80, rr=0.40, weight=6.5)
        score_full = calculate_kelly_edge_score(pop=0.80, rr=0.40, weight=13.0)
        assert abs(score_full - 2 * score_half) < 0.001

