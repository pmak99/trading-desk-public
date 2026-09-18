"""Integration tests for the thin StrategyGenerator coordinator."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from src.application.metrics.skew_enhanced import SkewAnalysis
from src.application.services.strategy import generator as generator_module
from src.application.services.strategy.generator import StrategyGenerator
from src.config.config import StrategyConfig
from src.domain.enums import DirectionalBias, OptionType, Recommendation, StrategyType
from src.domain.types import (
    Money,
    OptionChain,
    OptionQuote,
    Percentage,
    SizingContext,
    StrategyRecommendation,
    Strike,
    VRPResult,
)


def make_strike(price: float) -> Strike:
    return Strike(price=Decimal(str(price)))


def make_quote(bid: float, ask: float, delta: float = None, oi: int = 200) -> OptionQuote:
    return OptionQuote(
        bid=Money(bid), ask=Money(ask), open_interest=oi, delta=delta,
        gamma=0.01 if delta else None,
        theta=-0.05 if delta else None,
        vega=0.10 if delta else None,
    )


def make_vrp(ratio: float = 2.0) -> VRPResult:
    return VRPResult(
        ticker="TEST",
        expiration=date(2026, 7, 18),
        implied_move_pct=Percentage(5.0),
        historical_mean_move_pct=Percentage(5.0 / ratio),
        vrp_ratio=ratio,
        edge_score=0.5,
        recommendation=Recommendation.EXCELLENT,
    )


def make_realistic_chain() -> OptionChain:
    """Put + call chain with tiered premiums for a $100 stock."""
    puts = {}
    for price, delta, bid, ask in [
        (75.0,  -0.05, 0.10, 0.20),
        (80.0,  -0.08, 0.20, 0.40),
        (85.0,  -0.12, 0.40, 0.70),
        (90.0,  -0.15, 0.80, 1.20),  # long (mid=1.0)
        (93.0,  -0.20, 1.20, 1.80),
        (95.0,  -0.25, 1.80, 2.20),  # short (mid=2.0)
        (97.0,  -0.30, 2.50, 3.00),
        (100.0, -0.50, 4.50, 5.00),
    ]:
        puts[make_strike(price)] = make_quote(bid=bid, ask=ask, delta=delta)
    calls = {}
    for price, delta, bid, ask in [
        (100.0, 0.50, 4.50, 5.00),
        (103.0, 0.30, 2.50, 3.00),
        (105.0, 0.25, 1.80, 2.20),  # short (mid=2.0)
        (107.0, 0.20, 1.20, 1.80),
        (110.0, 0.15, 0.80, 1.20),  # long (mid=1.0)
        (115.0, 0.12, 0.40, 0.70),
        (120.0, 0.08, 0.20, 0.40),
    ]:
        calls[make_strike(price)] = make_quote(bid=bid, ask=ask, delta=delta)
    return OptionChain(
        ticker="TEST", expiration=date(2026, 7, 18),
        stock_price=Money(100.0), calls=calls, puts=puts,
    )


@pytest.fixture
def config():
    return StrategyConfig()


@pytest.fixture
def liquidity_scorer():
    scorer = MagicMock()
    scorer.classify_option_tier.return_value = "EXCELLENT"
    scorer.calculate_spread_pct.return_value = 15.0
    return scorer


@pytest.fixture
def generator(config, liquidity_scorer):
    return StrategyGenerator(config=config, liquidity_scorer=liquidity_scorer)


class TestStrategyGenerator:
    def test_generate_strategies_returns_recommendation(self, generator):
        chain = make_realistic_chain()
        result = generator.generate_strategies("TEST", chain, make_vrp())
        assert isinstance(result, StrategyRecommendation)
        assert result.ticker == "TEST"
        assert len(result.strategies) >= 1

    def test_excellent_vrp_neutral_generates_two_strategies(self, generator):
        chain = make_realistic_chain()
        result = generator.generate_strategies("TEST", chain, make_vrp(ratio=2.0))
        assert len(result.strategies) == 2

    def test_raises_when_no_strategies_built(self, config, liquidity_scorer):
        empty_chain = OptionChain(
            ticker="TEST", expiration=date(2026, 7, 18),
            stock_price=Money(100.0), calls={}, puts={},
        )
        gen = StrategyGenerator(config=config, liquidity_scorer=liquidity_scorer)
        with pytest.raises(ValueError, match="Could not generate any valid strategies"):
            gen.generate_strategies("TEST", empty_chain, make_vrp())

    def test_recommended_index_is_zero(self, generator):
        result = generator.generate_strategies("TEST", make_realistic_chain(), make_vrp())
        assert result.recommended_index == 0

    def test_strategies_sorted_by_score_descending(self, generator):
        result = generator.generate_strategies("TEST", make_realistic_chain(), make_vrp())
        scores = [s.overall_score for s in result.strategies]
        assert scores == sorted(scores, reverse=True)


class TestVRPLiquidityReductionIntegration:
    """CLAUDE.md sizing Rule 4 (MARGINAL VRP or WARNING liquidity -> 50%
    reduction), verified end-to-end through generate_strategies -- not just
    the SizingContext math in isolation (see test_sizing_context.py), but
    that contracts actually come out halved AND every dollar total derived
    from contracts (max_profit/max_loss/capital_required) stays internally
    consistent with the reduced count, not stale from the first build.
    Added 2026-07-27.
    """

    def _boosted_config(self) -> StrategyConfig:
        # Force the contract CAP to be the binding constraint (not the Kelly
        # EV formula), so halving 100->50 reliably shows up in the result
        # regardless of this fake chain's specific premiums.
        return StrategyConfig(max_contracts=100, risk_budget_per_trade=10_000_000.0)

    def _make_favorable_chain(self) -> OptionChain:
        # make_realistic_chain()'s 25-delta/20-delta strikes (95P/90P) credit
        # $1.00 on a $5 width -> EV = 0.75*100 - 0.25*400 = -25 (negative),
        # so calculate_contracts_kelly bails out to kelly_min_contracts=1
        # before ever reaching apply_kelly_caps -- the code path these tests
        # need to exercise. Same strikes/deltas, wider short premium so EV
        # is comfortably positive and Kelly sizing actually engages the cap.
        puts = {
            make_strike(90.0): make_quote(bid=0.40, ask=0.60, delta=-0.20, oi=200),   # long
            make_strike(95.0): make_quote(bid=2.90, ask=3.10, delta=-0.25, oi=200),   # short
            make_strike(100.0): make_quote(bid=4.50, ask=5.00, delta=-0.50, oi=200),
        }
        calls = {
            make_strike(100.0): make_quote(bid=4.50, ask=5.00, delta=0.50, oi=200),
            make_strike(105.0): make_quote(bid=2.90, ask=3.10, delta=0.25, oi=200),    # short
            make_strike(110.0): make_quote(bid=0.40, ask=0.60, delta=0.20, oi=200),    # long
        }
        return OptionChain(
            ticker="TEST", expiration=date(2026, 7, 18),
            stock_price=Money(100.0), calls=calls, puts=puts,
        )

    def test_vrp_marginal_halves_contracts_vs_no_reduction(self):
        config = self._boosted_config()
        liquidity_scorer = MagicMock()
        liquidity_scorer.classify_option_tier.return_value = "EXCELLENT"
        liquidity_scorer.calculate_spread_pct.return_value = 15.0
        gen = StrategyGenerator(config=config, liquidity_scorer=liquidity_scorer)
        chain = self._make_favorable_chain()

        baseline = gen.generate_strategies(
            "TEST", chain, make_vrp(ratio=2.0), sizing_context=SizingContext(),
        )
        reduced = gen.generate_strategies(
            "TEST", chain, make_vrp(ratio=2.0),
            sizing_context=SizingContext(vrp_marginal=True),
        )

        base_contracts = baseline.strategies[0].contracts
        reduced_contracts = reduced.strategies[0].contracts
        assert base_contracts == 50  # CLAUDE.md rule 4: no-flag default 50 (was 100 until 2026-09-13)
        assert reduced_contracts == 25
        # max_loss must scale with contracts, not be left stale from a
        # different count.
        per_contract_loss = float(baseline.strategies[0].max_loss.amount) / base_contracts
        assert float(reduced.strategies[0].max_loss.amount) == pytest.approx(
            per_contract_loss * reduced_contracts, rel=0.01
        )

    def test_warning_liquidity_triggers_rebuild_and_halves_contracts(self):
        config = self._boosted_config()

        excellent_scorer = MagicMock()
        excellent_scorer.classify_option_tier.return_value = "EXCELLENT"
        excellent_scorer.calculate_spread_pct.return_value = 15.0
        gen_excellent = StrategyGenerator(config=config, liquidity_scorer=excellent_scorer)

        warning_scorer = MagicMock()
        warning_scorer.classify_option_tier.return_value = "WARNING"
        warning_scorer.calculate_spread_pct.return_value = 20.0
        gen_warning = StrategyGenerator(config=config, liquidity_scorer=warning_scorer)

        chain = self._make_favorable_chain()
        baseline = gen_excellent.generate_strategies(
            "TEST", chain, make_vrp(ratio=2.0), sizing_context=SizingContext(),
        )
        reduced = gen_warning.generate_strategies(
            "TEST", chain, make_vrp(ratio=2.0), sizing_context=SizingContext(),
        )

        base_contracts = baseline.strategies[0].contracts
        reduced_contracts = reduced.strategies[0].contracts
        assert base_contracts == 50  # CLAUDE.md rule 4: no-flag default 50 (was 100 until 2026-09-13)
        assert reduced_contracts == 25
        assert reduced.strategies[0].liquidity_tier == "WARNING"

        per_contract_loss = float(baseline.strategies[0].max_loss.amount) / base_contracts
        assert float(reduced.strategies[0].max_loss.amount) == pytest.approx(
            per_contract_loss * reduced_contracts, rel=0.01
        )

    def test_warning_liquidity_skipped_when_vrp_already_reduced(self):
        # Both conditions fire -> single reduction (not stacked to 12).
        config = self._boosted_config()
        warning_scorer = MagicMock()
        warning_scorer.classify_option_tier.return_value = "WARNING"
        warning_scorer.calculate_spread_pct.return_value = 20.0
        gen = StrategyGenerator(config=config, liquidity_scorer=warning_scorer)

        result = gen.generate_strategies(
            "TEST", self._make_favorable_chain(), make_vrp(ratio=2.0),
            sizing_context=SizingContext(vrp_marginal=True),
        )
        assert result.strategies[0].contracts == 25
        assert result.strategies[0].liquidity_tier == "WARNING"

    def test_no_reduction_when_liquidity_excellent_and_vrp_not_marginal(self):
        config = self._boosted_config()
        excellent_scorer = MagicMock()
        excellent_scorer.classify_option_tier.return_value = "EXCELLENT"
        excellent_scorer.calculate_spread_pct.return_value = 15.0
        gen = StrategyGenerator(config=config, liquidity_scorer=excellent_scorer)

        result = gen.generate_strategies(
            "TEST", self._make_favorable_chain(), make_vrp(ratio=2.0),
            sizing_context=SizingContext(),
        )
        assert result.strategies[0].contracts == 50  # CLAUDE.md rule 4: no-flag default 50 (was 100 until 2026-09-13)


class TestShortStrikeIgnoresBias:
    """Direction has no validated edge: skew measures hedge demand, not
    conviction (CLAUDE.md §6). Until 2026-09-14 a bearish bias moved the put
    spread's short strike from 25Δ to 30Δ (35Δ when strong) -- closer to the
    money on the side that loses if the read is right -- and a bullish bias
    did the same to the call spread. Every spread now sells the configured
    base delta whatever the bias.
    """

    SPREAD_FOR = {
        OptionType.PUT: StrategyType.BULL_PUT_SPREAD,
        OptionType.CALL: StrategyType.BEAR_CALL_SPREAD,
    }

    @staticmethod
    def _delta_ladder_chain() -> OptionChain:
        # A 2% implied move puts every strike from 15Δ to 35Δ outside the
        # move, so delta-based selection is accepted and never falls back to
        # distance-based -- the path where a bias-shifted target would show.
        puts, calls = {}, {}
        for price, delta, mid in [
            (85.0, -0.08, 0.80), (88.0, -0.12, 1.20), (90.0, -0.15, 1.50),
            (92.0, -0.20, 2.00), (93.0, -0.25, 2.50), (95.0, -0.30, 3.00),
            (96.0, -0.35, 3.50), (100.0, -0.50, 5.00),
        ]:
            puts[make_strike(price)] = make_quote(bid=mid - 0.05, ask=mid + 0.05, delta=delta)
        for price, delta, mid in [
            (100.0, 0.50, 5.00), (104.0, 0.35, 3.50), (105.0, 0.30, 3.00),
            (107.0, 0.25, 2.50), (108.0, 0.20, 2.00), (110.0, 0.15, 1.50),
            (112.0, 0.12, 1.20), (115.0, 0.08, 0.80),
        ]:
            calls[make_strike(price)] = make_quote(bid=mid - 0.05, ask=mid + 0.05, delta=delta)
        return OptionChain(
            ticker="TEST", expiration=date(2026, 7, 18),
            stock_price=Money(100.0), calls=calls, puts=puts,
        )

    def _short_leg_delta(self, generator, option_type, bias) -> float:
        chain = self._delta_ladder_chain()
        vrp = VRPResult(
            ticker="TEST", expiration=date(2026, 7, 18),
            implied_move_pct=Percentage(2.0), historical_mean_move_pct=Percentage(1.0),
            vrp_ratio=2.0, edge_score=0.5, recommendation=Recommendation.EXCELLENT,
        )
        skew = MagicMock()
        skew.directional_bias = bias
        # Build only the given side, so the one strategy returned is the one measured.
        with patch.object(generator_module, "OFFERED_SPREADS",
                          (self.SPREAD_FOR[option_type],)):
            result = generator.generate_strategies("TEST", chain, vrp, skew=skew)
        (strategy,) = result.strategies
        (short,) = [leg for leg in strategy.legs if leg.action == "SELL"]
        side = chain.puts if option_type == OptionType.PUT else chain.calls
        return abs(side[short.strike].delta)

    @pytest.mark.parametrize("option_type", [OptionType.PUT, OptionType.CALL])
    @pytest.mark.parametrize("bias", list(DirectionalBias))
    def test_short_leg_never_closer_to_the_money_than_base_delta(
        self, generator, config, option_type, bias
    ):
        assert self._short_leg_delta(generator, option_type, bias) <= config.target_delta_short

    @pytest.mark.parametrize("option_type", [OptionType.PUT, OptionType.CALL])
    @pytest.mark.parametrize("bias", list(DirectionalBias))
    def test_short_leg_sells_the_base_delta_under_every_bias(
        self, generator, config, option_type, bias
    ):
        assert self._short_leg_delta(generator, option_type, bias) == pytest.approx(
            config.target_delta_short
        )


class TestScoreIgnoresBias:
    """Direction gets no points in strategy scoring: skew measures hedge
    demand, not conviction (CLAUDE.md §6). Until 2026-09-14 the scorer added
    +3/+5/+8 (WEAK/MODERATE/STRONG) to the spread on the bias side and took 3
    from the other -- up to 11 points between two spreads, enough to decide
    the pick whenever both were offered. A spread now scores the same under
    every bias, component by component.
    """

    SPREADS = [StrategyType.BULL_PUT_SPREAD, StrategyType.BEAR_CALL_SPREAD]

    @staticmethod
    def _generate(generator, spread_types, bias):
        skew = None
        if bias is not None:
            skew = MagicMock()
            skew.directional_bias = bias
        # Offer the given candidates, so a single spread can be compared alone.
        with patch.object(generator_module, "OFFERED_SPREADS", tuple(spread_types)):
            return generator.generate_strategies(
                "TEST", make_realistic_chain(), make_vrp(), skew=skew
            )

    @staticmethod
    def _components(strategy):
        return (
            strategy.strategy_type,
            strategy.overall_score,
            strategy.profitability_score,
            strategy.risk_score,
            strategy.rationale,
        )

    @pytest.mark.parametrize("spread_type", SPREADS)
    @pytest.mark.parametrize("bias", list(DirectionalBias))
    def test_every_score_component_is_the_same_under_every_bias(
        self, generator, spread_type, bias
    ):
        (unbiased,) = self._generate(generator, [spread_type], None).strategies
        (biased,) = self._generate(generator, [spread_type], bias).strategies
        assert self._components(biased) == self._components(unbiased)

    @pytest.mark.parametrize("bias", list(DirectionalBias))
    def test_the_ranking_and_its_rationale_are_the_same_under_every_bias(
        self, generator, bias
    ):
        unbiased = self._generate(generator, self.SPREADS, None)
        biased = self._generate(generator, self.SPREADS, bias)
        assert [self._components(s) for s in biased.strategies] == [
            self._components(s) for s in unbiased.strategies
        ]
        assert biased.recommendation_rationale == unbiased.recommendation_rationale


class TestOfferingIgnoresBias:
    """Direction chooses no spread: skew measures hedge demand, not conviction
    (CLAUDE.md §6). Until 2026-09-14 a STRONG bias built only the spread on
    its own side at every VRP tier, a MODERATE one did the same under GOOD and
    MARGINAL, and with TRR HIGH a MODERATE or STRONG bias kept only its own
    side -- the scorer never saw the other spread, so a thin, lower-scoring
    spread could be the only one offered. Both spreads are now built and
    scored whatever the bias, at every tier, and the score picks.
    """

    TIER_RATIO = {
        Recommendation.EXCELLENT: 2.5, Recommendation.GOOD: 1.6, Recommendation.MARGINAL: 1.3,
    }
    TRR_LEVELS = [None, "LOW", "NORMAL", "HIGH"]
    SPREADS = {StrategyType.BULL_PUT_SPREAD, StrategyType.BEAR_CALL_SPREAD}
    # A thin short strike rates WARNING liquidity; the other side scores 11 points higher.
    WINNER = {"put": StrategyType.BEAR_CALL_SPREAD, "call": StrategyType.BULL_PUT_SPREAD}

    @classmethod
    def _generate(cls, thin_side, tier, trr, bias):
        chain = make_realistic_chain()
        side = "puts" if thin_side == "put" else "calls"
        short = make_strike(95.0 if thin_side == "put" else 105.0)
        quotes = dict(getattr(chain, side))
        quotes[short] = replace(quotes[short], open_interest=10)
        chain = replace(chain, **{side: quotes})

        liquidity_scorer = MagicMock()
        liquidity_scorer.classify_option_tier.side_effect = (
            lambda quote: "WARNING" if quote.open_interest < 100 else "EXCELLENT"
        )
        liquidity_scorer.calculate_spread_pct.return_value = 15.0

        vrp = replace(make_vrp(cls.TIER_RATIO[tier]), recommendation=tier)
        skew = SkewAnalysis(
            ticker="TEST", expiration=chain.expiration, stock_price=chain.stock_price,
            skew_atm=Percentage(0.0), curvature=0.0, strength="smirk",
            directional_bias=bias, confidence=0.9, num_points=8, slope_atm=0.0,
            bias_confidence=0.9,
        )
        return StrategyGenerator(StrategyConfig(), liquidity_scorer).generate_strategies(
            "TEST", chain, vrp, skew=skew, tail_risk_level=trr,
        )

    @pytest.mark.parametrize("thin_side", ["put", "call"])
    @pytest.mark.parametrize("trr", TRR_LEVELS)
    @pytest.mark.parametrize("tier", list(TIER_RATIO))
    @pytest.mark.parametrize("bias", list(DirectionalBias))
    def test_offered_spreads_and_top_pick_are_the_neutral_ones(self, bias, tier, trr, thin_side):
        neutral = self._generate(thin_side, tier, trr, DirectionalBias.NEUTRAL)
        biased = self._generate(thin_side, tier, trr, bias)
        assert [s.strategy_type for s in biased.strategies] == [
            s.strategy_type for s in neutral.strategies
        ]
        assert biased.recommended_strategy.strategy_type is self.WINNER[thin_side]

    @pytest.mark.parametrize("thin_side", ["put", "call"])
    @pytest.mark.parametrize("trr", [None, "LOW", "NORMAL"])
    @pytest.mark.parametrize("tier", list(TIER_RATIO))
    def test_both_spreads_are_offered_best_first(self, tier, trr, thin_side):
        result = self._generate(thin_side, tier, trr, DirectionalBias.NEUTRAL)
        winner = self.WINNER[thin_side]
        (loser,) = self.SPREADS - {winner}
        assert [s.strategy_type for s in result.strategies] == [winner, loser]

    @pytest.mark.parametrize("thin_side", ["put", "call"])
    @pytest.mark.parametrize("tier", list(TIER_RATIO))
    def test_trr_high_offers_one_spread_the_higher_scoring(self, tier, thin_side):
        result = self._generate(thin_side, tier, "HIGH", DirectionalBias.NEUTRAL)
        assert [s.strategy_type for s in result.strategies] == [self.WINNER[thin_side]]

    @pytest.mark.parametrize("bias", list(DirectionalBias))
    def test_a_trr_high_pick_discloses_the_spread_it_beat(self, bias):
        # Until 2026-09-14 the TRR HIGH cut ran before the rationale, which then
        # saw one spread and never said the pick carried no directional edge.
        result = self._generate("put", Recommendation.EXCELLENT, "HIGH", bias)
        assert "no directional edge" in result.recommendation_rationale
        assert "over Bull Put Spread" in result.recommendation_rationale

    def test_no_banned_structure_is_offered(self):
        assert set(generator_module.OFFERED_SPREADS) == self.SPREADS


def test_a_banned_structure_has_no_strategy_type():
    """Iron condors and iron butterflies are banned (CLAUDE.md). The engine still
    named both, and carried code only they could reach: Strategy.strike_description
    branches and metrics.combine_greeks. The journal's IRON_CONDOR is its own text
    vocabulary (strategies.strategy_type's CHECK), not this enum."""
    from src.application.services.strategy import metrics

    assert {t.name for t in StrategyType} == {"BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "CALENDAR_SPREAD"}
    assert not hasattr(metrics, "combine_greeks")
