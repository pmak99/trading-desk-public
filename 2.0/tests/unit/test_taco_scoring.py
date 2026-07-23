"""Unit tests for scripts/taco/scoring.py — tiered entry score."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.taco.constants import (
    EventType,
    FUND_SIZE,
    Tier,
    WEIGHT_CROSS_ASSET,
    WEIGHT_DIP,
    WEIGHT_EVENT,
    WEIGHT_TERM,
    WEIGHT_VIX,
)
from scripts.taco.scoring import (
    cross_asset_component,
    dip_component,
    event_component,
    score_entry,
    term_component,
    vix_component,
)


class TestComponents:
    def test_weights_sum_to_100(self):
        assert (WEIGHT_DIP + WEIGHT_VIX + WEIGHT_EVENT + WEIGHT_TERM
                + WEIGHT_CROSS_ASSET) == pytest.approx(100.0)

    def test_dip_linear(self):
        # calibrated scale 1.5% -> 0 pts, 6% -> 30 pts (rebalanced 2026-07-22)
        assert dip_component(1.5) == 0.0
        assert dip_component(6.0) == 30.0
        assert dip_component(3.75) == pytest.approx(15.0)
        assert dip_component(20.0) == 30.0          # clamped

    def test_vix_component_range(self):
        # level 16 -> 0, 30 -> 12.6; spike 1.1 -> 0, 1.6 -> 8.4
        assert vix_component(16.0, 1.0) == 0.0
        assert vix_component(30.0, 1.6) == pytest.approx(21.0)

    def test_term_backwardation_scores(self):
        assert term_component(0.90) == 0.0
        assert term_component(1.10) == pytest.approx(13.0)

    def test_event_scores_ordered(self):
        assert (event_component(EventType.TARIFF_TACO)
                > event_component(EventType.FED_REGIME_SHIFT)
                > event_component(EventType.UNKNOWN))

    def test_cross_asset_points(self):
        assert cross_asset_component(0, 3) == 0.0
        assert cross_asset_component(3, 3) == pytest.approx(15.0)
        assert cross_asset_component(1, 3) == pytest.approx(5.0)
        # degradation: count over available assets, same 15-pt range
        assert cross_asset_component(1, 2) == pytest.approx(7.5)
        assert cross_asset_component(2, 2) == pytest.approx(15.0)


class TestScoreEntry:
    def _panic_kwargs(self):
        return dict(vix_level=35.0, vix_spike_ratio=1.6,
                    vix3m_ratio=1.15, event_type=EventType.GEOPOLITICAL)

    def test_full_panic_call_all_confirm(self):
        sig = score_entry("CALL", drawdown=9.0, cross_asset_count=3,
                          cross_asset_available=3, **self._panic_kwargs())
        # 30 + 21 + 18.5 + 13 + 15
        assert sig.score == pytest.approx(97.5)
        assert sig.components["cross_asset"] == pytest.approx(15.0)
        assert sig.tier == Tier.FULL
        assert sig.sizing_usd == pytest.approx(0.40 * FUND_SIZE)

    def test_call_no_cross_asset_renormalizes(self):
        sig = score_entry("CALL", drawdown=9.0, **self._panic_kwargs())
        # (30 + 21 + 18.5 + 13) * 100/85
        assert sig.score == pytest.approx(82.5 * 100.0 / 85.0)
        assert "cross_asset" not in sig.components
        assert any("cross-asset unavailable" in n.lower() for n in sig.notes)

    def test_call_partial_availability_scales(self):
        sig = score_entry("CALL", drawdown=9.0, cross_asset_count=1,
                          cross_asset_available=2, **self._panic_kwargs())
        assert sig.components["cross_asset"] == pytest.approx(7.5)
        assert any("2 of 3" in n for n in sig.notes)

    def test_zero_confirms_drags_score(self):
        confirmed = score_entry("CALL", drawdown=9.0, cross_asset_count=3,
                                cross_asset_available=3, **self._panic_kwargs())
        divergent = score_entry("CALL", drawdown=9.0, cross_asset_count=0,
                                cross_asset_available=3, **self._panic_kwargs())
        assert confirmed.score - divergent.score == pytest.approx(15.0)

    def test_calm_market_is_skip(self):
        sig = score_entry("CALL", drawdown=0.5, vix_level=15.0,
                          vix_spike_ratio=1.0, vix3m_ratio=0.86,
                          event_type=EventType.UNKNOWN,
                          cross_asset_count=0, cross_asset_available=3)
        assert sig.tier == Tier.SKIP
        assert sig.sizing_usd == 0.0

    def test_put_ignores_cross_asset_in_score(self):
        with_xa = score_entry("PUT", runup_z=3.5, cross_asset_count=3,
                              cross_asset_available=3, **self._panic_kwargs())
        without = score_entry("PUT", runup_z=3.5, **self._panic_kwargs())
        assert with_xa.score == pytest.approx(without.score)
        assert "cross_asset" not in with_xa.components
        # put score = four legacy components renormalized
        assert with_xa.score == pytest.approx((30 + 21 + 18.5 + 13) * 100 / 85)

    def test_put_capped_at_pilot(self):
        sig = score_entry("PUT", runup_z=3.5, **self._panic_kwargs())
        assert sig.tier == Tier.PILOT
        assert any("PILOT" in n for n in sig.notes)

    def test_event_misclassification_one_step_rarely_flips_full_to_skip(self):
        # Audit finding 5 (2026-07-14): +/-1 category must not flip FULL<->SKIP.
        # Max adjacent gap is now GEO(18.5) vs FED(10.1) = 8.4 pts.
        base = dict(drawdown=6.0, vix_level=30.0, vix_spike_ratio=1.4,
                    cross_asset_count=2, cross_asset_available=3)
        hi = score_entry("CALL", event_type=EventType.GEOPOLITICAL,
                         vix3m_ratio=1.10, **base)
        lo = score_entry("CALL", event_type=EventType.FED_REGIME_SHIFT,
                         vix3m_ratio=1.10, **base)
        assert hi.score - lo.score == pytest.approx(8.4)
        assert not (hi.tier == Tier.FULL and lo.tier == Tier.SKIP)

    def test_missing_vix3m_scores_zero_term_with_note(self):
        sig = score_entry("CALL", drawdown=9.0, vix_level=35.0,
                          vix_spike_ratio=1.6, vix3m_ratio=None,
                          event_type=EventType.GEOPOLITICAL,
                          cross_asset_count=3, cross_asset_available=3)
        assert sig.components["term"] == 0.0
        assert any("VIX3M" in n for n in sig.notes)

    def test_invalid_direction_raises(self):
        with pytest.raises(ValueError):
            score_entry("STRADDLE", drawdown=5.0, **self._panic_kwargs())

    def test_call_requires_drawdown(self):
        with pytest.raises(ValueError):
            score_entry("CALL", **self._panic_kwargs())

    def test_put_requires_runup_z(self):
        with pytest.raises(ValueError):
            score_entry("PUT", **self._panic_kwargs())

    def test_put_between_27_and_30_is_skip(self):
        # Recalibrated TIER_PILOT (27) is calls-only; puts keep legacy 30.
        sig = score_entry("PUT", runup_z=1.6, vix_level=19.0,
                          vix_spike_ratio=1.15, vix3m_ratio=0.95,
                          event_type=EventType.ECON_DATA)
        assert 27.0 <= sig.score < 30.0, f"fixture drifted: {sig.score}"
        assert sig.tier == Tier.SKIP

    def test_call_between_27_and_30_is_pilot(self):
        sig = score_entry("CALL", drawdown=2.7, vix_level=19.0,
                          vix_spike_ratio=1.15, vix3m_ratio=0.95,
                          event_type=EventType.ECON_DATA,
                          cross_asset_count=1, cross_asset_available=3)
        assert 27.0 <= sig.score < 30.0, f"fixture drifted: {sig.score}"
        assert sig.tier == Tier.PILOT


class TestFrozenCalibration:
    """Guards the user-approved 2026-07 cross-asset calibration — same
    pattern as the frozen-refs guard in test_taco_exits.py. Changing these
    values requires a new user-approved calibration pass."""

    def test_tier_thresholds_frozen(self):
        from scripts.taco.constants import TIER_FULL, TIER_HALF, TIER_PILOT
        assert (TIER_FULL, TIER_HALF, TIER_PILOT) == (65.0, 42.0, 27.0)

    def test_cross_asset_thresholds_frozen(self):
        from scripts.taco.constants import CROSS_ASSET_THRESHOLDS
        assert CROSS_ASSET_THRESHOLDS == {"TLT": 1.0, "UUP": 0.5, "USO": 3.0}
