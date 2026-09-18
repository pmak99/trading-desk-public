"""Tests for SizingContext-driven contract caps in strategy_generator."""

from unittest.mock import Mock

from src.domain.types import SizingContext


class TestStrategyGeneratorSizingContext:
    """Contract cap is driven by SizingContext, not config.max_contracts."""

    def _make_generator(self, max_contracts: int = 100):
        from src.application.services.strategy import StrategyGenerator
        from src.config.config import StrategyConfig
        config = StrategyConfig(max_contracts=max_contracts)
        liquidity_scorer = Mock()
        return StrategyGenerator(config, liquidity_scorer)

    def test_no_sizing_context_uses_config_max(self):
        gen = self._make_generator(max_contracts=100)
        cap = gen._effective_cap(sizing_context=None)
        assert cap == 100

    def test_custom_config_max_respected_when_no_context(self):
        gen = self._make_generator(max_contracts=75)
        cap = gen._effective_cap(sizing_context=None)
        assert cap == 75

    def test_trr_high_caps_at_50(self):
        gen = self._make_generator(max_contracts=100)
        ctx = SizingContext(trr_level='HIGH')
        cap = gen._effective_cap(sizing_context=ctx)
        assert cap == 50

    def test_high_fcst_halves_default_50(self):
        gen = self._make_generator(max_contracts=100)
        ctx = SizingContext(fcst_ern_iv_effect=2.5)
        cap = gen._effective_cap(sizing_context=ctx)
        assert cap == 25

    def test_trr_high_plus_high_fcst_gives_25(self):
        gen = self._make_generator(max_contracts=100)
        ctx = SizingContext(trr_level='HIGH', fcst_ern_iv_effect=2.5)
        cap = gen._effective_cap(sizing_context=ctx)
        assert cap == 25

    def test_no_signals_gives_default_50(self):
        gen = self._make_generator(max_contracts=100)
        ctx = SizingContext()
        cap = gen._effective_cap(sizing_context=ctx)
        assert cap == 50  # CLAUDE.md rule 4: no-flag default 50 (was 100 until 2026-09-13)
