"""
Unit tests for StrategyScorer._calculate_liquidity_score, which previously
had zero direct test coverage (22% of total strategy score weight, added
Nov 2025 after a liquidity-driven loss).
"""

from unittest.mock import MagicMock

from src.domain.enums import StrategyType
from src.domain.scoring.strategy_scorer import StrategyScorer
from src.domain.scoring.weights import ScoringWeights


def make_mock_strategy(liquidity_tier=None, strategy_type=StrategyType.BULL_PUT_SPREAD):
    strategy = MagicMock()
    strategy.liquidity_tier = liquidity_tier
    strategy.strategy_type = strategy_type
    return strategy


class TestCalculateLiquidityScore:
    """4-tier liquidity scoring: EXCELLENT/GOOD=full, WARNING=50%, REJECT=0."""

    def setup_method(self):
        self.scorer = StrategyScorer(ScoringWeights())
        self.full_weight = ScoringWeights().liquidity_weight  # 22.0

    def test_excellent_tier_gets_full_weight(self):
        strategy = make_mock_strategy(liquidity_tier="EXCELLENT")
        assert self.scorer._calculate_liquidity_score(strategy) == self.full_weight

    def test_good_tier_gets_full_weight(self):
        # GOOD = full size per CLAUDE.md trading rules, so it scores the
        # same as EXCELLENT (not partial credit).
        strategy = make_mock_strategy(liquidity_tier="GOOD")
        assert self.scorer._calculate_liquidity_score(strategy) == self.full_weight

    def test_warning_tier_gets_half_weight(self):
        strategy = make_mock_strategy(liquidity_tier="WARNING")
        assert self.scorer._calculate_liquidity_score(strategy) == self.full_weight * 0.5

    def test_reject_tier_gets_zero(self):
        strategy = make_mock_strategy(liquidity_tier="REJECT")
        assert self.scorer._calculate_liquidity_score(strategy) == 0.0

    def test_tier_is_case_insensitive(self):
        strategy = make_mock_strategy(liquidity_tier="warning")
        assert self.scorer._calculate_liquidity_score(strategy) == self.full_weight * 0.5

    def test_missing_tier_defaults_to_full_weight(self):
        # No tier info -> assume EXCELLENT for backward compatibility.
        strategy = make_mock_strategy(liquidity_tier=None)
        assert self.scorer._calculate_liquidity_score(strategy) == self.full_weight

    def test_unknown_tier_string_defaults_to_full_weight(self):
        strategy = make_mock_strategy(liquidity_tier="SOMETHING_NEW")
        assert self.scorer._calculate_liquidity_score(strategy) == self.full_weight
