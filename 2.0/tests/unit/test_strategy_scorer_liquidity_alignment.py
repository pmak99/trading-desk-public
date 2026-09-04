"""
Unit tests for StrategyScorer methods that previously had zero direct test
coverage: _calculate_liquidity_score (22% of total strategy score weight,
added after a liquidity-driven loss) and _apply_directional_alignment (the
skew-driven bonus/penalty backing the directional bias rules).
"""

from unittest.mock import MagicMock

from src.domain.scoring.strategy_scorer import StrategyScorer
from src.domain.enums import StrategyType, DirectionalBias
from src.config.config import ScoringWeights


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


class TestApplyDirectionalAlignment:
    """Skew-alignment bonus/penalty per CLAUDE.md directional bias rules."""

    def setup_method(self):
        self.scorer = StrategyScorer(ScoringWeights())

    def test_strong_bearish_bias_aligned_with_bear_call_spread_gets_plus_8(self):
        strategy = make_mock_strategy(strategy_type=StrategyType.BEAR_CALL_SPREAD)
        score = self.scorer._apply_directional_alignment(50.0, strategy, DirectionalBias.STRONG_BEARISH)
        assert score == 58.0

    def test_bearish_bias_aligned_with_bear_call_spread_gets_plus_5(self):
        strategy = make_mock_strategy(strategy_type=StrategyType.BEAR_CALL_SPREAD)
        score = self.scorer._apply_directional_alignment(50.0, strategy, DirectionalBias.BEARISH)
        assert score == 55.0

    def test_weak_bearish_bias_aligned_with_bear_call_spread_gets_plus_3(self):
        strategy = make_mock_strategy(strategy_type=StrategyType.BEAR_CALL_SPREAD)
        score = self.scorer._apply_directional_alignment(50.0, strategy, DirectionalBias.WEAK_BEARISH)
        assert score == 53.0

    def test_bearish_bias_against_bull_put_spread_gets_minus_3(self):
        strategy = make_mock_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        score = self.scorer._apply_directional_alignment(50.0, strategy, DirectionalBias.BEARISH)
        assert score == 47.0

    def test_strong_bullish_bias_aligned_with_bull_put_spread_gets_plus_8(self):
        strategy = make_mock_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        score = self.scorer._apply_directional_alignment(50.0, strategy, DirectionalBias.STRONG_BULLISH)
        assert score == 58.0

    def test_bullish_bias_against_bear_call_spread_gets_minus_3(self):
        strategy = make_mock_strategy(strategy_type=StrategyType.BEAR_CALL_SPREAD)
        score = self.scorer._apply_directional_alignment(50.0, strategy, DirectionalBias.BULLISH)
        assert score == 47.0

    def test_neutral_bias_applies_no_adjustment(self):
        for strategy_type in (StrategyType.BULL_PUT_SPREAD, StrategyType.BEAR_CALL_SPREAD):
            strategy = make_mock_strategy(strategy_type=strategy_type)
            score = self.scorer._apply_directional_alignment(50.0, strategy, DirectionalBias.NEUTRAL)
            assert score == 50.0

    def test_score_clamped_to_zero_floor(self):
        strategy = make_mock_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        score = self.scorer._apply_directional_alignment(1.0, strategy, DirectionalBias.BEARISH)
        assert score == 0.0

    def test_score_clamped_to_100_ceiling(self):
        strategy = make_mock_strategy(strategy_type=StrategyType.BEAR_CALL_SPREAD)
        score = self.scorer._apply_directional_alignment(95.0, strategy, DirectionalBias.STRONG_BEARISH)
        assert score == 100.0
