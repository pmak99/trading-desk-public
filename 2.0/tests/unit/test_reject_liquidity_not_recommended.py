"""A REJECT-liquidity spread is never the recommendation, and never sized to trade.

CLAUDE.md §2: REJECT -> check the ATM chain at the broker; an ATM single with a
bid of $1.50+ and OI of 200+ trades as a single at half size, else skip. Until
2026-09-16 /analyze ordered spreads by score alone, sized a REJECT spread like any
other (only WARNING halved), and could print it as the recommendation followed by
"Enter position before earnings".
"""
import logging
import sys
from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.application.services.strategy.generator import StrategyGenerator
from src.config.config import StrategyConfig
from src.domain.enums import Recommendation, StrategyType
from src.domain.errors import Ok
from src.domain.scoring.rationale import generate_recommendation_rationale
from src.domain.scoring.weights import ScoringWeights
from src.domain.types import (
    Money,
    OptionChain,
    OptionQuote,
    Percentage,
    Strike,
    VRPResult,
)

PUT, CALL = StrategyType.BULL_PUT_SPREAD, StrategyType.BEAR_CALL_SPREAD


def _quote(bid, ask, delta, oi):
    return OptionQuote(bid=Money(bid), ask=Money(ask), open_interest=oi, delta=delta,
                       implied_volatility=Percentage(60.0))


def _chain(put_oi=500, call_oi=500):
    puts = {Strike(Decimal(str(k))): _quote(b, a, d, put_oi) for k, d, b, a in [
        (90.0, -0.15, 0.80, 1.20), (93.0, -0.20, 1.20, 1.80), (95.0, -0.25, 1.80, 2.20),
        (100.0, -0.50, 4.50, 5.00)]}
    calls = {Strike(Decimal(str(k))): _quote(b, a, d, call_oi) for k, d, b, a in [
        (100.0, 0.50, 4.50, 5.00), (105.0, 0.25, 1.80, 2.20), (107.0, 0.20, 1.20, 1.80),
        (110.0, 0.15, 0.80, 1.20)]}
    return OptionChain(ticker="TEST", expiration=date(2026, 10, 16), stock_price=Money(100.0),
                       calls=calls, puts=puts)


VRP = VRPResult(ticker="TEST", expiration=date(2026, 10, 16), implied_move_pct=Percentage(5.0),
                historical_mean_move_pct=Percentage(2.0), vrp_ratio=2.5, edge_score=1.0,
                recommendation=Recommendation.EXCELLENT, historical_quarters=12)


def _generator(favour=None):
    """Liquidity is REJECT on a leg with OI under 100. `favour` rigs the scorer
    so that spread type outscores the other by 20 points."""
    scorer = MagicMock()
    scorer.classify_option_tier.side_effect = lambda q: "REJECT" if q.open_interest < 100 else "EXCELLENT"
    scorer.calculate_spread_pct.return_value = 5.0
    gen = StrategyGenerator(StrategyConfig(), scorer)
    if favour is not None:
        real = gen.scorer.score_strategies

        def rigged(strategies, vrp):
            real(strategies, vrp)
            for s in strategies:
                s.overall_score = 90.0 if s.strategy_type is favour else 70.0
        gen.scorer.score_strategies = rigged
    return gen


class TestGeneratorRanksRejectLast:
    def test_a_reject_spread_never_outranks_a_tradeable_one(self):
        rec = _generator(favour=PUT).generate_strategies("TEST", _chain(put_oi=20), VRP)
        assert [s.strategy_type for s in rec.strategies] == [CALL, PUT]
        assert rec.strategies[1].overall_score > rec.strategies[0].overall_score
        assert not rec.recommended_strategy.liquidity_rejected
        assert "Bull Put Spread is liquidity REJECT" in rec.recommendation_rationale
        assert "coin flip" not in rec.recommendation_rationale

    def test_scores_still_order_two_tradeable_spreads(self):
        rec = _generator(favour=PUT).generate_strategies("TEST", _chain(), VRP)
        assert [s.strategy_type for s in rec.strategies] == [PUT, CALL]

    def test_trr_high_keeps_the_tradeable_spread(self):
        rec = _generator(favour=PUT).generate_strategies(
            "TEST", _chain(put_oi=20), VRP, tail_risk_level="HIGH")
        assert [s.strategy_type for s in rec.strategies] == [CALL]

    def test_every_spread_reject_names_no_size(self):
        rec = _generator().generate_strategies("TEST", _chain(put_oi=20, call_oi=20), VRP)
        assert all(s.liquidity_rejected for s in rec.strategies)
        assert "contracts" not in rec.recommendation_rationale
        assert "not tradeable as a spread" in rec.recommendation_rationale
        assert "ATM single" in rec.recommendation_rationale


class TestRationale:
    WEIGHTS = ScoringWeights()

    def _strategy(self, stype, tier, score):
        s = _generator().generate_strategies("TEST", _chain(), VRP).strategies[0]
        return replace(s, strategy_type=stype, liquidity_tier=tier, overall_score=score)

    def test_a_lower_scoring_pick_over_a_reject_runner_up_claims_no_score_gap(self):
        text = generate_recommendation_rationale(
            [self._strategy(CALL, "WARNING", 60.0), self._strategy(PUT, "REJECT", 75.0)],
            VRP, self.WEIGHTS)
        assert "Bull Put Spread is liquidity REJECT" in text
        assert "pts over" not in text

    def test_a_reject_runner_up_behind_a_reject_pick_keeps_the_score_disclosure(self):
        text = generate_recommendation_rationale(
            [self._strategy(PUT, "REJECT", 70.0), self._strategy(CALL, "REJECT", 68.0)],
            VRP, self.WEIGHTS)
        assert "coin flip" in text
        assert "contracts" not in text


def _run_analyze(strategies, caplog):
    import scripts.analyze as analyze

    vrp = SimpleNamespace(
        vrp_ratio=2.5, historical_mean_move_pct=Percentage(2.0), edge_score=1.0,
        recommendation=Recommendation.EXCELLENT, historical_quarters=12, is_tradeable=True,
        implied_move_pct=Percentage(5.0),
    )
    analysis = SimpleNamespace(
        implied_move=SimpleNamespace(stock_price=Money(100.0), implied_move_pct=Percentage(5.0),
                                     expiration=date(2026, 10, 16), upper_bound=Money(105.0),
                                     lower_bound=Money(95.0), avg_iv=None),
        vrp=vrp, vrp_close_ratio=None, historical_close_mean_pct=None,
        vrp_intraday_ratio=None, historical_intraday_mean_pct=None, strategies=strategies,
        expiration=date(2026, 10, 16), skew=None, term_structure=None, calendar_candidate=None,
        existing_position_warning=None, stale_history_warning=None,
    )
    container = Mock()
    container.earnings_repository.get_timing.return_value = Mock(is_ok=False, is_err=True, error="none")
    container.analyzer.analyze.return_value = Ok(analysis)
    container.market_conditions_analyzer.get_current_conditions.side_effect = RuntimeError("offline")
    argv = ["analyze.py", "TEST", "--earnings-date", "2026-10-15", "--expiration", "2026-10-16",
            "--strategies"]
    caplog.set_level(logging.INFO)
    with patch.object(sys, "argv", argv), \
         patch.object(analyze, "setup_logging"), \
         patch.object(analyze, "apply_migrations"), \
         patch.object(analyze, "Config", Mock(from_env=Mock(return_value=SimpleNamespace(
             strategy=StrategyConfig())))), \
         patch.object(analyze, "Container", return_value=container):
        assert analyze.main() == 0
    return [r.getMessage() for r in caplog.records if r.name == "scripts.analyze"]


class TestAnalyzeOutput:
    def test_one_reject_side_recommends_the_other_and_sizes_only_it(self, caplog):
        rec = _generator(favour=PUT).generate_strategies("TEST", _chain(put_oi=20), VRP)
        out = "\n".join(_run_analyze(rec, caplog))
        assert "💡 Recommended Strategy: Bear Call Spread" in out
        assert "Contracts: not sized — liquidity REJECT" in out        # the put spread's block
        assert "💰 PROFIT/LOSS (one spread):" in out
        assert "Enter position before earnings" in out
        assert "natural (sell at bid, buy at ask)" in out
        assert "at natural" in out

    def test_every_spread_reject_prints_the_section_2_check_not_entry_steps(self, caplog):
        rec = _generator().generate_strategies("TEST", _chain(put_oi=20, call_oi=20), VRP)
        lines = _run_analyze(rec, caplog)
        out = "\n".join(lines)
        assert "Recommended Strategy" not in out
        assert "Best-scored spread:" in out and "not tradeable as a spread" in out
        assert "Enter position before earnings" not in out
        assert "   2. ATM single bid ≥ $1.50 and OI ≥ 200 → trade it as a single at half size" in lines
        assert "   3. Otherwise skip this event" in lines
        assert "Contracts: 50" not in out


@pytest.mark.parametrize("amount,text", [(0.6, "$0.60"), (-0.3, "debit $0.30")])
def test_a_natural_debit_reads_as_a_debit(amount, text):
    from scripts.analyze import _signed_credit
    assert _signed_credit(Money(amount)) == text
