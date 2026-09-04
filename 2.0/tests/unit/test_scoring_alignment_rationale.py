"""Tests for scoring.alignment and scoring.rationale module functions."""
from datetime import date

from src.config.config import ScoringWeights
from src.domain.types import Strategy, VRPResult, Money, Percentage
from src.domain.enums import StrategyType, DirectionalBias, Recommendation
from src.domain.scoring.alignment import apply_directional_alignment
from src.domain.scoring.rationale import generate_strategy_rationale, generate_recommendation_rationale


def make_vrp(implied_move_pct: float = 7.0, vrp_ratio: float = 1.8) -> VRPResult:
    return VRPResult(
        ticker="AAPL",
        expiration=date(2026, 7, 18),
        implied_move_pct=Percentage(implied_move_pct),
        historical_mean_move_pct=Percentage(implied_move_pct / vrp_ratio),
        vrp_ratio=vrp_ratio,
        edge_score=0.8,
        recommendation=Recommendation.EXCELLENT,
    )


def make_strategy(
    strategy_type=StrategyType.BULL_PUT_SPREAD,
    liquidity_tier=None,
    position_theta=None,
    position_vega=None,
) -> Strategy:
    return Strategy(
        ticker="AAPL",
        strategy_type=strategy_type,
        expiration=date(2026, 7, 18),
        legs=[],
        stock_price=Money(100.0),
        net_credit=Money(1.00),
        max_profit=Money(1.00),
        max_loss=Money(4.00),
        breakeven=[Money(96.0)],
        probability_of_profit=0.80,
        reward_risk_ratio=0.25,
        contracts=50,
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


class TestApplyDirectionalAlignment:
    def test_strong_bearish_aligned(self):
        s = make_strategy(strategy_type=StrategyType.BEAR_CALL_SPREAD)
        assert apply_directional_alignment(50.0, s, DirectionalBias.STRONG_BEARISH) == 58.0

    def test_moderate_bearish_aligned(self):
        s = make_strategy(strategy_type=StrategyType.BEAR_CALL_SPREAD)
        assert apply_directional_alignment(50.0, s, DirectionalBias.BEARISH) == 55.0

    def test_weak_bearish_aligned(self):
        s = make_strategy(strategy_type=StrategyType.BEAR_CALL_SPREAD)
        assert apply_directional_alignment(50.0, s, DirectionalBias.WEAK_BEARISH) == 53.0

    def test_counter_trend_penalty(self):
        s = make_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        assert apply_directional_alignment(50.0, s, DirectionalBias.BEARISH) == 47.0

    def test_strong_bullish_aligned(self):
        s = make_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        assert apply_directional_alignment(50.0, s, DirectionalBias.STRONG_BULLISH) == 58.0

    def test_neutral_no_adjustment(self):
        s = make_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        assert apply_directional_alignment(50.0, s, DirectionalBias.NEUTRAL) == 50.0

    def test_score_capped_at_100(self):
        s = make_strategy(strategy_type=StrategyType.BEAR_CALL_SPREAD)
        assert apply_directional_alignment(95.0, s, DirectionalBias.STRONG_BEARISH) == 100.0

    def test_score_floored_at_0(self):
        s = make_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        assert apply_directional_alignment(1.0, s, DirectionalBias.BEARISH) == 0.0


class TestGenerateStrategyRationale:
    def setup_method(self):
        self.weights = ScoringWeights()

    def test_excellent_vrp_mentioned(self):
        s = make_strategy()
        vrp = make_vrp(vrp_ratio=2.5)
        rationale = generate_strategy_rationale(s, vrp, self.weights)
        assert "VRP" in rationale

    def test_warning_liquidity_flagged(self):
        s = make_strategy(liquidity_tier="WARNING")
        rationale = generate_strategy_rationale(s, make_vrp(), self.weights)
        assert "LOW LIQUIDITY" in rationale

    def test_reject_liquidity_flagged(self):
        s = make_strategy(liquidity_tier="REJECT")
        rationale = generate_strategy_rationale(s, make_vrp(), self.weights)
        assert "VERY LOW LIQUIDITY" in rationale

    def test_spread_exit_rule_mentioned(self):
        s = make_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        rationale = generate_strategy_rationale(s, make_vrp(), self.weights)
        assert "EXIT next trading day" in rationale

    def test_returns_nonempty_string(self):
        s = make_strategy()
        result = generate_strategy_rationale(s, make_vrp(), self.weights)
        assert isinstance(result, str) and len(result) > 0


class TestGenerateRecommendationRationale:
    def setup_method(self):
        self.weights = ScoringWeights()

    def test_bull_put_spread_named(self):
        s = make_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        result = generate_recommendation_rationale(s, make_vrp(), DirectionalBias.NEUTRAL, self.weights)
        assert "Bull Put Spread" in result

    def test_bear_call_spread_named(self):
        s = make_strategy(strategy_type=StrategyType.BEAR_CALL_SPREAD)
        result = generate_recommendation_rationale(s, make_vrp(), DirectionalBias.NEUTRAL, self.weights)
        assert "Bear Call Spread" in result

    def test_contracts_mentioned(self):
        s = make_strategy()
        result = generate_recommendation_rationale(s, make_vrp(), DirectionalBias.NEUTRAL, self.weights)
        assert "50 contracts" in result

    def test_returns_nonempty_string(self):
        s = make_strategy()
        result = generate_recommendation_rationale(s, make_vrp(), DirectionalBias.NEUTRAL, self.weights)
        assert isinstance(result, str) and len(result) > 0
