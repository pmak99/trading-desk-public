"""
Unit tests for critical strategy selection business logic.

Tests the core decision-making rules that determine which strategies
are generated, how they are sized, and how they are scored/ranked.

Business rules tested (from CLAUDE.md):
1. VRP tier classification (close-to-close, CLAUDE.md §1): EXCELLENT >= 0.99, GOOD >= 0.78,
   MARGINAL >= 0.71, SKIP < 0.71
2. Score calculation and ranking priority
3. Directional bias read from skew (it chooses no spread -- see
   test_strategy_generator.TestOfferingIgnoresBias)
4. Kelly-based position sizing respects trade quality

TRR tiers are pinned in test_domain_tail_risk.py, and the TRR contract caps in
test_sizing_context.py.
"""

from datetime import date, timedelta

import pytest

from src.application.metrics.liquidity_scorer import LiquidityScorer
from src.application.metrics.vrp import VRPCalculator
from src.application.services.strategy import StrategyGenerator
from src.config.config import StrategyConfig, ThresholdsConfig
from src.domain.enums import (
    DirectionalBias,
    OptionType,
    Recommendation,
    StrategyType,
)
from src.domain.scoring.strategy_scorer import StrategyScorer
from src.domain.scoring.weights import ScoringWeights
from src.domain.types import (
    Money,
    Percentage,
    SkewResult,
    Strategy,
    StrategyLeg,
    Strike,
    VRPResult,
)
from src.domain.vrp_tier import (
    VRP_EXCELLENT_FROM,
    VRP_GOOD_FROM,
    VRP_MARGINAL_FROM,
    vrp_tier,
)

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def default_config():
    """Create default StrategyConfig matching production defaults."""
    return StrategyConfig(
        use_kelly_sizing=True,
        kelly_fraction=0.25,
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
    return date(2026, 6, 23)


def _make_vrp(
    ticker: str = "TEST",
    vrp_ratio: float = 2.0,
    implied_move_pct: float = 8.0,
    historical_mean: float = 4.0,
    recommendation: Recommendation = None,
    expiration: date = None,
) -> VRPResult:
    """Helper to create VRPResult; the recommendation defaults to the ratio's tier."""
    if expiration is None:
        expiration = date(2026, 6, 23)
    return VRPResult(
        ticker=ticker,
        expiration=expiration,
        implied_move_pct=Percentage(implied_move_pct),
        historical_mean_move_pct=Percentage(historical_mean),
        vrp_ratio=vrp_ratio,
        edge_score=vrp_ratio / 2.0,
        recommendation=recommendation or vrp_tier(vrp_ratio),
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
    expiration = date(2026, 6, 23)
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

    CLAUDE.md §1 thresholds (src/domain/vrp_tier.py, close-to-close):
    - EXCELLENT: >= VRP_EXCELLENT_FROM
    - GOOD: >= VRP_GOOD_FROM
    - MARGINAL: >= VRP_MARGINAL_FROM
    - SKIP: below VRP_MARGINAL_FROM
    """

    def test_vrp_excellent_at_boundary(self):
        """VRP ratio of exactly VRP_EXCELLENT_FROM should classify as EXCELLENT."""
        config = ThresholdsConfig()
        assert config.vrp_excellent == VRP_EXCELLENT_FROM
        # At exactly the boundary, recommendation should be EXCELLENT
        vrp = _make_vrp(vrp_ratio=VRP_EXCELLENT_FROM, recommendation=Recommendation.EXCELLENT)
        assert vrp.vrp_ratio >= config.vrp_excellent
        assert vrp.is_tradeable is True

    def test_vrp_excellent_above_boundary(self):
        """VRP ratio of 2.5x is well above EXCELLENT threshold."""
        config = ThresholdsConfig()
        vrp = _make_vrp(vrp_ratio=2.5, recommendation=Recommendation.EXCELLENT)
        assert vrp.vrp_ratio >= config.vrp_excellent
        assert vrp.is_tradeable is True

    def test_vrp_good_at_boundary(self):
        """VRP ratio of exactly VRP_GOOD_FROM should be in GOOD tier."""
        config = ThresholdsConfig()
        assert config.vrp_good == VRP_GOOD_FROM
        vrp = _make_vrp(vrp_ratio=VRP_GOOD_FROM, recommendation=Recommendation.GOOD)
        assert vrp.vrp_ratio >= config.vrp_good
        assert vrp.vrp_ratio < config.vrp_excellent
        assert vrp.is_tradeable is True

    def test_vrp_good_below_excellent(self):
        """A ratio just below VRP_EXCELLENT_FROM is GOOD, not EXCELLENT."""
        config = ThresholdsConfig()
        ratio = VRP_EXCELLENT_FROM - 0.01
        assert ratio >= config.vrp_good
        assert ratio < config.vrp_excellent

    def test_vrp_marginal_at_boundary(self):
        """VRP ratio of exactly VRP_MARGINAL_FROM should be MARGINAL."""
        config = ThresholdsConfig()
        assert config.vrp_marginal == VRP_MARGINAL_FROM
        vrp = _make_vrp(vrp_ratio=VRP_MARGINAL_FROM, recommendation=Recommendation.MARGINAL)
        assert vrp.vrp_ratio >= config.vrp_marginal
        assert vrp.vrp_ratio < config.vrp_good
        # MARGINAL is tradeable (50% reduced size) per CLAUDE.md's VRP tier
        # table -- only SKIP (below VRP_MARGINAL_FROM) means don't trade.
        assert vrp.is_tradeable is True

    def test_vrp_skip_below_marginal(self):
        """A ratio below VRP_MARGINAL_FROM should be SKIP."""
        config = ThresholdsConfig()
        ratio = VRP_MARGINAL_FROM - 0.1
        vrp = _make_vrp(vrp_ratio=ratio, recommendation=Recommendation.SKIP)
        assert vrp.vrp_ratio < config.vrp_marginal
        assert vrp.is_tradeable is False

    def test_vrp_is_tradeable_reads_the_injected_recommendation(self):
        """is_tradeable follows the injected recommendation, not a tier re-derived from
        the ratio. 1.0x is EXCELLENT on the live close-to-close ladder (>= VRP_EXCELLENT_FROM,
        0.99) -- an injected SKIP at that ratio still reads not tradeable."""
        vrp = _make_vrp(vrp_ratio=1.0, recommendation=Recommendation.SKIP)
        assert vrp.is_tradeable is False

    def test_vrp_threshold_ordering(self):
        """Thresholds must be strictly ordered: excellent > good > marginal."""
        config = ThresholdsConfig()
        assert config.vrp_excellent > config.vrp_good > config.vrp_marginal

    def test_balanced_profile_defaults(self):
        """Default thresholds are CLAUDE.md §1 (close-to-close)."""
        config = ThresholdsConfig()
        assert config.vrp_excellent == VRP_EXCELLENT_FROM
        assert config.vrp_good == VRP_GOOD_FROM
        assert config.vrp_marginal == VRP_MARGINAL_FROM


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
    """

    @pytest.fixture
    def scorer(self):
        return StrategyScorer(ScoringWeights())

    def test_score_weights_sum_to_100(self):
        """Scoring weights with Greeks should sum to 100."""
        w = ScoringWeights()
        total = (
            w.pop_weight + w.liquidity_weight + w.vrp_weight
            + w.reward_risk_weight + w.greeks_weight
        )
        assert abs(total - 100.0) < 0.01, f"Weights sum to {total}, expected 100"

    def test_score_weights_no_greeks_sum_to_100(self):
        """Scoring weights without Greeks should also sum to 100."""
        w = ScoringWeights()
        total = (
            w.pop_weight_no_greeks + w.liquidity_weight_no_greeks
            + w.vrp_weight_no_greeks + w.reward_risk_weight_no_greeks
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
        vrp = _make_vrp(vrp_ratio=VRP_MARGINAL_FROM + 0.02)
        result = scorer.score_strategy(strategy, vrp)
        # Poor setup should score below 50
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
        # Both stay below ScoringWeights.target_vrp (1.07) so the VRP term
        # hasn't saturated for either input.
        strategy = _make_strategy()
        low_vrp = _make_vrp(vrp_ratio=ScoringWeights().target_vrp - 0.3)
        high_vrp = _make_vrp(vrp_ratio=ScoringWeights().target_vrp - 0.05)

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


def _no_greeks(strategy):
    strategy.position_theta = None
    strategy.position_vega = None
    return strategy


def _no_breakeven(strategy):
    strategy.breakeven = []    # the calendar pilot's shape (calendar_spread.py)
    return strategy


class TestScoreHasNoDeadTerms:
    """The overall score is the sum of its weighted factors and nothing else.

    Until 2026-09-14 two more terms were applied. A profit-zone multiplier
    penalised a narrow range between two breakevens; no strategy the engine
    builds has two (a spread has one, the calendar pilot none) since iron
    condors and strangles were banned, so it was always 1.0. A size term was
    weighted 0.0 in both weight sets. The pinned scores below were recorded
    with both terms still in place.
    """

    @pytest.fixture
    def scorer(self):
        return StrategyScorer(ScoringWeights())

    @pytest.mark.parametrize("strategy,vrp,overall", [
        (lambda: _make_strategy(), lambda: _make_vrp(vrp_ratio=2.0), 93.48571428571428),
        (lambda: _make_strategy(strategy_type=StrategyType.BEAR_CALL_SPREAD, pop=0.68,
                                liquidity_tier="WARNING", contracts=40),
         lambda: _make_vrp(vrp_ratio=0.81), 69.06915887850467),
        (lambda: _no_greeks(_make_strategy(pop=0.62, liquidity_tier="WARNING", max_profit=100.0,
                                           max_loss=400.0, contracts=100)),
         lambda: _make_vrp(vrp_ratio=0.70), 67.04457225017973),
        (lambda: _no_breakeven(_make_strategy(pop=0.55, contracts=10)),
         lambda: _make_vrp(vrp_ratio=2.4, implied_move_pct=15.0), 78.04615384615384),
    ], ids=["put-spread", "call-spread-warning", "no-greeks", "no-breakeven"])
    def test_scores_are_unchanged(self, scorer, strategy, vrp, overall):
        assert scorer.score_strategy(strategy(), vrp()).overall_score == pytest.approx(overall, abs=1e-9)

    @pytest.mark.parametrize("build", [_make_strategy, lambda **kw: _no_greeks(_make_strategy(**kw))],
                             ids=["greeks", "no-greeks"])
    def test_contract_count_does_not_move_the_score(self, scorer, build):
        vrp = _make_vrp(vrp_ratio=2.0)
        scores = {scorer.score_strategy(build(contracts=n), vrp).overall_score for n in (1, 10, 100)}
        assert len(scores) == 1

    def test_overall_is_the_sum_of_the_weighted_factors(self, scorer):
        from src.domain.scoring.factors import (
            calculate_greeks_score,
            calculate_kelly_edge_score,
            calculate_liquidity_score,
        )
        w = scorer.weights
        strategy, vrp = _make_strategy(pop=0.70, liquidity_tier="WARNING"), _make_vrp(vrp_ratio=1.6)
        expected = (
            min(strategy.probability_of_profit / w.target_pop, 1.0) * w.pop_weight
            + calculate_liquidity_score(strategy, w)
            + min(vrp.vrp_ratio / w.target_vrp, 1.0) * w.vrp_weight
            + calculate_kelly_edge_score(strategy.probability_of_profit,
                                         strategy.reward_risk_ratio, w.reward_risk_weight)
            + calculate_greeks_score(strategy, w)
        )
        assert scorer.score_strategy(strategy, vrp).overall_score == pytest.approx(expected, abs=1e-9)

    def test_the_dead_terms_are_gone(self):
        from dataclasses import fields

        from src.domain.scoring import factors

        assert not hasattr(factors, "calculate_profit_zone_multiplier")
        assert not hasattr(StrategyScorer, "_calculate_profit_zone_multiplier")
        assert not {"size_weight", "size_weight_no_greeks"} & {f.name for f in fields(ScoringWeights)}


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

    Uses the production default thresholds (CLAUDE.md §1, close-to-close):
    - EXCELLENT: >= VRP_EXCELLENT_FROM
    - GOOD: >= VRP_GOOD_FROM
    - MARGINAL: >= VRP_MARGINAL_FROM
    - SKIP: below VRP_MARGINAL_FROM
    """

    @pytest.fixture
    def calculator(self):
        """VRP calculator on the CLAUDE.md §1 thresholds."""
        return VRPCalculator(
            threshold_excellent=VRP_EXCELLENT_FROM,
            threshold_good=VRP_GOOD_FROM,
            threshold_marginal=VRP_MARGINAL_FROM,
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
                earnings_date=date(2026, 6, 16) - timedelta(days=90 * (i + 1)),
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
        """A VRP well above VRP_EXCELLENT_FROM (0.99) should produce EXCELLENT recommendation."""
        exp = date(2026, 6, 23)
        # implied=9%, historical close mean ~ 9/(ratio) = 9/1.8 = 5%
        # close_move_pct = pct * 0.5, so pct * 0.5 should average ~5%
        # => pct ~= 10%
        implied = self._make_implied_move("TEST", 9.0, exp)
        hist = self._make_historical_moves(mean_pct=10.0)  # close avg ~5%

        result = calculator.calculate("TEST", exp, implied, hist)
        assert result.is_ok
        vrp = result.value
        assert vrp.vrp_ratio >= 1.8   # comfortably above VRP_EXCELLENT_FROM (0.99)
        assert vrp.recommendation == Recommendation.EXCELLENT

    def test_skip_recommendation(self, calculator):
        """A VRP below VRP_MARGINAL_FROM should produce SKIP recommendation."""
        exp = date(2026, 6, 23)
        # implied=2%, historical close mean ~5% => ratio ~0.4, well below VRP_MARGINAL_FROM
        implied = self._make_implied_move("TEST", 2.0, exp)
        hist = self._make_historical_moves(mean_pct=10.0)  # close avg ~5%

        result = calculator.calculate("TEST", exp, implied, hist)
        assert result.is_ok
        vrp = result.value
        assert vrp.vrp_ratio < VRP_MARGINAL_FROM
        assert vrp.recommendation == Recommendation.SKIP

    def test_insufficient_history_returns_error(self, calculator):
        """Fewer than 4 quarters of history should return an error."""
        exp = date(2026, 6, 23)
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
