"""Unit tests for SizingContext and PositionLimitsSnapshot."""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.domain.enums import DirectionalBias
from src.domain.types import SizingContext, PositionLimitsSnapshot


class TestPositionLimitsSnapshot:
    def test_defaults_are_none(self):
        s = PositionLimitsSnapshot()
        assert s.fcst_ern_iv_effect is None
        assert s.iee_earn_effect is None
        assert s.r_slp_30 is None

    def test_frozen(self):
        s = PositionLimitsSnapshot(fcst_ern_iv_effect=1.5)
        with pytest.raises(Exception):
            s.fcst_ern_iv_effect = 2.0  # type: ignore


class TestSizingContextTRRCap:
    def test_high_trr_cap_50(self):
        ctx = SizingContext(trr_level='HIGH')
        assert ctx.trr_cap == 50

    def test_normal_trr_cap_100(self):
        ctx = SizingContext(trr_level='NORMAL')
        assert ctx.trr_cap == 100

    def test_low_trr_cap_100(self):
        ctx = SizingContext(trr_level='LOW')
        assert ctx.trr_cap == 100

    def test_none_trr_cap_100(self):
        ctx = SizingContext()
        assert ctx.trr_cap == 100


class TestSizingContextIVEffectReduction:
    def test_fcst_above_threshold_triggers_reduction(self):
        ctx = SizingContext(fcst_ern_iv_effect=2.1)
        assert ctx.iv_effect_reduction is True

    def test_fcst_exactly_at_threshold_triggers_reduction(self):
        ctx = SizingContext(fcst_ern_iv_effect=2.0)
        assert ctx.iv_effect_reduction is True

    def test_fcst_below_threshold_no_reduction(self):
        ctx = SizingContext(fcst_ern_iv_effect=1.9)
        assert ctx.iv_effect_reduction is False

    def test_fcst_none_no_reduction(self):
        ctx = SizingContext()
        assert ctx.iv_effect_reduction is False

    def test_divergence_ratio_triggers_reduction(self):
        # iee / fcst = 3.0 / 1.5 = 2.0 >= 1.5 threshold
        ctx = SizingContext(fcst_ern_iv_effect=1.5, iee_earn_effect=3.0)
        assert ctx.iv_effect_reduction is True

    def test_divergence_ratio_below_threshold_no_reduction(self):
        # iee / fcst = 2.0 / 1.5 = 1.33 < 1.5 threshold
        ctx = SizingContext(fcst_ern_iv_effect=1.5, iee_earn_effect=2.0)
        assert ctx.iv_effect_reduction is False

    def test_zero_fcst_no_truthiness_bug(self):
        # fcst=0.0 is not None — should evaluate rule, but 0.0 < 2.0 → no reduction
        ctx = SizingContext(fcst_ern_iv_effect=0.0)
        assert ctx.iv_effect_reduction is False

    def test_iee_none_divergence_rule_skipped(self):
        # fcst below threshold, iee None → no reduction
        ctx = SizingContext(fcst_ern_iv_effect=1.5, iee_earn_effect=None)
        assert ctx.iv_effect_reduction is False


class TestSizingContextEffectiveMaxContracts:
    def test_no_signals_100_contracts(self):
        ctx = SizingContext()
        assert ctx.effective_max_contracts == 100

    def test_high_trr_50_contracts(self):
        ctx = SizingContext(trr_level='HIGH')
        assert ctx.effective_max_contracts == 50

    def test_high_fcst_halves_base(self):
        ctx = SizingContext(fcst_ern_iv_effect=2.5)
        assert ctx.effective_max_contracts == 50

    def test_trr_high_plus_high_fcst_combines(self):
        # TRR cap=50, then halved → 25
        ctx = SizingContext(trr_level='HIGH', fcst_ern_iv_effect=2.5)
        assert ctx.effective_max_contracts == 25

    def test_minimum_1_contract(self):
        ctx = SizingContext(trr_level='HIGH', fcst_ern_iv_effect=3.0)
        assert ctx.effective_max_contracts >= 1


class TestSizingContextCompoundRisk:
    """Compound tail risk: >=2 of [TRR HIGH, sizing alarm, bearish fused skew] -> 25 max.

    Engine-level enforcement of the CLAUDE.md sizing rules 1-2 (previously
    prompt-only in analyze.md). Post-ORATS the sizing alarm cannot fire live,
    so the reachable live combination is TRR HIGH + bearish fused skew.
    """

    def test_trr_high_plus_bearish_fused_caps_25(self):
        ctx = SizingContext(trr_level='HIGH', fused_bias=DirectionalBias.BEARISH)
        assert ctx.compound_risk_active is True
        assert ctx.effective_max_contracts == 25

    def test_trr_high_plus_strong_bearish_fused_caps_25(self):
        ctx = SizingContext(trr_level='HIGH', fused_bias=DirectionalBias.STRONG_BEARISH)
        assert ctx.effective_max_contracts == 25

    def test_bearish_fused_alone_no_cap(self):
        ctx = SizingContext(fused_bias=DirectionalBias.BEARISH)
        assert ctx.compound_risk_active is False
        assert ctx.effective_max_contracts == 100

    def test_trr_high_plus_weak_bearish_not_compound(self):
        # WEAK_BEARISH is not a compound signal (threshold is BEARISH/STRONG_BEARISH)
        ctx = SizingContext(trr_level='HIGH', fused_bias=DirectionalBias.WEAK_BEARISH)
        assert ctx.compound_risk_active is False
        assert ctx.effective_max_contracts == 50

    def test_alarm_plus_bearish_fused_caps_25(self):
        # Sizing alarm (fcst>=2.0) + bearish fused, TRR not HIGH -> compound -> 25
        # (pre-fix behavior was 100//2 = 50)
        ctx = SizingContext(fcst_ern_iv_effect=2.5, fused_bias=DirectionalBias.BEARISH)
        assert ctx.compound_risk_active is True
        assert ctx.effective_max_contracts == 25

    def test_all_three_signals_cap_25(self):
        ctx = SizingContext(
            trr_level='HIGH', fcst_ern_iv_effect=2.5,
            fused_bias=DirectionalBias.STRONG_BEARISH,
        )
        assert ctx.compound_risk_active is True
        assert ctx.effective_max_contracts == 25

    def test_trr_high_plus_alarm_still_25(self):
        # Pre-existing PARTIAL path (TRR cap 50 halved) must stay 25 via compound
        ctx = SizingContext(trr_level='HIGH', fcst_ern_iv_effect=2.5)
        assert ctx.compound_risk_active is True
        assert ctx.effective_max_contracts == 25

    def test_neutral_fused_bias_not_compound(self):
        ctx = SizingContext(trr_level='HIGH', fused_bias=DirectionalBias.NEUTRAL)
        assert ctx.compound_risk_active is False
        assert ctx.effective_max_contracts == 50

    def test_firing_signal_labels_compound(self):
        ctx = SizingContext(trr_level='HIGH', fused_bias=DirectionalBias.BEARISH)
        sig = ctx.firing_signal
        assert sig is not None
        assert 'COMPOUND_RISK' in sig
        assert 'TRR_HIGH' in sig
        assert 'BEARISH_SKEW' in sig

    def test_firing_signal_non_compound_unchanged(self):
        ctx = SizingContext(trr_level='HIGH')
        assert ctx.firing_signal == 'TRR_HIGH'


class TestSizingContextFiringSignal:
    def test_no_signals_returns_none(self):
        ctx = SizingContext()
        assert ctx.firing_signal is None

    def test_trr_high_only(self):
        ctx = SizingContext(trr_level='HIGH')
        assert ctx.firing_signal == 'TRR_HIGH'

    def test_fcst_only(self):
        ctx = SizingContext(fcst_ern_iv_effect=2.5)
        sig = ctx.firing_signal
        assert sig is not None
        assert 'FCST_ERN_IV' in sig
        assert '2.50x' in sig

    def test_trr_plus_fcst(self):
        ctx = SizingContext(trr_level='HIGH', fcst_ern_iv_effect=2.5)
        sig = ctx.firing_signal
        assert sig is not None
        assert 'TRR_HIGH' in sig
        assert 'FCST_ERN_IV' in sig

    def test_divergence_signal_label(self):
        # fcst=1.5 below threshold, iee=3.0 → divergence 2.0x >= 1.5
        ctx = SizingContext(fcst_ern_iv_effect=1.5, iee_earn_effect=3.0)
        sig = ctx.firing_signal
        assert sig is not None
        assert 'IEE_DIVERGENCE' in sig

    def test_fcst_high_plus_divergence(self):
        # fcst above threshold AND divergence: should show both
        ctx = SizingContext(fcst_ern_iv_effect=2.5, iee_earn_effect=5.0)
        sig = ctx.firing_signal
        assert sig is not None
        assert 'FCST' in sig
        assert 'IEE_DIVERGENCE' in sig


class TestSizingContextVRPLiquidityReduction:
    """CLAUDE.md sizing Rule 4: MARGINAL VRP or WARNING liquidity -> 50%
    reduction. Added 2026-07-27 -- this rule previously existed only in
    documentation; MARGINAL-VRP tickers never even reached strategy
    generation (see VRPResult.is_tradeable), so there was nothing to
    reduce. Fixed alongside that gate.
    """

    def test_vrp_marginal_alone_halves_base(self):
        ctx = SizingContext(vrp_marginal=True)
        assert ctx.vrp_or_liquidity_reduction is True
        assert ctx.effective_max_contracts == 50

    def test_liquidity_warning_alone_halves_base(self):
        ctx = SizingContext(liquidity_warning=True)
        assert ctx.vrp_or_liquidity_reduction is True
        assert ctx.effective_max_contracts == 50

    def test_neither_flag_no_reduction(self):
        ctx = SizingContext()
        assert ctx.vrp_or_liquidity_reduction is False
        assert ctx.effective_max_contracts == 100

    def test_both_flags_single_reduction_not_stacked(self):
        # "50% size reduction", singular -- not 25% from double-halving.
        ctx = SizingContext(vrp_marginal=True, liquidity_warning=True)
        assert ctx.effective_max_contracts == 50

    def test_vrp_marginal_plus_trr_high_compounds_to_25(self):
        # TRR HIGH caps at 50 first, VRP_MARGINAL then halves that -> 25.
        # Same resulting number as the compound-risk path but via a
        # different mechanism (sequential cap-then-halve, not the
        # compound_risk_active early return) -- worth locking in separately.
        ctx = SizingContext(trr_level='HIGH', vrp_marginal=True)
        assert ctx.compound_risk_active is False
        assert ctx.effective_max_contracts == 25

    def test_liquidity_warning_plus_trr_high_compounds_to_25(self):
        ctx = SizingContext(trr_level='HIGH', liquidity_warning=True)
        assert ctx.effective_max_contracts == 25

    def test_compound_risk_active_not_further_reduced_by_vrp(self):
        # Compound risk's 25-contract cap is an early return -- rules 1-2
        # are "first match wins" per CLAUDE.md; vrp_marginal must not
        # push it down to 12.
        ctx = SizingContext(
            trr_level='HIGH', fused_bias=None,
            fcst_ern_iv_effect=2.5,  # + TRR HIGH = 2 compound signals
            vrp_marginal=True,
        )
        assert ctx.compound_risk_active is True
        assert ctx.effective_max_contracts == 25

    def test_firing_signal_vrp_marginal_label(self):
        ctx = SizingContext(vrp_marginal=True)
        assert ctx.firing_signal == 'VRP_MARGINAL'

    def test_firing_signal_liquidity_warning_label(self):
        ctx = SizingContext(liquidity_warning=True)
        assert ctx.firing_signal == 'LIQUIDITY_WARNING'

    def test_firing_signal_both_flags_label(self):
        ctx = SizingContext(vrp_marginal=True, liquidity_warning=True)
        sig = ctx.firing_signal
        assert 'VRP_MARGINAL' in sig
        assert 'LIQUIDITY_WARNING' in sig

    def test_firing_signal_trr_plus_vrp_marginal(self):
        ctx = SizingContext(trr_level='HIGH', vrp_marginal=True)
        sig = ctx.firing_signal
        assert sig == 'TRR_HIGH + VRP_MARGINAL'

    def test_firing_signal_fcst_plus_vrp_marginal(self):
        # Neither trr_fired nor compound (only 1 of 3 compound signals present)
        # -- iv_signal and vr_signal combine via the dedicated branch.
        ctx = SizingContext(fcst_ern_iv_effect=2.5, vrp_marginal=True)
        assert ctx.compound_risk_active is False
        sig = ctx.firing_signal
        assert 'FCST_ERN_IV' in sig
        assert 'VRP_MARGINAL' in sig

    def test_firing_signal_below_threshold_fcst_gives_vrp_marginal_only(self):
        ctx = SizingContext(fcst_ern_iv_effect=1.9, vrp_marginal=True)
        assert ctx.firing_signal == 'VRP_MARGINAL'
