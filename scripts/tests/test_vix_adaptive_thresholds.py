"""Pure helpers of 2.0/scripts/research/vix_adaptive_thresholds.py.

No database and no Yahoo: load_events is covered by test_research_events.py, and
the VIX download is one call. What matters here is that the as-of VIX close can
never be a lookahead, that the regime table the study tests is the one the
analyzer applied, and that the two comparisons and the verdict do what the
pre-registration says.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "2.0" / "scripts"))

from research import vix_adaptive_thresholds as vt  # noqa: E402


def _event(ed, vrp, crushed, ticker="T"):
    return {"ticker": ticker, "earnings_date": ed, "vrp": vrp, "crushed": crushed}


# --- as-of VIX: strictly before the event, never stale ---------------------

def test_vix_asof_takes_the_last_close_strictly_before_the_event():
    closes = {date(2025, 4, 3): 30.0, date(2025, 4, 4): 45.3, date(2025, 4, 7): 46.9}
    idx = vt.VixIndex(closes)
    assert idx.asof(date(2025, 4, 7)) == (date(2025, 4, 4), 45.3)


def test_vix_asof_never_uses_the_event_day_close():
    """The event day's close is after an AMC entry and after a BMO reaction."""
    idx = vt.VixIndex({date(2025, 4, 7): 46.9})
    assert idx.asof(date(2025, 4, 7)) is None


def test_vix_asof_refuses_a_stale_close():
    last = date(2025, 1, 2)
    idx = vt.VixIndex({last: 17.0})
    assert idx.asof(last + timedelta(days=vt.VIX_MAX_STALENESS_DAYS)) == (last, 17.0)
    assert idx.asof(last + timedelta(days=vt.VIX_MAX_STALENESS_DAYS + 1)) is None


# --- the regime table under test is the analyzer's -------------------------

@pytest.mark.parametrize("vix,factor", [
    (11.9, 1.0), (19.99, 1.0), (20.0, 1.1), (24.9, 1.1), (25.0, 1.2),
    (30.0, 1.3), (35.0, 1.5), (39.99, 1.5), (40.0, None), (82.7, None),
])
def test_factor_for_vix_bands(vix, factor):
    assert vt.factor_for(vix) == factor


def test_pre_registered_bands_sit_on_the_live_regime_boundaries():
    """The multipliers were AdaptiveThresholdCalculator's (checked against it
    when this test was registered; the calculator was removed on the verdict).
    The regime boundaries they hung on are still live."""
    from src.domain.vix_regime import VIX_REGIMES

    floors = {floor for floor, _ in vt.VIX_FACTORS}
    assert {r.lower_bound for r in VIX_REGIMES if r.lower_bound >= 20} == floors - {0.0}
    for regime in VIX_REGIMES:
        if regime.lower_bound < 20:
            assert vt.factor_for(regime.lower_bound) == 1.0, regime.name


def test_base_thresholds_are_the_intraday_claude_md_section_1():
    """This retired study froze BASELINE = "intraday" (its own load_events call),
    so BASE_* stay pinned to the intraday-era §1 ladder (1.2/1.4/1.8) it was
    registered against -- not the live src.domain.vrp_tier constants, which
    moved to close-to-close in the 2026-09 migration and no longer share units
    with this module's VRP inputs."""
    assert (vt.BASE_MARGINAL, vt.BASE_GOOD, vt.BASE_EXCELLENT) == (1.2, 1.4, 1.8)


# --- tiers and demotion ----------------------------------------------------

@pytest.mark.parametrize("vrp,factor,tier", [
    (1.19, 1.0, "SKIP"), (1.2, 1.0, "MARGINAL"), (1.4, 1.0, "GOOD"), (1.8, 1.0, "EXCELLENT"),
    (1.25, 1.1, "SKIP"), (1.35, 1.1, "MARGINAL"), (1.6, 1.2, "MARGINAL"),
    (5.0, None, "SKIP"),
])
def test_tier(vrp, factor, tier):
    assert vt.tier(vrp, factor) == tier


def test_classify_marks_skip_and_full_size_demotions():
    idx = vt.VixIndex({date(2025, 1, 2): 22.0, date(2025, 4, 4): 45.0, date(2025, 6, 2): 14.0})
    events = [
        _event(date(2025, 1, 3), 1.25, 1),   # MARGINAL -> SKIP at 1.1x
        _event(date(2025, 1, 3), 1.35, 1),   # MARGINAL stays MARGINAL
        _event(date(2025, 1, 3), 1.45, 0),   # GOOD -> MARGINAL at 1.1x
        _event(date(2025, 4, 7), 3.00, 1),   # EXCELLENT -> SKIP at VIX 40+
        _event(date(2025, 6, 3), 1.25, 0),   # factor 1.0: nothing moves
        _event(date(2025, 6, 3), 1.10, 1),   # not base-tradeable: out of sample
        _event(date(2025, 9, 1), 2.00, 1),   # no as-of VIX: dropped
    ]
    sample, no_vix = vt.classify(events, idx)

    assert no_vix == 1
    assert [e["vrp"] for e in sample] == [1.25, 1.35, 1.45, 3.00, 1.25]
    assert [e["demoted_skip"] for e in sample] == [True, False, False, True, False]
    assert [e["demoted_to_marginal"] for e in sample] == [False, False, True, False, False]
    assert [e["factor"] for e in sample] == [1.1, 1.1, 1.1, None, 1.0]


# --- statistics --------------------------------------------------------------

def test_vrp_bins_are_the_pre_registered_edges():
    assert vt.vrp_bin(1.2) == 0
    assert vt.vrp_bin(1.39) == 0
    assert vt.vrp_bin(1.4) == 1
    assert vt.vrp_bin(2.39) == 5
    assert vt.vrp_bin(2.4) == 6
    assert vt.vrp_bin(9.0) == 6


def _sample(rows):
    """rows: (asof, vrp, crushed, demoted, control). control = factor 1.0."""
    return [
        {"asof": a, "vrp": v, "crushed": c, "demoted_skip": d,
         "demoted_to_marginal": False, "factor": 1.0 if ctrl else 1.1}
        for a, v, c, d, ctrl in rows
    ]


def test_primary_and_matched_deltas_on_a_hand_example():
    d1, d2, d3 = date(2025, 1, 2), date(2025, 1, 3), date(2025, 6, 2)
    sample = _sample([
        # demoted, bin 0: 1 of 2 crushed
        (d1, 1.25, 1, True, False), (d2, 1.30, 0, True, False),
        # kept at VIX 20-25, bin 3: crushed
        (d1, 1.90, 1, False, False),
        # controls (factor 1.0): bin 0 3 of 4 crushed, bin 3 1 of 1
        (d3, 1.21, 1, False, True), (d3, 1.22, 1, False, True),
        (d3, 1.23, 1, False, True), (d3, 1.24, 0, False, True),
        (d3, 1.95, 1, False, True),
    ])
    agg = vt.aggregate(sample, "demoted_skip")
    stats = vt.statistics(list(agg.values()))

    kept_rate = 5 / 6          # every non-demoted base-tradeable event
    assert stats["treated_n"] == 2
    assert stats["treated_rate"] == pytest.approx(0.5)
    assert stats["kept_rate"] == pytest.approx(kept_rate)
    assert stats["primary_delta"] == pytest.approx(kept_rate - 0.5)
    # demoted events all sit in bin 0, so the matched control is bin 0's rate
    assert stats["matched_control_rate"] == pytest.approx(0.75)
    assert stats["matched_delta"] == pytest.approx(0.25)


def test_matched_control_is_standardised_to_the_treated_bin_mix():
    d1, d3 = date(2025, 1, 2), date(2025, 6, 2)
    sample = _sample([
        (d1, 1.25, 0, True, False),                      # treated, bin 0
        (d1, 1.95, 1, True, False), (d1, 1.96, 1, True, False),  # treated, bin 3
        (d3, 1.21, 0, False, True), (d3, 1.22, 1, False, True),  # control bin 0: 50%
        (d3, 1.91, 1, False, True),                      # control bin 3: 100%
        (d3, 1.92, 1, False, True), (d3, 1.93, 1, False, True),
    ])
    stats = vt.statistics(list(vt.aggregate(sample, "demoted_skip").values()))
    assert stats["matched_control_rate"] == pytest.approx(1 / 3 * 0.5 + 2 / 3 * 1.0)


def test_bootstrap_is_seeded_and_resamples_dates_not_events():
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(30)]
    rows = []
    for i, d in enumerate(days):
        rows.append((d, 1.25, i % 2, True, False))
        rows.append((d, 1.3, 1, False, i % 3 == 0))
        rows.append((d, 1.9, 1, False, True))
    agg = list(vt.aggregate(_sample(rows), "demoted_skip").values())

    a = vt.bootstrap(agg, n_boot=200, seed=7)
    b = vt.bootstrap(agg, n_boot=200, seed=7)
    assert a == b
    assert a["primary"][0] <= a["primary"][1]
    assert a["matched"][0] <= a["matched"][1]
    # one aggregate per as-of date is the resampling unit
    assert len(agg) == len(days)


def test_verdict_needs_enough_demoted_events_and_dates():
    ok = {"treated_n": vt.MIN_DEMOTED_EVENTS, "treated_dates": vt.MIN_DEMOTED_DATES}
    ci = {"primary": (0.01, 0.2), "matched": (0.02, 0.3)}
    assert vt.verdict(ok, ci) == "PASS"
    assert vt.verdict({**ok, "treated_n": vt.MIN_DEMOTED_EVENTS - 1}, ci) == "INSUFFICIENT"
    assert vt.verdict({**ok, "treated_dates": vt.MIN_DEMOTED_DATES - 1}, ci) == "INSUFFICIENT"


def test_verdict_needs_both_lower_bounds_above_zero():
    ok = {"treated_n": 10_000, "treated_dates": 10_000}
    assert vt.verdict(ok, {"primary": (0.01, 0.2), "matched": (-0.01, 0.3)}) == "FAIL"
    assert vt.verdict(ok, {"primary": (-0.01, 0.2), "matched": (0.02, 0.3)}) == "FAIL"
    assert vt.verdict(ok, {"primary": (-0.2, -0.1), "matched": (-0.3, -0.1)}) == "FAIL"
