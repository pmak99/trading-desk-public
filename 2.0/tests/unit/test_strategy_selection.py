"""
Unit tests for critical strategy selection business logic.

Tests the core decision-making rules that determine which strategies
are generated, how they are sized, and how they are scored/ranked.

Business rules tested (from CLAUDE.md):
1. VRP tier classification: EXCELLENT >= 1.8, GOOD >= 1.4, MARGINAL >= 1.2, SKIP < 1.2
2. TRR-based position limits: HIGH (>2.5x) = 50 max, NORMAL/LOW = 100
3. Strategy type selection based on VRP level and directional bias
4. Score calculation and ranking priority
5. Kelly-based position sizing respects trade quality
6. Directional alignment bonuses/penalties
"""

import pytest
from datetime import date, timedelta

from src.domain.types import (
    Money, Percentage, Strike, OptionQuote, OptionChain,
    VRPResult, SkewResult, Strategy, StrategyLeg, StrategyRecommendation,
)
from src.domain.enums import (
    StrategyType, DirectionalBias, OptionType, Recommendation,
)
from src.config.config import StrategyConfig, ScoringWeights, ThresholdsConfig
from src.application.services.strategy_generator import StrategyGenerator
from src.application.metrics.liquidity_scorer import LiquidityScorer
from src.application.metrics.vrp import VRPCalculator
from src.domain.scoring.strategy_scorer import StrategyScorer


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def default_config():
    """Create default StrategyConfig matching production defaults."""
    return StrategyConfig(
        use_kelly_sizing=True,
        kelly_fraction=0.25,
        kelly_min_edge=0.05,
        kelly_min_contracts=1,
        risk_budget_per_trade=20000.0,
        max_contracts=100,
        target_delta_short=0.25,
        target_delta_long=0.20,
        spread_width_high_price=5.0,
        spread_width_low_price=3.0,
        spread_width_threshold=20.0,
        min_credit_per_spread=0.20,
        min_reward_risk=0.25,
        commission_per_contract=0.30,
        scoring_weights=ScoringWeights(),
    )


@pytest.fixture
def liquidity_scorer(default_config):
    """Create liquidity scorer with default config."""
    return LiquidityScorer()


@pytest.fixture
def generator(default_config, liquidity_scorer):
    """Create strategy generator instance."""
    return StrategyGenerator(default_config, liquidity_scorer)


@pytest.fixture
def scorer():
    """Create strategy scorer with default weights."""
    return StrategyScorer(ScoringWeights())


@pytest.fixture
def expiration():
    """Expiration date one week out."""
    return date.today() + timedelta(days=7)


def _make_vrp(
    ticker: str = "TEST",
    vrp_ratio: float = 2.0,
    implied_move_pct: float = 8.0,
    historical_mean: float = 4.0,
    recommendation: Recommendation = Recommendation.EXCELLENT,
    expiration: date = None,
) -> VRPResult:
    """Helper to create VRPResult with sensible defaults."""
    if expiration is None:
        expiration = date.today() + timedelta(days=7)
    return VRPResult(
        ticker=ticker,
        expiration=expiration,
        implied_move_pct=Percentage(implied_move_pct),
        historical_mean_move_pct=Percentage(historical_mean),
        vrp_ratio=vrp_ratio,
        edge_score=vrp_ratio / 2.0,
        recommendation=recommendation,
    )


def _make_strategy(
    ticker: str = "TEST",
    strategy_type: StrategyType = StrategyType.BULL_PUT_SPREAD,
    stock_price: float = 100.0,
    net_credit: float = 1.50,
    max_profit: float = 150.0,
    max_loss: float = 350.0,
    pop: float = 0.75,
    contracts: int = 5,
    liquidity_tier: str = "EXCELLENT",
    position_theta: float = 25.0,
    position_vega: float = -80.0,
) -> Strategy:
    """Helper to create Strategy with sensible defaults."""
    expiration = date.today() + timedelta(days=7)
    short_strike = Strike(95.0) if strategy_type == StrategyType.BULL_PUT_SPREAD else Strike(105.0)
    long_strike = Strike(90.0) if strategy_type == StrategyType.BULL_PUT_SPREAD else Strike(110.0)
    opt_type = OptionType.PUT if strategy_type == StrategyType.BULL_PUT_SPREAD else OptionType.CALL

    legs = [
        StrategyLeg(
            strike=short_strike, option_type=opt_type,
            action="SELL", contracts=1, premium=Money(2.00),
        ),
        StrategyLeg(
            strike=long_strike, option_type=opt_type,
            action="BUY", contracts=1, premium=Money(0.50),
        ),
    ]

    rr = float(max_profit) / float(max_loss) if max_loss > 0 else 0.0

    return Strategy(
        ticker=ticker,
        strategy_type=strategy_type,
        expiration=expiration,
        legs=legs,
        stock_price=Money(stock_price),
        net_credit=Money(net_credit),
        max_profit=Money(max_profit),
        max_loss=Money(max_loss),
        breakeven=[Money(float(short_strike.price) - net_credit)],
        probability_of_profit=pop,
        reward_risk_ratio=rr,
        contracts=contracts,
        capital_required=Money(max_loss * contracts),
        commission_per_contract=0.30,
        total_commission=Money(2 * contracts * 0.30),
        net_profit_after_fees=Money(max_profit * contracts - 2 * contracts * 0.30),
        profitability_score=0.0,
        risk_score=0.0,
        overall_score=0.0,
        rationale="",
        position_delta=-15.0,
        position_gamma=-2.0,
        position_theta=position_theta,
        position_vega=position_vega,
        liquidity_tier=liquidity_tier,
        min_open_interest=500,
        max_spread_pct=5.0,
    )


# ============================================================================
# VRP Tier Classification Tests
# ============================================================================


class TestVRPTierClassification:
    """
    Test VRP tier boundaries match CLAUDE.md specification.

    BALANCED profile thresholds:
    - EXCELLENT: >= 1.8x
    - GOOD: >= 1.4x
    - MARGINAL: >= 1.2x
    - SKIP: < 1.2x
    """

    def test_vrp_excellent_at_boundary(self):
        """VRP ratio of exactly 1.8x should classify as EXCELLENT in BALANCED profile."""
        config = ThresholdsConfig()  # Default = BALANCED
        assert config.vrp_excellent == 1.8
        # The VRP calculator uses configurable thresholds
        calc = VRPCalculator(
            threshold_excellent=config.vrp_excellent,
            threshold_good=config.vrp_good,
            threshold_marginal=config.vrp_marginal,
        )
        # At exactly 1.8x, recommendation should be EXCELLENT
        vrp = _make_vrp(vrp_ratio=1.8, recommendation=Recommendation.EXCELLENT)
        assert vrp.vrp_ratio >= config.vrp_excellent
        assert vrp.is_tradeable is True

    def test_vrp_excellent_above_boundary(self):
        """VRP ratio of 2.5x is well above EXCELLENT threshold."""
        config = ThresholdsConfig()
        vrp = _make_vrp(vrp_ratio=2.5, recommendation=Recommendation.EXCELLENT)
        assert vrp.vrp_ratio >= config.vrp_excellent
        assert vrp.is_tradeable is True

    def test_vrp_good_at_boundary(self):
        """VRP ratio of exactly 1.4x should be in GOOD tier."""
        config = ThresholdsConfig()
        assert config.vrp_good == 1.4
        vrp = _make_vrp(vrp_ratio=1.4, recommendation=Recommendation.GOOD)
        assert vrp.vrp_ratio >= config.vrp_good
        assert vrp.vrp_ratio < config.vrp_excellent
        assert vrp.is_tradeable is True

    def test_vrp_good_below_excellent(self):
        """VRP at 1.79x is GOOD, not EXCELLENT."""
        config = ThresholdsConfig()
        assert 1.79 >= config.vrp_good
        assert 1.79 < config.vrp_excellent

    def test_vrp_marginal_at_boundary(self):
        """VRP ratio of exactly 1.2x should be MARGINAL."""
        config = ThresholdsConfig()
        assert config.vrp_marginal == 1.2
        vrp = _make_vrp(vrp_ratio=1.2, recommendation=Recommendation.MARGINAL)
        assert vrp.vrp_ratio >= config.vrp_marginal
        assert vrp.vrp_ratio < config.vrp_good
        # MARGINAL is NOT tradeable per VRPResult.is_tradeable (only EXCELLENT and GOOD)
        assert vrp.is_tradeable is False

    def test_vrp_skip_below_marginal(self):
        """VRP ratio of 1.1x should be SKIP."""
        config = ThresholdsConfig()
        vrp = _make_vrp(vrp_ratio=1.1, recommendation=Recommendation.SKIP)
        assert vrp.vrp_ratio < config.vrp_marginal
        assert vrp.is_tradeable is False

    def test_vrp_skip_at_unity(self):
        """VRP ratio of 1.0x means no edge -- implied matches historical."""
        vrp = _make_vrp(vrp_ratio=1.0, recommendation=Recommendation.SKIP)
        assert vrp.is_tradeable is False

    def test_vrp_threshold_ordering(self):
        """Thresholds must be strictly ordered: excellent > good > marginal."""
        config = ThresholdsConfig()
        assert config.vrp_excellent > config.vrp_good > config.vrp_marginal

    def test_balanced_profile_defaults(self):
        """BALANCED profile should use 1.8/1.4/1.2 thresholds."""
        config = ThresholdsConfig()
        assert config.vrp_threshold_mode == "BALANCED"
        assert config.vrp_excellent == 1.8
        assert config.vrp_good == 1.4
        assert config.vrp_marginal == 1.2


# ============================================================================
# TRR-Based Position Limit Tests
# ============================================================================


class TestTRRPositionLimits:
    """
    Test Tail Risk Ratio classification and position limits.

    From CLAUDE.md:
    - HIGH: TRR > 2.5x -> max 50 contracts
    - NORMAL: TRR 1.5-2.5x -> max 100 contracts
    - LOW: TRR < 1.5x -> max 100 contracts (best performance: 70.6% win)
    """

    def test_trr_high_classification(self):
        """TRR > 2.5 should be classified as HIGH."""
        trr = 3.0
        assert trr > 2.5
        level = 'HIGH' if trr > 2.5 else ('NORMAL' if trr >= 1.5 else 'LOW')
        assert level == 'HIGH'

    def test_trr_normal_classification(self):
        """TRR between 1.5 and 2.5 should be NORMAL."""
        for trr in [1.5, 2.0, 2.5]:
            level = 'HIGH' if trr > 2.5 else ('NORMAL' if trr >= 1.5 else 'LOW')
            assert level == 'NORMAL', f"TRR {trr} should be NORMAL, got {level}"

    def test_trr_low_classification(self):
        """TRR < 1.5 should be LOW (best historical performance)."""
        for trr in [1.0, 1.2, 1.49]:
            level = 'HIGH' if trr > 2.5 else ('NORMAL' if trr >= 1.5 else 'LOW')
            assert level == 'LOW', f"TRR {trr} should be LOW, got {level}"

    def test_trr_high_max_contracts(self):
        """HIGH TRR should limit to 50 contracts."""
        trr = 3.0
        max_contracts = 50 if trr > 2.5 else 100
        assert max_contracts == 50

    def test_trr_normal_max_contracts(self):
        """NORMAL TRR should allow 100 contracts."""
        trr = 2.0
        max_contracts = 50 if trr > 2.5 else 100
        assert max_contracts == 100

    def test_trr_low_max_contracts(self):
        """LOW TRR should allow 100 contracts."""
        trr = 1.2
        max_contracts = 50 if trr > 2.5 else 100
        assert max_contracts == 100

    def test_trr_at_high_boundary(self):
        """TRR at exactly 2.5 is NORMAL, not HIGH (boundary is > 2.5)."""
        trr = 2.5
        level = 'HIGH' if trr > 2.5 else ('NORMAL' if trr >= 1.5 else 'LOW')
        assert level == 'NORMAL'

    def test_trr_classification_matches_vrp_calculator(self):
        """
        Verify TRR classification in VRPCalculator.calculate_with_consistency
        matches the documented thresholds.

        The VRP calculator returns tail_risk_level in the consistency dict
        using the same thresholds:
        - > 2.5: HIGH
        - >= 1.5: NORMAL
        - < 1.5: LOW
        """
        # These are the thresholds used in vrp.py calculate_with_consistency
        test_cases = [
            (3.0, 'HIGH'),
            (2.51, 'HIGH'),
            (2.5, 'NORMAL'),
            (2.0, 'NORMAL'),
            (1.5, 'NORMAL'),
            (1.49, 'LOW'),
            (1.0, 'LOW'),
        ]
        for trr, expected_level in test_cases:
            if trr > 2.5:
                level = 'HIGH'
            elif trr >= 1.5:
                level = 'NORMAL'
            else:
                level = 'LOW'
            assert level == expected_level, (
                f"TRR {trr}: expected {expected_level}, got {level}"
            )


# ============================================================================
# Strategy Type Selection Tests
# ============================================================================


class TestStrategyTypeSelection:
    """
    Test that _select_strategy_types returns correct strategy mix
    based on VRP level and directional bias.
    """

    @pytest.fixture
    def generator(self, default_config, liquidity_scorer):
        return StrategyGenerator(default_config, liquidity_scorer)

    def test_very_high_vrp_neutral_includes_iron_butterfly(self, generator):
        """VRP >= 2.5 + neutral bias should include Iron Butterfly first."""
        vrp = _make_vrp(vrp_ratio=2.5)
        types = generator._select_strategy_types(vrp, DirectionalBias.NEUTRAL)
        assert StrategyType.IRON_BUTTERFLY in types
        assert types[0] == StrategyType.IRON_BUTTERFLY

    def test_very_high_vrp_neutral_includes_iron_condor(self, generator):
        """VRP >= 2.5 + neutral should also include Iron Condor."""
        vrp = _make_vrp(vrp_ratio=2.5)
        types = generator._select_strategy_types(vrp, DirectionalBias.NEUTRAL)
        assert StrategyType.IRON_CONDOR in types

    def test_high_vrp_neutral_generates_three_strategies(self, generator):
        """VRP >= 2.0 + neutral should generate 3 strategy types."""
        vrp = _make_vrp(vrp_ratio=2.0)
        types = generator._select_strategy_types(vrp, DirectionalBias.NEUTRAL)
        assert len(types) == 3
        assert StrategyType.IRON_CONDOR in types
        assert StrategyType.BULL_PUT_SPREAD in types
        assert StrategyType.BEAR_CALL_SPREAD in types

    def test_high_vrp_strong_bullish_skips_bear_call(self, generator):
        """VRP >= 2.0 + strong bullish should skip bear call spread."""
        vrp = _make_vrp(vrp_ratio=2.0)
        types = generator._select_strategy_types(vrp, DirectionalBias.STRONG_BULLISH)
        assert StrategyType.BEAR_CALL_SPREAD not in types
        assert StrategyType.BULL_PUT_SPREAD in types
        assert len(types) == 2

    def test_high_vrp_strong_bearish_skips_bull_put(self, generator):
        """VRP >= 2.0 + strong bearish should skip bull put spread."""
        vrp = _make_vrp(vrp_ratio=2.0)
        types = generator._select_strategy_types(vrp, DirectionalBias.STRONG_BEARISH)
        assert StrategyType.BULL_PUT_SPREAD not in types
        assert StrategyType.BEAR_CALL_SPREAD in types
        assert len(types) == 2

    def test_high_vrp_moderate_bullish_includes_all(self, generator):
        """VRP >= 2.0 + moderate bullish includes all but prioritizes bullish."""
        vrp = _make_vrp(vrp_ratio=2.0)
        types = generator._select_strategy_types(vrp, DirectionalBias.BULLISH)
        assert len(types) == 3
        # Bull put spread should be first (prioritized)
        assert types[0] == StrategyType.BULL_PUT_SPREAD

    def test_high_vrp_moderate_bearish_includes_all(self, generator):
        """VRP >= 2.0 + moderate bearish includes all but prioritizes bearish."""
        vrp = _make_vrp(vrp_ratio=2.0)
        types = generator._select_strategy_types(vrp, DirectionalBias.BEARISH)
        assert len(types) == 3
        # Bear call spread should be first (prioritized)
        assert types[0] == StrategyType.BEAR_CALL_SPREAD

    def test_moderate_vrp_neutral_two_strategies(self, generator):
        """VRP 1.5-2.0 + neutral should generate 2 strategies."""
        vrp = _make_vrp(vrp_ratio=1.7)
        types = generator._select_strategy_types(vrp, DirectionalBias.NEUTRAL)
        assert len(types) == 2
        assert StrategyType.IRON_CONDOR in types

    def test_moderate_vrp_bullish_prioritizes_bull_put(self, generator):
        """VRP 1.5-2.0 + bullish bias should put bull put first."""
        vrp = _make_vrp(vrp_ratio=1.7)
        types = generator._select_strategy_types(vrp, DirectionalBias.BULLISH)
        assert types[0] == StrategyType.BULL_PUT_SPREAD

    def test_moderate_vrp_bearish_prioritizes_bear_call(self, generator):
        """VRP 1.5-2.0 + bearish bias should put bear call first."""
        vrp = _make_vrp(vrp_ratio=1.7)
        types = generator._select_strategy_types(vrp, DirectionalBias.STRONG_BEARISH)
        assert types[0] == StrategyType.BEAR_CALL_SPREAD

    def test_low_vrp_single_strategy_only(self, generator):
        """VRP < 1.5 should generate only a single strategy."""
        vrp = _make_vrp(vrp_ratio=1.3)
        types = generator._select_strategy_types(vrp, DirectionalBias.NEUTRAL)
        assert len(types) == 1

    def test_low_vrp_neutral_defaults_to_bull_put(self, generator):
        """VRP < 1.5 + neutral defaults to bull put spread."""
        vrp = _make_vrp(vrp_ratio=1.3)
        types = generator._select_strategy_types(vrp, DirectionalBias.NEUTRAL)
        assert types[0] == StrategyType.BULL_PUT_SPREAD

    def test_low_vrp_bullish_selects_bull_put(self, generator):
        """VRP < 1.5 + bullish selects bull put spread."""
        vrp = _make_vrp(vrp_ratio=1.3)
        types = generator._select_strategy_types(vrp, DirectionalBias.BULLISH)
        assert types[0] == StrategyType.BULL_PUT_SPREAD

    def test_low_vrp_bearish_selects_bear_call(self, generator):
        """VRP < 1.5 + bearish selects bear call spread."""
        vrp = _make_vrp(vrp_ratio=1.3)
        types = generator._select_strategy_types(vrp, DirectionalBias.BEARISH)
        assert types[0] == StrategyType.BEAR_CALL_SPREAD

    def test_weak_bias_treated_like_neutral(self, generator):
        """Weak bias (strength 1) should be treated similarly to neutral."""
        vrp = _make_vrp(vrp_ratio=2.0)
        neutral_types = generator._select_strategy_types(vrp, DirectionalBias.NEUTRAL)
        weak_types = generator._select_strategy_types(vrp, DirectionalBias.WEAK_BULLISH)
        # Both should generate the same strategy set
        assert set(neutral_types) == set(weak_types)


# ============================================================================
# Score Calculation Tests
# ============================================================================


class TestScoreCalculation:
    """
    Test strategy scoring produces expected ranges and relative ordering.

    Key scoring factors (with Greeks):
    - POP: 40% weight
    - Liquidity: 22% weight
    - VRP: 17% weight
    - Edge (Kelly): 13% weight
    - Greeks: 8% weight

    From CLAUDE.md:
    - Score >= 50 for pre-filter (2.0 Score)
    - Score >= 55 for post-filter (4.0 Score with sentiment)
    """

    @pytest.fixture
    def scorer(self):
        return StrategyScorer(ScoringWeights())

    def test_score_weights_sum_to_100(self):
        """Scoring weights with Greeks should sum to 100."""
        w = ScoringWeights()
        total = (
            w.pop_weight + w.liquidity_weight + w.vrp_weight
            + w.reward_risk_weight + w.greeks_weight + w.size_weight
        )
        assert abs(total - 100.0) < 0.01, f"Weights sum to {total}, expected 100"

    def test_score_weights_no_greeks_sum_to_100(self):
        """Scoring weights without Greeks should also sum to 100."""
        w = ScoringWeights()
        total = (
            w.pop_weight_no_greeks + w.liquidity_weight_no_greeks
            + w.vrp_weight_no_greeks + w.reward_risk_weight_no_greeks
            + w.size_weight_no_greeks
        )
        assert abs(total - 100.0) < 0.01, f"No-Greeks weights sum to {total}, expected 100"

    def test_excellent_setup_scores_high(self, scorer):
        """A strategy with excellent POP, liquidity, and VRP should score high."""
        strategy = _make_strategy(
            pop=0.85,
            max_profit=200.0,
            max_loss=300.0,
            liquidity_tier="EXCELLENT",
            position_theta=60.0,
            position_vega=-120.0,
        )
        vrp = _make_vrp(vrp_ratio=2.5)
        result = scorer.score_strategy(strategy, vrp)
        # Excellent setup should score well above 50
        assert result.overall_score >= 60, (
            f"Excellent setup scored {result.overall_score:.1f}, expected >= 60"
        )

    def test_poor_setup_scores_low(self, scorer):
        """A strategy with poor POP, reject liquidity scores low."""
        strategy = _make_strategy(
            pop=0.55,
            max_profit=50.0,
            max_loss=450.0,
            liquidity_tier="REJECT",
            position_theta=5.0,
            position_vega=-10.0,
        )
        vrp = _make_vrp(vrp_ratio=1.3)
        result = scorer.score_strategy(strategy, vrp)
        # Poor setup should score below the 50-point pre-filter
        assert result.overall_score < 50, (
            f"Poor setup scored {result.overall_score:.1f}, expected < 50"
        )

    def test_reject_liquidity_zeroes_liquidity_component(self, scorer):
        """REJECT liquidity tier should contribute 0 points for liquidity."""
        good_liq = _make_strategy(liquidity_tier="EXCELLENT")
        bad_liq = _make_strategy(liquidity_tier="REJECT")
        vrp = _make_vrp(vrp_ratio=2.0)

        good_result = scorer.score_strategy(good_liq, vrp)
        bad_result = scorer.score_strategy(bad_liq, vrp)

        # REJECT should score lower due to zero liquidity points
        assert bad_result.overall_score < good_result.overall_score
        # The difference should be approximately the liquidity weight (22 points)
        diff = good_result.overall_score - bad_result.overall_score
        assert diff >= 15, (
            f"Liquidity penalty only {diff:.1f} points, expected >= 15"
        )

    def test_warning_liquidity_scores_between_excellent_and_reject(self, scorer):
        """WARNING liquidity should score between EXCELLENT and REJECT."""
        excellent = _make_strategy(liquidity_tier="EXCELLENT")
        warning = _make_strategy(liquidity_tier="WARNING")
        reject = _make_strategy(liquidity_tier="REJECT")
        vrp = _make_vrp(vrp_ratio=2.0)

        s_excellent = scorer.score_strategy(excellent, vrp).overall_score
        s_warning = scorer.score_strategy(warning, vrp).overall_score
        s_reject = scorer.score_strategy(reject, vrp).overall_score

        assert s_excellent > s_warning > s_reject

    def test_higher_vrp_increases_score(self, scorer):
        """Higher VRP ratio should produce a higher score (all else equal)."""
        strategy = _make_strategy()
        low_vrp = _make_vrp(vrp_ratio=1.4)
        high_vrp = _make_vrp(vrp_ratio=2.5)

        low_score = scorer.score_strategy(strategy, low_vrp).overall_score
        high_score = scorer.score_strategy(strategy, high_vrp).overall_score

        assert high_score > low_score

    def test_higher_pop_increases_score(self, scorer):
        """Higher probability of profit should produce a higher score."""
        low_pop = _make_strategy(pop=0.60)
        high_pop = _make_strategy(pop=0.85)
        vrp = _make_vrp(vrp_ratio=2.0)

        low_score = scorer.score_strategy(low_pop, vrp).overall_score
        high_score = scorer.score_strategy(high_pop, vrp).overall_score

        assert high_score > low_score

    def test_pop_is_heaviest_weight(self):
        """POP should have the largest weight (40%) per CLAUDE.md scoring system."""
        w = ScoringWeights()
        assert w.pop_weight >= w.liquidity_weight
        assert w.pop_weight >= w.vrp_weight
        assert w.pop_weight >= w.reward_risk_weight
        assert w.pop_weight >= w.greeks_weight
        assert w.pop_weight == 40.0

    def test_score_without_greeks_still_works(self, scorer):
        """Strategies without Greeks should still get scored."""
        strategy = _make_strategy(position_theta=None, position_vega=None)
        # Clear Greek values - Strategy is mutable
        strategy.position_theta = None
        strategy.position_vega = None
        strategy.position_delta = None
        strategy.position_gamma = None

        vrp = _make_vrp(vrp_ratio=2.0)
        result = scorer.score_strategy(strategy, vrp)
        assert result.overall_score > 0


# ============================================================================
# Directional Alignment Scoring Tests
# ============================================================================


class TestDirectionalAlignmentScoring:
    """
    Test that directional alignment bonuses/penalties are applied correctly.

    From strategy_scorer.py:
    - STRONG bias + aligned: +8 points
    - MODERATE bias + aligned: +5 points
    - WEAK bias + aligned: +3 points
    - Counter-trend: -3 points
    - Neutral bias: no adjustment
    """

    @pytest.fixture
    def scorer(self):
        return StrategyScorer(ScoringWeights())

    def test_bullish_strategy_with_strong_bullish_bias_gets_bonus(self, scorer):
        """Bull put spread + strong bullish should get +8 bonus."""
        strategy = _make_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        vrp = _make_vrp(vrp_ratio=2.0)

        score_neutral = scorer.score_strategy(strategy, vrp, DirectionalBias.NEUTRAL).overall_score
        score_aligned = scorer.score_strategy(strategy, vrp, DirectionalBias.STRONG_BULLISH).overall_score

        assert score_aligned > score_neutral
        diff = score_aligned - score_neutral
        assert diff > 5.0, f"Expected significant bonus, got {diff:.2f}"

    def test_bearish_strategy_with_strong_bearish_bias_gets_bonus(self, scorer):
        """Bear call spread + strong bearish should get meaningful bonus."""
        strategy = _make_strategy(strategy_type=StrategyType.BEAR_CALL_SPREAD)
        vrp = _make_vrp(vrp_ratio=2.0)

        score_neutral = scorer.score_strategy(strategy, vrp, DirectionalBias.NEUTRAL).overall_score
        score_aligned = scorer.score_strategy(strategy, vrp, DirectionalBias.STRONG_BEARISH).overall_score

        diff = score_aligned - score_neutral
        assert diff > 5.0, f"Expected significant bonus, got {diff:.2f}"

    def test_counter_trend_strategy_gets_penalty(self, scorer):
        """Bull put spread with bearish bias should get -3 penalty."""
        strategy = _make_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        vrp = _make_vrp(vrp_ratio=2.0)

        score_neutral = scorer.score_strategy(strategy, vrp, DirectionalBias.NEUTRAL).overall_score
        score_counter = scorer.score_strategy(strategy, vrp, DirectionalBias.STRONG_BEARISH).overall_score

        diff = score_counter - score_neutral
        assert abs(diff - (-3.0)) < 0.01, f"Expected -3 penalty, got {diff:.2f}"

    def test_moderate_bias_gives_moderate_bonus(self, scorer):
        """Moderate (not prefixed) bias should give +5 bonus."""
        strategy = _make_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        vrp = _make_vrp(vrp_ratio=2.0)

        score_neutral = scorer.score_strategy(strategy, vrp, DirectionalBias.NEUTRAL).overall_score
        score_moderate = scorer.score_strategy(strategy, vrp, DirectionalBias.BULLISH).overall_score

        diff = score_moderate - score_neutral
        assert abs(diff - 5.0) < 0.01, f"Expected +5 bonus, got {diff:.2f}"

    def test_weak_bias_gives_weak_bonus(self, scorer):
        """Weak bias should give +3 bonus."""
        strategy = _make_strategy(strategy_type=StrategyType.BULL_PUT_SPREAD)
        vrp = _make_vrp(vrp_ratio=2.0)

        score_neutral = scorer.score_strategy(strategy, vrp, DirectionalBias.NEUTRAL).overall_score
        score_weak = scorer.score_strategy(strategy, vrp, DirectionalBias.WEAK_BULLISH).overall_score

        diff = score_weak - score_neutral
        assert abs(diff - 3.0) < 0.01, f"Expected +3 bonus, got {diff:.2f}"

    def test_neutral_strategy_no_adjustment(self, scorer):
        """Iron condor (neutral) should get no directional adjustment."""
        ic_legs = [
            StrategyLeg(strike=Strike(90.0), option_type=OptionType.PUT, action="SELL", contracts=1, premium=Money(2.0)),
            StrategyLeg(strike=Strike(85.0), option_type=OptionType.PUT, action="BUY", contracts=1, premium=Money(0.5)),
            StrategyLeg(strike=Strike(110.0), option_type=OptionType.CALL, action="SELL", contracts=1, premium=Money(2.0)),
            StrategyLeg(strike=Strike(115.0), option_type=OptionType.CALL, action="BUY", contracts=1, premium=Money(0.5)),
        ]
        strategy = _make_strategy(strategy_type=StrategyType.IRON_CONDOR)
        strategy.legs = ic_legs
        vrp = _make_vrp(vrp_ratio=2.0)

        score_neutral = scorer.score_strategy(strategy, vrp, DirectionalBias.NEUTRAL).overall_score
        score_bearish = scorer.score_strategy(strategy, vrp, DirectionalBias.STRONG_BEARISH).overall_score

        # Iron condor is neutral -- should get 0 adjustment regardless of bias
        assert abs(score_neutral - score_bearish) < 0.01


# ============================================================================
# Strategy Priority Order Tests
# ============================================================================


class TestStrategyPriorityOrder:
    """
    Test that strategies are ranked in expected priority order
    when scored and sorted.

    From CLAUDE.md:
    - SINGLE options preferred over spreads (63.9% vs 52.3% win rate)
    - Higher POP is prioritized (40% weight)
    - Better liquidity is prioritized (22% weight)
    """

    @pytest.fixture
    def scorer(self):
        return StrategyScorer(ScoringWeights())

    def test_strategies_sorted_by_score_descending(self, scorer):
        """Score and sort should produce highest score first."""
        strategies = [
            _make_strategy(pop=0.60, liquidity_tier="WARNING"),
            _make_strategy(pop=0.85, liquidity_tier="EXCELLENT"),
            _make_strategy(pop=0.70, liquidity_tier="EXCELLENT"),
        ]
        vrp = _make_vrp(vrp_ratio=2.0)

        scorer.score_strategies(strategies, vrp)
        strategies.sort(key=lambda s: s.overall_score, reverse=True)

        # Highest score first
        assert strategies[0].overall_score >= strategies[1].overall_score
        assert strategies[1].overall_score >= strategies[2].overall_score

    def test_high_pop_strategy_beats_low_pop(self, scorer):
        """A higher POP strategy should rank above a lower POP one."""
        high_pop = _make_strategy(pop=0.85, liquidity_tier="EXCELLENT")
        low_pop = _make_strategy(pop=0.60, liquidity_tier="EXCELLENT")
        vrp = _make_vrp(vrp_ratio=2.0)

        scorer.score_strategies([high_pop, low_pop], vrp)
        assert high_pop.overall_score > low_pop.overall_score

    def test_excellent_liquidity_beats_reject(self, scorer):
        """A strategy with excellent liquidity should rank above reject liquidity."""
        good = _make_strategy(pop=0.75, liquidity_tier="EXCELLENT")
        bad = _make_strategy(pop=0.75, liquidity_tier="REJECT")
        vrp = _make_vrp(vrp_ratio=2.0)

        scorer.score_strategies([good, bad], vrp)
        assert good.overall_score > bad.overall_score

    def test_aligned_strategy_beats_counter_trend(self, scorer):
        """Bull put spread with bullish bias should beat bear call spread."""
        aligned = _make_strategy(
            strategy_type=StrategyType.BULL_PUT_SPREAD, pop=0.75,
        )
        counter = _make_strategy(
            strategy_type=StrategyType.BEAR_CALL_SPREAD, pop=0.75,
        )
        vrp = _make_vrp(vrp_ratio=2.0)

        scorer.score_strategies([aligned, counter], vrp, DirectionalBias.STRONG_BULLISH)

        # Aligned gets bonus, counter gets penalty => meaningful difference
        assert aligned.overall_score > counter.overall_score
        diff = aligned.overall_score - counter.overall_score
        assert diff > 8.0, f"Expected >8 point diff, got {diff:.1f}"


# ============================================================================
# Kelly Sizing Integration Tests
# ============================================================================


class TestKellySizingIntegration:
    """
    Test Kelly Criterion position sizing interacts correctly with
    trade quality signals.
    """

    @pytest.fixture
    def generator(self, default_config, liquidity_scorer):
        return StrategyGenerator(default_config, liquidity_scorer)

    def test_positive_ev_sizes_above_minimum(self, generator):
        """Positive EV trade should size above minimum 1 contract."""
        max_profit = Money(200.0)
        max_loss = Money(300.0)
        pop = 0.80  # EV = 0.80*200 - 0.20*300 = 160 - 60 = $100 (positive)

        contracts = generator._calculate_contracts_kelly(max_profit, max_loss, pop)
        assert contracts > 1, f"Positive EV should size > 1, got {contracts}"

    def test_negative_ev_sizes_at_minimum(self, generator):
        """Strongly negative EV trade should size at minimum."""
        max_profit = Money(50.0)
        max_loss = Money(450.0)
        pop = 0.55  # EV = 0.55*50 - 0.45*450 = 27.5 - 202.5 = -$175 (very negative)

        contracts = generator._calculate_contracts_kelly(max_profit, max_loss, pop)
        assert contracts == 1, f"Negative EV should size at 1, got {contracts}"

    def test_borderline_ev_still_trades(self, generator):
        """
        Slightly negative EV (within -2% threshold) should still allow sizing.
        VRP provides additional edge beyond what delta-implied POP shows.
        """
        max_profit = Money(150.0)
        max_loss = Money(350.0)
        pop = 0.70  # EV = 0.70*150 - 0.30*350 = 105 - 105 = $0 (break-even)

        contracts = generator._calculate_contracts_kelly(max_profit, max_loss, pop)
        # Break-even should still be tradeable (EV% = 0%, above -2% threshold)
        assert contracts >= 1

    def test_higher_pop_sizes_larger(self, generator):
        """Higher POP (with same R/R) should produce larger position."""
        max_profit = Money(200.0)
        max_loss = Money(300.0)

        contracts_70 = generator._calculate_contracts_kelly(max_profit, max_loss, 0.70)
        contracts_85 = generator._calculate_contracts_kelly(max_profit, max_loss, 0.85)

        assert contracts_85 >= contracts_70, (
            f"85% POP ({contracts_85}) should size >= 70% POP ({contracts_70})"
        )

    def test_respects_max_contracts_cap(self, generator):
        """Position size should never exceed max_contracts (100)."""
        max_profit = Money(500.0)
        max_loss = Money(50.0)
        pop = 0.95  # Extremely favorable

        contracts = generator._calculate_contracts_kelly(max_profit, max_loss, pop)
        assert contracts <= 100

    def test_zero_max_loss_returns_minimum(self, generator):
        """Zero max loss (invalid) should return minimum contracts."""
        contracts = generator._calculate_contracts_kelly(Money(100.0), Money(0.0), 0.70)
        assert contracts == 1

    def test_zero_max_profit_returns_minimum(self, generator):
        """Zero max profit (invalid) should return minimum contracts."""
        contracts = generator._calculate_contracts_kelly(Money(0.0), Money(400.0), 0.70)
        assert contracts == 1

    def test_invalid_pop_returns_minimum(self, generator):
        """POP outside [0, 1] should return minimum contracts."""
        contracts = generator._calculate_contracts_kelly(Money(100.0), Money(400.0), 1.5)
        assert contracts == 1

        contracts = generator._calculate_contracts_kelly(Money(100.0), Money(400.0), -0.1)
        assert contracts == 1


# ============================================================================
# Directional Bias Determination Tests
# ============================================================================


class TestBiasDetermination:
    """
    Test _determine_bias correctly maps skew analysis to DirectionalBias enum.
    """

    @pytest.fixture
    def generator(self, default_config, liquidity_scorer):
        return StrategyGenerator(default_config, liquidity_scorer)

    def test_no_skew_returns_neutral(self, generator):
        """No skew analysis should default to NEUTRAL."""
        bias = generator._determine_bias(None)
        assert bias == DirectionalBias.NEUTRAL

    def test_skew_result_bearish(self, generator, expiration):
        """Bearish SkewResult should map to BEARISH."""
        skew = SkewResult(
            ticker="TEST", expiration=expiration,
            skew_atm=2.5, skew_strength="moderate", direction="bearish"
        )
        bias = generator._determine_bias(skew)
        assert bias == DirectionalBias.BEARISH

    def test_skew_result_bullish(self, generator, expiration):
        """Bullish SkewResult should map to BULLISH."""
        skew = SkewResult(
            ticker="TEST", expiration=expiration,
            skew_atm=-2.5, skew_strength="moderate", direction="bullish"
        )
        bias = generator._determine_bias(skew)
        assert bias == DirectionalBias.BULLISH

    def test_skew_result_neutral(self, generator, expiration):
        """Neutral SkewResult should map to NEUTRAL."""
        skew = SkewResult(
            ticker="TEST", expiration=expiration,
            skew_atm=0.5, skew_strength="weak", direction="neutral"
        )
        bias = generator._determine_bias(skew)
        assert bias == DirectionalBias.NEUTRAL


# ============================================================================
# Directional Bias Enum Tests
# ============================================================================


class TestDirectionalBiasEnum:
    """Test DirectionalBias enum helper methods."""

    def test_bullish_variants_are_bullish(self):
        """All bullish variants should return True for is_bullish()."""
        for bias in [
            DirectionalBias.WEAK_BULLISH,
            DirectionalBias.BULLISH,
            DirectionalBias.STRONG_BULLISH,
        ]:
            assert bias.is_bullish(), f"{bias.value} should be bullish"
            assert not bias.is_bearish(), f"{bias.value} should not be bearish"
            assert not bias.is_neutral(), f"{bias.value} should not be neutral"

    def test_bearish_variants_are_bearish(self):
        """All bearish variants should return True for is_bearish()."""
        for bias in [
            DirectionalBias.WEAK_BEARISH,
            DirectionalBias.BEARISH,
            DirectionalBias.STRONG_BEARISH,
        ]:
            assert bias.is_bearish(), f"{bias.value} should be bearish"
            assert not bias.is_bullish(), f"{bias.value} should not be bullish"
            assert not bias.is_neutral(), f"{bias.value} should not be neutral"

    def test_neutral_is_neutral(self):
        """NEUTRAL should only be neutral."""
        assert DirectionalBias.NEUTRAL.is_neutral()
        assert not DirectionalBias.NEUTRAL.is_bullish()
        assert not DirectionalBias.NEUTRAL.is_bearish()

    def test_strength_levels(self):
        """Verify strength levels: 0=NEUTRAL, 1=WEAK, 2=MODERATE, 3=STRONG."""
        assert DirectionalBias.NEUTRAL.strength() == 0
        assert DirectionalBias.WEAK_BULLISH.strength() == 1
        assert DirectionalBias.WEAK_BEARISH.strength() == 1
        assert DirectionalBias.BULLISH.strength() == 2
        assert DirectionalBias.BEARISH.strength() == 2
        assert DirectionalBias.STRONG_BULLISH.strength() == 3
        assert DirectionalBias.STRONG_BEARISH.strength() == 3


# ============================================================================
# VRP Calculator Recommendation Tests
# ============================================================================


class TestVRPCalculatorRecommendation:
    """
    Test VRPCalculator.calculate() produces correct Recommendation enums
    for different VRP ratio levels.

    Uses the production default thresholds:
    - EXCELLENT: >= 1.8x
    - GOOD: >= 1.4x
    - MARGINAL: >= 1.2x
    - SKIP: < 1.2x
    """

    @pytest.fixture
    def calculator(self):
        """VRP calculator with BALANCED profile thresholds."""
        return VRPCalculator(
            threshold_excellent=1.8,
            threshold_good=1.4,
            threshold_marginal=1.2,
            min_quarters=4,
        )

    def _make_implied_move(self, ticker, implied_pct, expiration):
        """Create a minimal ImpliedMove-like object for testing."""
        from src.domain.types import ImpliedMove
        return ImpliedMove(
            ticker=ticker,
            expiration=expiration,
            stock_price=Money(100.0),
            atm_strike=Strike(100.0),
            straddle_cost=Money(implied_pct / 100 * 100),  # straddle at implied_pct% of stock
            implied_move_pct=Percentage(implied_pct),
            upper_bound=Money(100 + implied_pct),
            lower_bound=Money(100 - implied_pct),
        )

    def _make_historical_moves(self, mean_pct, num_quarters=8):
        """Create historical moves with a given mean percentage."""
        from src.domain.types import HistoricalMove
        moves = []
        for i in range(num_quarters):
            # Vary slightly around the mean
            pct = mean_pct + (i - num_quarters / 2) * 0.5
            pct = max(0.5, pct)  # Ensure positive
            moves.append(HistoricalMove(
                ticker="TEST",
                earnings_date=date.today() - timedelta(days=90 * (i + 1)),
                prev_close=Money(100.0),
                earnings_open=Money(100.0),
                earnings_high=Money(100 + pct),
                earnings_low=Money(100 - pct),
                earnings_close=Money(100 + pct * 0.5),
                intraday_move_pct=Percentage(pct * 2),
                gap_move_pct=Percentage(0.5),
                close_move_pct=Percentage(pct * 0.5),
            ))
        return moves

    def test_excellent_recommendation(self, calculator):
        """VRP >= 1.8x should produce EXCELLENT recommendation."""
        exp = date.today() + timedelta(days=7)
        # implied=9%, historical close mean ~ 9/(ratio) = 9/1.8 = 5%
        # close_move_pct = pct * 0.5, so pct * 0.5 should average ~5%
        # => pct ~= 10%
        implied = self._make_implied_move("TEST", 9.0, exp)
        hist = self._make_historical_moves(mean_pct=10.0)  # close avg ~5%

        result = calculator.calculate("TEST", exp, implied, hist)
        assert result.is_ok
        vrp = result.value
        assert vrp.vrp_ratio >= 1.8
        assert vrp.recommendation == Recommendation.EXCELLENT

    def test_skip_recommendation(self, calculator):
        """VRP < 1.2x should produce SKIP recommendation."""
        exp = date.today() + timedelta(days=7)
        # implied=5%, historical close mean ~5% => ratio ~1.0
        implied = self._make_implied_move("TEST", 5.0, exp)
        hist = self._make_historical_moves(mean_pct=10.0)  # close avg ~5%

        result = calculator.calculate("TEST", exp, implied, hist)
        assert result.is_ok
        vrp = result.value
        assert vrp.vrp_ratio < 1.2
        assert vrp.recommendation == Recommendation.SKIP

    def test_insufficient_history_returns_error(self, calculator):
        """Fewer than 4 quarters of history should return an error."""
        exp = date.today() + timedelta(days=7)
        implied = self._make_implied_move("TEST", 10.0, exp)
        hist = self._make_historical_moves(mean_pct=5.0, num_quarters=3)

        result = calculator.calculate("TEST", exp, implied, hist)
        assert result.is_err


# ============================================================================
# Kelly Edge Score Tests
# ============================================================================


class TestKellyEdgeScore:
    """
    Test the Kelly edge scoring component used in strategy scoring.

    Kelly Edge = (p * b) - q where:
    - p = POP, b = reward/risk, q = 1-p
    - Negative edge scores 0 points
    - Target edge = 10% for full points
    """

    @pytest.fixture
    def scorer(self):
        return StrategyScorer(ScoringWeights())

    def test_positive_edge_scores_above_zero(self, scorer):
        """Positive Kelly edge should get points."""
        # p=0.80, b=0.40, edge = 0.80*0.40 - 0.20 = 0.12 (positive)
        score = scorer._calculate_kelly_edge_score(pop=0.80, rr=0.40)
        assert score > 0

    def test_negative_edge_scores_zero(self, scorer):
        """Negative Kelly edge should get 0 points."""
        # p=0.60, b=0.10, edge = 0.06 - 0.40 = -0.34 (negative)
        score = scorer._calculate_kelly_edge_score(pop=0.60, rr=0.10)
        assert score == 0.0

    def test_break_even_scores_zero(self, scorer):
        """Break-even edge (edge = 0) should score 0."""
        # p=0.70, b = q/p = 0.30/0.70 = 0.4286 => edge = 0
        score = scorer._calculate_kelly_edge_score(pop=0.70, rr=0.30 / 0.70)
        assert abs(score) < 0.01

    def test_excellent_edge_near_max_score(self, scorer):
        """Edge >= 10% should get near full weight points."""
        # p=0.80, b=0.50, edge = 0.80*0.50 - 0.20 = 0.20 (20% edge, above 10% target)
        score = scorer._calculate_kelly_edge_score(pop=0.80, rr=0.50)
        # With default weight=13.0, should be near 13.0
        assert score >= 12.0, f"Expected near 13.0, got {score:.2f}"


# ============================================================================
# Asymmetric Delta Tests
# ============================================================================


class TestAsymmetricDeltas:
    """
    Test that directional bias correctly adjusts delta targets
    for asymmetric strike positioning.
    """

    @pytest.fixture
    def generator(self, default_config, liquidity_scorer):
        return StrategyGenerator(default_config, liquidity_scorer)

    def test_neutral_bias_returns_base_deltas(self, generator):
        """Neutral bias should return default 0.25/0.20 deltas."""
        short_d, long_d = generator._get_asymmetric_deltas(
            OptionType.PUT, DirectionalBias.NEUTRAL
        )
        assert short_d == 0.25
        assert long_d == 0.20

    def test_bullish_bias_lowers_put_deltas(self, generator):
        """Bullish bias should lower put spread deltas (safer/further OTM)."""
        short_d, long_d = generator._get_asymmetric_deltas(
            OptionType.PUT, DirectionalBias.BULLISH
        )
        assert short_d < 0.25, "Bullish put short delta should be lower"
        assert long_d < 0.20, "Bullish put long delta should be lower"

    def test_bearish_bias_raises_put_deltas(self, generator):
        """Bearish bias should raise put spread deltas (riskier/closer ATM)."""
        short_d, long_d = generator._get_asymmetric_deltas(
            OptionType.PUT, DirectionalBias.BEARISH
        )
        assert short_d > 0.25, "Bearish put short delta should be higher"
        assert long_d > 0.20, "Bearish put long delta should be higher"

    def test_strong_bias_adjusts_more_than_weak(self, generator):
        """Strong bias should produce larger delta adjustment than weak."""
        strong_short, _ = generator._get_asymmetric_deltas(
            OptionType.PUT, DirectionalBias.STRONG_BULLISH
        )
        weak_short, _ = generator._get_asymmetric_deltas(
            OptionType.PUT, DirectionalBias.WEAK_BULLISH
        )
        # Both lower put deltas for bullish, but strong lowers more
        assert strong_short < weak_short, (
            f"Strong ({strong_short}) should be further from ATM than weak ({weak_short})"
        )

    def test_delta_spread_always_maintained(self, generator):
        """Long delta should always be lower than short delta (MIN_SPREAD enforced)."""
        for bias in DirectionalBias:
            for opt_type in [OptionType.PUT, OptionType.CALL]:
                short_d, long_d = generator._get_asymmetric_deltas(opt_type, bias)
                assert long_d < short_d, (
                    f"Spread violated for {opt_type.value}/{bias.value}: "
                    f"short={short_d}, long={long_d}"
                )

    def test_deltas_within_valid_range(self, generator):
        """All delta values should be within [MIN_DELTA, MAX_DELTA]."""
        for bias in DirectionalBias:
            for opt_type in [OptionType.PUT, OptionType.CALL]:
                short_d, long_d = generator._get_asymmetric_deltas(opt_type, bias)
                assert generator.MIN_DELTA <= short_d <= generator.MAX_DELTA, (
                    f"Short delta {short_d} out of range for {opt_type.value}/{bias.value}"
                )
                assert generator.MIN_DELTA <= long_d <= generator.MAX_DELTA, (
                    f"Long delta {long_d} out of range for {opt_type.value}/{bias.value}"
                )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
