"""
Sizing tiers must be tunable through config, not hardcoded.

Before Sep 2026 SizingContext hardcoded every tier: `50 if trr_level == 'HIGH'
else 100`, a compound-risk floor of 25, and two `// 2` reductions. config.max_contracts
was read only on effective_cap()'s no-context fallback, so MAX_CONTRACTS could not move
the live cap at all — the divergence that made 2.0/.env's MAX_CONTRACTS=50 a no-op.

Defaults reproduce the old numbers except one deliberate change: since 2026-09-13
the no-flag cap is SIZING_DEFAULT_CAP (50, CLAUDE.md rule 4), and SIZING_BASE_CAP (100)
is only the ceiling. Before that the no-flag cap was the ceiling itself.
"""

import pytest

from src.config.config import Config, StrategyConfig
from src.domain.enums import DirectionalBias
from src.domain.types import SizingContext


class TestDefaultsUnchanged:
    """Regression guard: tuning must not silently move live sizing."""

    @pytest.mark.parametrize("trr,expected", [
        ("HIGH", 50), ("NORMAL", 50), ("LOW", 50), (None, 50),
    ])
    def test_trr_cap_defaults(self, trr, expected):
        assert SizingContext(trr_level=trr).trr_cap == expected

    @pytest.mark.parametrize("trr,expected", [
        ("HIGH", 50), ("NORMAL", 50), ("LOW", 50), (None, 50),
    ])
    def test_effective_defaults(self, trr, expected):
        assert SizingContext(trr_level=trr).effective_max_contracts == expected

    def test_vrp_marginal_halves(self):
        assert SizingContext(trr_level="NORMAL", vrp_marginal=True).effective_max_contracts == 25

    def test_liquidity_warning_halves(self):
        assert SizingContext(trr_level="NORMAL", liquidity_warning=True).effective_max_contracts == 25

    def test_high_trr_plus_reduction(self):
        assert SizingContext(trr_level="HIGH", liquidity_warning=True).effective_max_contracts == 25

    def test_compound_risk_floor(self):
        ctx = SizingContext(trr_level="HIGH", fused_bias=DirectionalBias.BEARISH)
        assert ctx.compound_risk_active
        assert ctx.effective_max_contracts == 25


class TestTiersAreTunable:

    def test_default_cap_tunable(self):
        assert SizingContext(trr_level="NORMAL", default_cap=60).effective_max_contracts == 60

    def test_base_cap_is_a_ceiling(self):
        assert SizingContext(trr_level="NORMAL", base_cap=40).effective_max_contracts == 40
        assert SizingContext(trr_level="NORMAL", base_cap=60).effective_max_contracts == 50

    def test_trr_high_cap_tunable(self):
        ctx = SizingContext(trr_level="HIGH", trr_high_cap=30)
        assert ctx.trr_cap == 30
        assert ctx.effective_max_contracts == 30

    def test_compound_risk_cap_tunable(self):
        ctx = SizingContext(trr_level="HIGH", fused_bias=DirectionalBias.BEARISH,
                            compound_risk_cap=10)
        assert ctx.effective_max_contracts == 10

    def test_reduction_factor_tunable(self):
        ctx = SizingContext(trr_level="NORMAL", vrp_marginal=True, reduction_factor=0.25)
        assert ctx.effective_max_contracts == 12

    def test_reduction_factor_one_disables_reduction(self):
        ctx = SizingContext(trr_level="NORMAL", vrp_marginal=True, reduction_factor=1.0)
        assert ctx.effective_max_contracts == 50

    def test_tuned_default_still_halves(self):
        ctx = SizingContext(trr_level="NORMAL", default_cap=80, liquidity_warning=True)
        assert ctx.effective_max_contracts == 40

    def test_never_returns_below_one(self):
        ctx = SizingContext(trr_level="NORMAL", base_cap=1, vrp_marginal=True)
        assert ctx.effective_max_contracts == 1

    def test_replace_preserves_tuned_tiers(self):
        """generator.py rebuilds the context via dataclasses.replace() when
        liquidity fires; tuned tiers must survive that."""
        from dataclasses import replace
        ctx = SizingContext(trr_level="NORMAL", default_cap=80, trr_high_cap=40)
        rebuilt = replace(ctx, liquidity_warning=True)
        assert rebuilt.default_cap == 80
        assert rebuilt.trr_high_cap == 40
        assert rebuilt.effective_max_contracts == 40


class TestConfigWiring:

    def test_config_defaults(self):
        c = StrategyConfig()
        assert c.sizing_base_cap == 100
        assert c.sizing_default_cap == 50
        assert c.sizing_trr_high_cap == 50
        assert c.sizing_compound_risk_cap == 25
        assert c.sizing_reduction_factor == 0.5

    def test_env_overrides(self, monkeypatch):
        monkeypatch.setenv("SIZING_BASE_CAP", "60")
        monkeypatch.setenv("SIZING_TRR_HIGH_CAP", "30")
        monkeypatch.setenv("SIZING_COMPOUND_RISK_CAP", "10")
        monkeypatch.setenv("SIZING_REDUCTION_FACTOR", "0.25")
        s = Config.from_env().strategy
        assert (s.sizing_base_cap, s.sizing_trr_high_cap,
                s.sizing_compound_risk_cap, s.sizing_reduction_factor) == (60, 30, 10, 0.25)

    def test_from_strategy_config_builds_tuned_context(self, monkeypatch):
        monkeypatch.setenv("SIZING_DEFAULT_CAP", "60")
        monkeypatch.setenv("SIZING_TRR_HIGH_CAP", "30")
        cfg = Config.from_env().strategy
        ctx = SizingContext.from_strategy_config(cfg, trr_level="HIGH")
        assert ctx.effective_max_contracts == 30
        assert SizingContext.from_strategy_config(
            cfg, trr_level="NORMAL").effective_max_contracts == 60

    def test_from_strategy_config_passes_through_signals(self):
        cfg = StrategyConfig()
        ctx = SizingContext.from_strategy_config(
            cfg, trr_level="HIGH", fused_bias=DirectionalBias.BEARISH, vrp_marginal=True)
        assert ctx.trr_level == "HIGH"
        assert ctx.fused_bias is DirectionalBias.BEARISH
        assert ctx.vrp_marginal is True
        assert ctx.compound_risk_active
