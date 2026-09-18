"""Tests for scoring.rationale module functions."""
from datetime import date

from src.domain.enums import StrategyType
from src.domain.scoring.rationale import (
    generate_recommendation_rationale,
    generate_strategy_rationale,
)
from src.domain.scoring.weights import ScoringWeights
from src.domain.types import Money, Percentage, Strategy, VRPResult
from src.domain.vrp_tier import (
    VRP_EXCELLENT_FROM,
    VRP_GOOD_FROM,
    VRP_MARGINAL_FROM,
    vrp_tier,
)


def make_vrp(implied_move_pct: float = 7.0, vrp_ratio: float = 1.8) -> VRPResult:
    return VRPResult(
        ticker="AAPL",
        expiration=date(2026, 7, 18),
        implied_move_pct=Percentage(implied_move_pct),
        historical_mean_move_pct=Percentage(implied_move_pct / vrp_ratio),
        vrp_ratio=vrp_ratio,
        edge_score=0.8,
        recommendation=vrp_tier(vrp_ratio),
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


class TestGenerateStrategyRationale:
    def setup_method(self):
        self.weights = ScoringWeights()

    def test_excellent_vrp_mentioned(self):
        s = make_strategy()
        vrp = make_vrp(vrp_ratio=2.5)
        rationale = generate_strategy_rationale(s, vrp, self.weights)
        assert "VRP" in rationale

    def test_vrp_is_named_by_its_tier(self):
        # The text used to call VRP "excellent" from 2.0 and "strong" from 1.5,
        # so a ratio above VRP_EXCELLENT_FROM -- EXCELLENT, sized as EXCELLENT
        # -- read "Strong VRP".
        s = make_strategy()
        assert "Excellent VRP edge" in generate_strategy_rationale(
            s, make_vrp(vrp_ratio=VRP_EXCELLENT_FROM + 0.2), self.weights)
        assert "Good VRP" in generate_strategy_rationale(
            s, make_vrp(vrp_ratio=VRP_GOOD_FROM + 0.05), self.weights)
        marginal = generate_strategy_rationale(
            s, make_vrp(vrp_ratio=(VRP_GOOD_FROM + VRP_MARGINAL_FROM) / 2), self.weights)
        assert "Excellent VRP" not in marginal and "Good VRP" not in marginal

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

    def test_excellent_vrp_cited_from_the_tier(self):
        s = make_strategy()
        excellent_ratio = VRP_EXCELLENT_FROM + 0.2
        assert f"excellent VRP ({excellent_ratio:.2f}x)" in generate_recommendation_rationale(
            [s], make_vrp(vrp_ratio=excellent_ratio), self.weights)
        assert "excellent VRP" not in generate_recommendation_rationale(
            [s], make_vrp(vrp_ratio=VRP_EXCELLENT_FROM - 0.01), self.weights)

    def test_bull_put_spread_named(self):
        s = make_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        result = generate_recommendation_rationale([s], make_vrp(), self.weights)
        assert "Bull Put Spread" in result

    def test_bear_call_spread_named(self):
        s = make_strategy(strategy_type=StrategyType.BEAR_CALL_SPREAD)
        result = generate_recommendation_rationale([s], make_vrp(), self.weights)
        assert "Bear Call Spread" in result

    def test_contracts_mentioned(self):
        s = make_strategy()
        result = generate_recommendation_rationale([s], make_vrp(), self.weights)
        assert "50 contracts" in result

    def test_returns_nonempty_string(self):
        s = make_strategy()
        result = generate_recommendation_rationale([s], make_vrp(), self.weights)
        assert isinstance(result, str) and len(result) > 0

    def test_coin_flip_disclosed(self):
        # Direction scores nothing, so a pick between two spreads is never a
        # directional call, whatever the bias. Until 2026-09-14 this disclosure
        # was NEUTRAL-only, because the scorer gave the bias side up to +8.
        winner = make_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        winner.overall_score = 70.0
        runner_up = make_strategy(strategy_type=StrategyType.BEAR_CALL_SPREAD)
        runner_up.overall_score = 68.0
        result = generate_recommendation_rationale(
            [winner, runner_up], make_vrp(), self.weights
        )
        assert "no directional edge" in result
        assert "coin flip" in result
        assert "Bear Call Spread" in result

    def test_wide_gap_still_disclosed(self):
        # A wide score gap is NOT directional confidence -- measured on the
        # live scorer, put skew alone hands BULL_PUT up to +9.3 pts on an
        # identical-delta chain. So the disclosure must fire here too, not
        # just on near-ties.
        winner = make_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        winner.overall_score = 90.3
        runner_up = make_strategy(strategy_type=StrategyType.BEAR_CALL_SPREAD)
        runner_up.overall_score = 81.0
        result = generate_recommendation_rationale(
            [winner, runner_up], make_vrp(), self.weights
        )
        assert "no directional edge" in result
        assert "premium/liquidity" in result
        assert "richer put skew" in result

    def test_wide_gap_to_a_call_spread_is_not_put_skew(self):
        # Put skew can only favour the put side. Live chain 2026-09-14: MU's
        # call spread won by 14.7 pts on liquidity (EXCELLENT vs REJECT) and
        # the text credited put skew.
        winner = make_strategy(strategy_type=StrategyType.BEAR_CALL_SPREAD)
        winner.overall_score = 84.4
        runner_up = make_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        runner_up.overall_score = 69.7
        result = generate_recommendation_rationale(
            [winner, runner_up], make_vrp(), self.weights
        )
        assert "no directional edge" in result
        assert "premium/liquidity" in result
        assert "put skew" not in result

    def test_single_candidate_no_disclosure(self):
        # Nothing to compare against -> no misleading "picked over X" claim.
        only = make_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        only.overall_score = 70.0
        result = generate_recommendation_rationale(
            [only], make_vrp(), self.weights
        )
        assert "no directional edge" not in result
