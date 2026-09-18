#!/usr/bin/env python3
"""
Pin the live scan quality score, scripts/scan/quality_scorer.py.

Inputs are the 2025-12-01 whisper-scan rows for MRVL, OKTA, CRWD, ASAN and AEO.
Every expected figure comes from calling calculate_scan_quality_score itself.
This file used to carry its own capped, bucketed copy of the formula; the copy
had drifted from the live scorer and only printed, so nothing noticed.

The VRP term is linear and uncapped (vrp / 2.16 * 55), so the score is not
bounded at 100. A pre-registered study on 5,948 events (2026-09-13) kept it that
way: capping VRP at 3.0, 3.6 or 4.0, or taking its log, lowered top-20% crush
(that study ran in intraday units; the shape it supports carries over to the
close-to-close target).
"""

import doctest

import pytest

import scripts.scan.quality_scorer as quality_scorer_module
from scripts.scan import (
    LIQUIDITY_PRIORITY_ORDER,
    SCORE_EDGE_MAX_POINTS,
    SCORE_EDGE_TARGET,
    SCORE_LIQUIDITY_EXCELLENT_POINTS,
    SCORE_LIQUIDITY_GOOD_POINTS,
    SCORE_LIQUIDITY_MAX_POINTS,
    SCORE_LIQUIDITY_REJECT_POINTS,
    SCORE_LIQUIDITY_WARNING_POINTS,
    SCORE_MOVE_BASELINE_PCT,
    SCORE_MOVE_CHALLENGING_POINTS,
    SCORE_MOVE_CHALLENGING_THRESHOLD,
    SCORE_MOVE_EASY_THRESHOLD,
    SCORE_MOVE_EXTREME_POINTS,
    SCORE_MOVE_MAX_POINTS,
    SCORE_MOVE_MODERATE_POINTS,
    SCORE_MOVE_MODERATE_THRESHOLD,
    SCORE_VRP_MAX_POINTS,
    SCORE_VRP_TARGET,
    _precalculate_quality_scores,
    calculate_scan_quality_score,
)

# Real rows from the 2025-12-01 whisper-mode scan, vrp_ratio rescaled from
# intraday to close-to-close by SCORE_VRP_TARGET's own ratio (2.16/4.0 = 0.54)
# so each ticker keeps its normalized VRP (vrp_ratio / SCORE_VRP_TARGET) and its
# score. edge_score and directional_bias are kept as scanned; the scorer reads
# neither.
MRVL = {'ticker': 'MRVL', 'vrp_ratio': 2.16, 'edge_score': 2.79,
        'implied_move_pct': '11.69%', 'liquidity_tier': 'WARNING',
        'directional_bias': 'NEUTRAL'}
OKTA = {'ticker': 'OKTA', 'vrp_ratio': 4.47, 'edge_score': 4.67,
        'implied_move_pct': '12.10%', 'liquidity_tier': 'WARNING',
        'directional_bias': 'STRONG BEARISH'}
CRWD = {'ticker': 'CRWD', 'vrp_ratio': 2.26, 'edge_score': 3.14,
        'implied_move_pct': '7.15%', 'liquidity_tier': 'WARNING',
        'directional_bias': 'STRONG BEARISH'}
ASAN = {'ticker': 'ASAN', 'vrp_ratio': 2.62, 'edge_score': 2.86,
        'implied_move_pct': '14.83%', 'liquidity_tier': 'REJECT',
        'directional_bias': 'STRONG BEARISH'}
AEO = {'ticker': 'AEO', 'vrp_ratio': 2.44, 'edge_score': 2.52,
       'implied_move_pct': '13.96%', 'liquidity_tier': 'REJECT',
       'directional_bias': 'STRONG BEARISH'}
WHISPER_ROWS = (MRVL, OKTA, CRWD, ASAN, AEO)


def breakdown_score(data):
    """Split a live score into its VRP, liquidity and implied-move points.

    Built only from calls to calculate_scan_quality_score: a VRP of zero drops
    the VRP term, an implied move at SCORE_MOVE_BASELINE_PCT drops the move
    term, and a row with both dropped scores the liquidity term alone. Each
    call rounds to 0.1, so a component can carry ~0.1 of rounding.
    """
    total = calculate_scan_quality_score(data)
    no_move = f"{SCORE_MOVE_BASELINE_PCT}%"
    vrp_pts = total - calculate_scan_quality_score({**data, 'vrp_ratio': 0.0})
    move_pts = total - calculate_scan_quality_score({**data, 'implied_move_pct': no_move})
    liq_pts = calculate_scan_quality_score(
        {**data, 'vrp_ratio': 0.0, 'implied_move_pct': no_move}
    )
    return vrp_pts, liq_pts, move_pts


def test_docstring_example_inputs_pin_live_scores():
    """The two inputs quoted in calculate_scan_quality_score's docstring.

    The docstring once promised 81.0 and 75.9 for these; the live scorer
    returns 135.7 and 77.4.
    """
    assert calculate_scan_quality_score(OKTA) == 135.7
    assert calculate_scan_quality_score(MRVL) == 77.4


def test_docstring_examples_run_against_live_scorer():
    """Run the module's doctests, so its examples cannot drift again."""
    result = doctest.testmod(quality_scorer_module, verbose=False)
    assert result.attempted >= 2
    assert result.failed == 0


def test_whisper_scan_scores_and_ranking():
    """Pin all five scores and the ranking they produce.

    There is no directional penalty at scan stage: OKTA's STRONG BEARISH bias
    does not stop its 4.47x (close-to-close) VRP ranking first. Direction is
    matched at strategy selection.
    """
    scores = {d['ticker']: calculate_scan_quality_score(d) for d in WHISPER_ROWS}
    assert scores == {'OKTA': 135.7, 'CRWD': 85.6, 'MRVL': 77.4,
                      'ASAN': 77.2, 'AEO': 73.7}
    ranking = sorted(scores, key=lambda t: -scores[t])
    assert ranking == ['OKTA', 'CRWD', 'MRVL', 'ASAN', 'AEO']


def test_score_is_not_capped_at_100():
    """VRP is linear and uncapped, so the total runs past 100."""
    assert calculate_scan_quality_score(OKTA) > 100

    base = {'implied_move_pct': '10%', 'liquidity_tier': 'WARNING'}
    at_1x = calculate_scan_quality_score({**base, 'vrp_ratio': SCORE_VRP_TARGET})
    at_2x = calculate_scan_quality_score({**base, 'vrp_ratio': 2 * SCORE_VRP_TARGET})
    at_3x = calculate_scan_quality_score({**base, 'vrp_ratio': 3 * SCORE_VRP_TARGET})
    # Each further multiple of the VRP target adds the full VRP weight again.
    assert at_2x - at_1x == pytest.approx(SCORE_VRP_MAX_POINTS)
    assert at_3x - at_2x == pytest.approx(SCORE_VRP_MAX_POINTS)

    # The best bounded factors with VRP exactly at target make 100 ...
    best = {'implied_move_pct': '0%', 'liquidity_tier': 'EXCELLENT'}
    assert calculate_scan_quality_score({**best, 'vrp_ratio': SCORE_VRP_TARGET}) == 100.0
    # ... and any VRP above the target goes past it.
    assert calculate_scan_quality_score({**best, 'vrp_ratio': 5.0}) > 100.0


def test_component_breakdown():
    """Components taken from the live scorer add up to its total."""
    for data in WHISPER_ROWS:
        vrp_pts, liq_pts, move_pts = breakdown_score(data)
        total = calculate_scan_quality_score(data)
        assert vrp_pts + liq_pts + move_pts == pytest.approx(total, abs=0.15)

        implied = float(data['implied_move_pct'].rstrip('%'))
        assert vrp_pts == pytest.approx(
            data['vrp_ratio'] / SCORE_VRP_TARGET * SCORE_VRP_MAX_POINTS, abs=0.15)
        assert move_pts == pytest.approx(
            (1 - implied / SCORE_MOVE_BASELINE_PCT) * SCORE_MOVE_MAX_POINTS, abs=0.15)

    # OKTA's VRP term alone is past the nominal 55-point weight.
    okta_vrp, okta_liq, _ = breakdown_score(OKTA)
    assert okta_vrp > SCORE_VRP_MAX_POINTS
    assert okta_liq == SCORE_LIQUIDITY_WARNING_POINTS
    assert breakdown_score(ASAN)[1] == SCORE_LIQUIDITY_REJECT_POINTS


def test_helper_function():
    """Test _precalculate_quality_scores helper function."""
    print("\n" + "=" * 80)
    print("TEST: Helper Function _precalculate_quality_scores()")
    print("=" * 80)

    # Create test data
    results = [
        {'vrp_ratio': 4.0, 'edge_score': 3.0, 'liquidity_tier': 'WARNING', 'implied_move_pct': '10%'},
        {'vrp_ratio': 8.0, 'edge_score': 4.5, 'liquidity_tier': 'EXCELLENT', 'implied_move_pct': '7%'},
        {'vrp_ratio': 2.0, 'edge_score': 1.5, 'liquidity_tier': 'REJECT', 'implied_move_pct': '15%'},
    ]

    # Call helper function
    _precalculate_quality_scores(results)

    # Verify _quality_score field was added
    print("\n✓ Checking helper function added '_quality_score' field:")
    for i, r in enumerate(results, 1):
        assert '_quality_score' in r, f"Result {i} missing '_quality_score' field"
        score = r['_quality_score']
        print(f"  Result {i}: score = {score:.1f}")

        # Verify score matches direct calculation
        direct_score = calculate_scan_quality_score(r)
        assert score == direct_score, f"Cached score {score} != direct score {direct_score}"
        print(f"    ✓ Matches direct calculation: {direct_score:.1f}")

    print("\n✅ PASS: Helper function works correctly")
    print("  - Adds '_quality_score' field to all results")
    print("  - Cached scores match direct calculation")
    print("  - Performance optimization: O(n) pre-calc vs O(n log n) in sort")


def test_liquidity_priority_order():
    """Test LIQUIDITY_PRIORITY_ORDER constant with 4-tier system."""
    print("\n" + "=" * 80)
    print("TEST: LIQUIDITY_PRIORITY_ORDER Constant (4-Tier)")
    print("=" * 80)

    # Verify constant structure
    print(f"\nLIQUIDITY_PRIORITY_ORDER = {LIQUIDITY_PRIORITY_ORDER}")

    # Check expected keys (4-tier: EXCELLENT, GOOD, WARNING, REJECT + UNKNOWN)
    expected_keys = {'EXCELLENT', 'GOOD', 'WARNING', 'REJECT', 'UNKNOWN'}
    actual_keys = set(LIQUIDITY_PRIORITY_ORDER.keys())
    assert actual_keys == expected_keys, f"Expected keys {expected_keys}, got {actual_keys}"
    print(f"✓ All expected keys present: {expected_keys}")

    # Check ordering (lower number = higher priority)
    assert LIQUIDITY_PRIORITY_ORDER['EXCELLENT'] == 0, "EXCELLENT should be priority 0"
    assert LIQUIDITY_PRIORITY_ORDER['GOOD'] == 1, "GOOD should be priority 1"
    assert LIQUIDITY_PRIORITY_ORDER['WARNING'] == 2, "WARNING should be priority 2"
    assert LIQUIDITY_PRIORITY_ORDER['REJECT'] == 3, "REJECT should be priority 3"
    assert LIQUIDITY_PRIORITY_ORDER['UNKNOWN'] == 4, "UNKNOWN should be priority 4"
    print("✓ Priorities correctly ordered: EXCELLENT(0) > GOOD(1) > WARNING(2) > REJECT(3) > UNKNOWN(4)")

    # Test sorting with priority
    test_data = [
        {'ticker': 'A', '_quality_score': 80.0, 'liquidity_tier': 'REJECT'},
        {'ticker': 'B', '_quality_score': 80.0, 'liquidity_tier': 'EXCELLENT'},
        {'ticker': 'C', '_quality_score': 80.0, 'liquidity_tier': 'WARNING'},
        {'ticker': 'D', '_quality_score': 80.0, 'liquidity_tier': 'UNKNOWN'},
        {'ticker': 'E', '_quality_score': 80.0, 'liquidity_tier': 'GOOD'},
    ]

    sorted_data = sorted(test_data, key=lambda x: (
        -x['_quality_score'],
        LIQUIDITY_PRIORITY_ORDER.get(x['liquidity_tier'], 4)
    ))

    sorted_tickers = [d['ticker'] for d in sorted_data]
    expected_order = ['B', 'E', 'C', 'A', 'D']  # EXCELLENT, GOOD, WARNING, REJECT, UNKNOWN
    assert sorted_tickers == expected_order, f"Expected {expected_order}, got {sorted_tickers}"
    print(f"✓ Sorting works correctly: {' > '.join(sorted_tickers)}")

    print("\n✅ PASS: LIQUIDITY_PRIORITY_ORDER constant working correctly")


def test_constants_consistency():
    """Test that all scoring constants are consistent (4-tier system)."""
    print("\n" + "=" * 80)
    print("TEST: Scoring Constants Consistency (4-Tier)")
    print("=" * 80)

    # The nominal weights sum to 100. That is the score at VRP == target with
    # the best liquidity and a 0% move -- not a ceiling: VRP is uncapped (see
    # test_score_is_not_capped_at_100).
    total_max = (SCORE_VRP_MAX_POINTS + SCORE_EDGE_MAX_POINTS +
                 SCORE_LIQUIDITY_EXCELLENT_POINTS + SCORE_MOVE_MAX_POINTS)
    print(f"\nTotal nominal points: {total_max}")
    assert total_max == 100, f"Nominal weights should sum to 100, got {total_max}"
    print(f"✓ Nominal weights sum to 100: {SCORE_VRP_MAX_POINTS} + {SCORE_EDGE_MAX_POINTS} + {SCORE_LIQUIDITY_EXCELLENT_POINTS} + {SCORE_MOVE_MAX_POINTS} = 100")

    # Verify targets are positive (prevent division by zero)
    assert SCORE_VRP_TARGET > 0, "VRP target must be > 0"
    assert SCORE_EDGE_TARGET > 0, "Edge target must be > 0"
    print(f"✓ Targets are positive: VRP={SCORE_VRP_TARGET}, Edge={SCORE_EDGE_TARGET}")

    # Verify 4-tier liquidity tier consistency
    assert SCORE_LIQUIDITY_EXCELLENT_POINTS == SCORE_LIQUIDITY_MAX_POINTS, "EXCELLENT should equal MAX"
    assert SCORE_LIQUIDITY_GOOD_POINTS == 16, "GOOD should be 16 points"
    assert SCORE_LIQUIDITY_WARNING_POINTS == 12, "WARNING should be 12 points"
    assert SCORE_LIQUIDITY_REJECT_POINTS == 4, "REJECT should be 4 points (not zero!)"
    print(f"✓ 4-Tier liquidity: EXCELLENT={SCORE_LIQUIDITY_EXCELLENT_POINTS}, GOOD={SCORE_LIQUIDITY_GOOD_POINTS}, WARNING={SCORE_LIQUIDITY_WARNING_POINTS}, REJECT={SCORE_LIQUIDITY_REJECT_POINTS}")

    # Verify tier ordering
    assert SCORE_LIQUIDITY_EXCELLENT_POINTS > SCORE_LIQUIDITY_GOOD_POINTS, "EXCELLENT > GOOD"
    assert SCORE_LIQUIDITY_GOOD_POINTS > SCORE_LIQUIDITY_WARNING_POINTS, "GOOD > WARNING"
    assert SCORE_LIQUIDITY_WARNING_POINTS > SCORE_LIQUIDITY_REJECT_POINTS, "WARNING > REJECT"
    assert SCORE_LIQUIDITY_REJECT_POINTS > 0, "REJECT > 0 (some REJECT trades win!)"
    print("✓ Tier ordering correct: EXCELLENT > GOOD > WARNING > REJECT > 0")

    # Verify move thresholds are ordered
    assert SCORE_MOVE_EASY_THRESHOLD < SCORE_MOVE_MODERATE_THRESHOLD, "Easy < Moderate"
    assert SCORE_MOVE_MODERATE_THRESHOLD < SCORE_MOVE_CHALLENGING_THRESHOLD, "Moderate < Challenging"
    print(f"✓ Move thresholds ordered: {SCORE_MOVE_EASY_THRESHOLD}% < {SCORE_MOVE_MODERATE_THRESHOLD}% < {SCORE_MOVE_CHALLENGING_THRESHOLD}%")

    # Verify move scores decrease with difficulty
    assert SCORE_MOVE_MAX_POINTS > SCORE_MOVE_MODERATE_POINTS, "Easy > Moderate"
    assert SCORE_MOVE_MODERATE_POINTS > SCORE_MOVE_CHALLENGING_POINTS, "Moderate > Challenging"
    assert SCORE_MOVE_CHALLENGING_POINTS > SCORE_MOVE_EXTREME_POINTS, "Challenging > Extreme"
    print(f"✓ Move scores decrease: {SCORE_MOVE_MAX_POINTS} > {SCORE_MOVE_MODERATE_POINTS} > {SCORE_MOVE_CHALLENGING_POINTS} > {SCORE_MOVE_EXTREME_POINTS}")

    print("\n✅ PASS: All constants are consistent and valid")


def test_vrp_target_defaults_to_the_live_target():
    assert calculate_scan_quality_score(MRVL) == calculate_scan_quality_score(
        MRVL, vrp_target=SCORE_VRP_TARGET)


def test_vrp_target_reads_vrp_against_another_scale():
    """Research and calibration score history against a frozen target (D13)."""
    row = {'vrp_ratio': 2.0, 'implied_move_pct': '8.00%', 'liquidity_tier': 'WARNING'}
    no_vrp = calculate_scan_quality_score({**row, 'vrp_ratio': 0.0})
    at_4 = calculate_scan_quality_score(row, vrp_target=4.0)
    at_2 = calculate_scan_quality_score(row, vrp_target=2.0)
    assert at_4 - no_vrp == pytest.approx(27.5, abs=0.1)
    assert at_2 - no_vrp == pytest.approx(55.0, abs=0.1)
