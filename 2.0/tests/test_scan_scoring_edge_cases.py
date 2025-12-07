#!/usr/bin/env python3
"""
Test edge cases and error handling in composite quality scoring.

Tests defensive programming features added in Dec 2025:
- Input validation (non-dict input raises TypeError)
- Type error handling (dict/list values fall back to defaults)
- Conservative defaults for missing/invalid data
- Graceful degradation for parsing errors
- Negative value clamping (negative values clamped to 0)

SCORING FORMULA (Updated Dec 2025):
- VRP Score (55 max): (vrp_ratio / 4.0) * 55, continuous scaling (no cap)
- Edge Score (0): Disabled (redundant with VRP)
- Liquidity Score (20 max): EXCELLENT=20, GOOD=16, WARNING=12, REJECT=4
- Move Score (25 max): (1 - implied_pct/20%) * 25, continuous scaling, default=12.5
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.scan import calculate_scan_quality_score


def test_input_validation():
    """Test that non-dict input raises TypeError."""
    print("\n" + "="*80)
    print("TEST 1: Input Validation")
    print("="*80)

    try:
        calculate_scan_quality_score("not a dict")
        print("❌ FAIL: Should have raised TypeError for string input")
    except TypeError as e:
        print(f"✅ PASS: Correctly raised TypeError: {e}")

    try:
        calculate_scan_quality_score(None)
        print("❌ FAIL: Should have raised TypeError for None input")
    except TypeError as e:
        print(f"✅ PASS: Correctly raised TypeError: {e}")

    try:
        calculate_scan_quality_score([1, 2, 3])
        print("❌ FAIL: Should have raised TypeError for list input")
    except TypeError as e:
        print(f"✅ PASS: Correctly raised TypeError: {e}")


def test_missing_data_defaults():
    """Test conservative defaults for missing data."""
    print("\n" + "="*80)
    print("TEST 2: Missing Data Defaults")
    print("="*80)

    # Empty dict - all defaults
    # VRP=0, Liq=WARNING(12), Move=default(12.5)
    result = calculate_scan_quality_score({})
    print(f"Empty dict score: {result}")
    expected = 0 + 12 + 12.5  # 24.5
    assert result == expected, f"Expected {expected}, got {result}"
    print(f"✅ PASS: Empty dict uses conservative defaults = {expected}")

    # Missing implied move
    # VRP: (4.0/4.0)*55 = 55
    # Liq: 12 (WARNING)
    # Move: 12.5 (default)
    # Total: 79.5
    result = calculate_scan_quality_score({
        'vrp_ratio': 4.0,
        'edge_score': 3.0,  # Ignored (disabled)
        'liquidity_tier': 'WARNING'
    })
    print(f"Missing implied_move_pct: {result}")
    assert result == 79.5, f"Expected 79.5, got {result}"
    print(f"✅ PASS: Missing implied_move uses default middle score (12.5)")

    # Unknown liquidity tier → WARNING default
    # VRP: (3.0/4.0)*55 = 41.25
    # Liq: 12 (WARNING - default for unknown)
    # Move: (1 - 10/20)*25 = 12.5
    # Total: 65.8 (rounded)
    result = calculate_scan_quality_score({
        'vrp_ratio': 3.0,
        'edge_score': 4.0,  # Ignored
        'liquidity_tier': 'SOMETHING_WEIRD',
        'implied_move_pct': '10%'
    })
    print(f"Unknown liquidity tier: {result}")
    expected = 41.25 + 12 + 12.5  # 65.75 → 65.8
    assert result == round(expected, 1), f"Expected {round(expected, 1)}, got {result}"
    print(f"✅ PASS: Unknown liquidity uses WARNING default (12 pts)")


def test_malformed_data_handling():
    """Test error handling for malformed data."""
    print("\n" + "="*80)
    print("TEST 3: Malformed Data Handling")
    print("="*80)

    # Invalid VRP (string that can't be converted)
    # VRP: 0 (error fallback)
    # Liq: 12 (WARNING)
    # Move: (1 - 10/20)*25 = 12.5
    # Total: 24.5
    result = calculate_scan_quality_score({
        'vrp_ratio': 'not_a_number',
        'edge_score': 3.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': '10%'
    })
    print(f"Invalid VRP (string): {result}")
    expected = 0 + 12 + 12.5  # 24.5
    assert result == expected, f"Expected {expected}, got {result}"
    print(f"✅ PASS: Invalid VRP string defaults to 0.0")

    # Invalid VRP (dict type)
    result = calculate_scan_quality_score({
        'vrp_ratio': {'nested': 'dict'},
        'edge_score': 3.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': '10%'
    })
    print(f"Invalid VRP (dict): {result}")
    assert result == expected, f"Expected {expected}, got {result}"
    print(f"✅ PASS: Invalid VRP dict defaults to 0.0")

    # Invalid VRP (list type)
    result = calculate_scan_quality_score({
        'vrp_ratio': [1, 2, 3],
        'edge_score': 3.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': '10%'
    })
    print(f"Invalid VRP (list): {result}")
    assert result == expected, f"Expected {expected}, got {result}"
    print(f"✅ PASS: Invalid VRP list defaults to 0.0")

    # Invalid edge score (ignored anyway, but shouldn't crash)
    # VRP: (3.0/4.0)*55 = 41.25
    # Liq: 12 (WARNING)
    # Move: (1 - 10/20)*25 = 12.5
    # Total: 65.8
    result = calculate_scan_quality_score({
        'vrp_ratio': 3.0,
        'edge_score': 'invalid',
        'liquidity_tier': 'WARNING',
        'implied_move_pct': '10%'
    })
    print(f"Invalid edge_score (string): {result}")
    expected = 41.25 + 12 + 12.5  # 65.75 → 65.8
    assert result == round(expected, 1), f"Expected {round(expected, 1)}, got {result}"
    print(f"✅ PASS: Invalid edge_score is ignored (disabled)")

    # Invalid implied move (can't parse) → default 12.5
    # VRP: (3.0/4.0)*55 = 41.25
    # Liq: 12 (WARNING)
    # Move: 12.5 (error default)
    # Total: 65.8
    result = calculate_scan_quality_score({
        'vrp_ratio': 3.0,
        'edge_score': 4.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': 'garbage%'
    })
    print(f"Invalid implied_move_pct: {result}")
    expected = 41.25 + 12 + 12.5  # 65.75 → 65.8
    assert result == round(expected, 1), f"Expected {round(expected, 1)}, got {result}"
    print(f"✅ PASS: Invalid implied_move uses default (12.5)")


def test_percentage_object_handling():
    """Test handling of Percentage objects vs strings."""
    print("\n" + "="*80)
    print("TEST 4: Percentage Object Handling")
    print("="*80)

    # Mock Percentage object with .value attribute
    class MockPercentage:
        def __init__(self, value):
            self.value = value

    # Test with Percentage object (7.5%)
    # VRP: (3.0/4.0)*55 = 41.25
    # Liq: 12 (WARNING)
    # Move: (1 - 7.5/20)*25 = 15.625
    # Total: 68.9
    result = calculate_scan_quality_score({
        'vrp_ratio': 3.0,
        'edge_score': 4.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': MockPercentage(7.5)
    })
    print(f"Percentage object (7.5%): {result}")
    expected = 41.25 + 12 + 15.625  # 68.875 → 68.9
    assert result == round(expected, 1), f"Expected {round(expected, 1)}, got {result}"
    print(f"✅ PASS: Percentage object handled correctly")

    # Test with string percentage
    result = calculate_scan_quality_score({
        'vrp_ratio': 3.0,
        'edge_score': 4.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': '7.5%'
    })
    print(f"String percentage ('7.5%'): {result}")
    assert result == round(expected, 1), f"Expected {round(expected, 1)}, got {result}"
    print(f"✅ PASS: String percentage handled correctly")


def test_boundary_conditions():
    """Test boundary conditions for scoring thresholds."""
    print("\n" + "="*80)
    print("TEST 5: Boundary Conditions")
    print("="*80)

    # Perfect VRP (4x = target), EXCELLENT liquidity, easy move (8%)
    # VRP: (4.0/4.0)*55 = 55
    # Liq: 20 (EXCELLENT)
    # Move: (1 - 8/20)*25 = 15.0
    # Total: 90.0
    result = calculate_scan_quality_score({
        'vrp_ratio': 4.0,
        'edge_score': 4.0,
        'liquidity_tier': 'EXCELLENT',
        'implied_move_pct': '8.0%'
    })
    print(f"Perfect VRP (4.0x), EXCELLENT liq, 8% move: {result}")
    expected = 55 + 20 + 15.0  # 90.0
    assert result == expected, f"Expected {expected}, got {result}"
    print(f"✅ PASS: Good score achievable (90.0)")

    # High VRP exceeds target - continuous scaling
    # VRP: (3.0/4.0)*55 = 41.25
    # Liq: 12 (WARNING)
    # Move: (1 - 12/20)*25 = 10.0
    # Total: 63.3
    result = calculate_scan_quality_score({
        'vrp_ratio': 3.0,
        'edge_score': 4.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': '12.0%'
    })
    print(f"VRP 3.0x, WARNING liq, 12% move: {result}")
    expected = 41.25 + 12 + 10.0  # 63.25 → 63.2
    assert result == round(expected, 1), f"Expected {round(expected, 1)}, got {result}"
    print(f"✅ PASS: 12.0% move gets 10.0 pts")

    # Just above 12% implied move
    # VRP: (3.0/4.0)*55 = 41.25
    # Liq: 12 (WARNING)
    # Move: (1 - 12.1/20)*25 = 9.875
    # Total: 63.1
    result = calculate_scan_quality_score({
        'vrp_ratio': 3.0,
        'edge_score': 4.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': '12.1%'
    })
    print(f"VRP 3.0x, 12.1% move: {result}")
    expected = 41.25 + 12 + (1 - 12.1/20)*25  # 63.1
    assert result == round(expected, 1), f"Expected {round(expected, 1)}, got {result}"
    print(f"✅ PASS: 12.1% gets slightly less than 12.0%")


def test_extreme_values():
    """Test extreme values don't break scoring."""
    print("\n" + "="*80)
    print("TEST 6: Extreme Values")
    print("="*80)

    # Extremely high VRP - no cap in continuous mode!
    # VRP: (100/4.0)*55 = 1375
    # Liq: 20 (EXCELLENT)
    # Move: (1 - 5/20)*25 = 18.75
    # Total: 1413.8 (no cap!)
    result = calculate_scan_quality_score({
        'vrp_ratio': 100.0,
        'edge_score': 50.0,
        'liquidity_tier': 'EXCELLENT',
        'implied_move_pct': '5%'
    })
    print(f"Extreme VRP (100x): {result}")
    expected = (100/4.0)*55 + 20 + (1 - 5/20)*25  # 1413.75 → 1413.8
    assert result == round(expected, 1), f"Expected {round(expected, 1)}, got {result}"
    print(f"✅ PASS: Extreme VRP not capped (continuous scaling)")

    # Zero values - minimum possible
    # VRP: 0
    # Liq: 4 (REJECT)
    # Move: (1 - 50/20)*25 = max(0, -1.5)*25 = 0
    # Total: 4.0
    result = calculate_scan_quality_score({
        'vrp_ratio': 0.0,
        'edge_score': 0.0,
        'liquidity_tier': 'REJECT',
        'implied_move_pct': '50%'
    })
    print(f"All zeros/minimums: {result}")
    expected = 0 + 4 + 0  # 4.0
    assert result == expected, f"Expected {expected}, got {result}"
    print(f"✅ PASS: Minimum score possible (4.0)")

    # Negative values (should be clamped to 0)
    # VRP: max(0, -5.0/4.0)*55 = 0
    # Liq: 12 (WARNING)
    # Move: (1 - 10/20)*25 = 12.5
    # Total: 24.5
    result = calculate_scan_quality_score({
        'vrp_ratio': -5.0,
        'edge_score': -10.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': '10%'
    })
    print(f"Negative VRP/edge: {result}")
    expected = 0 + 12 + 12.5  # 24.5
    assert result == expected, f"Expected {expected}, got {result}"
    print(f"✅ PASS: Negative values clamped to 0 (score = {result})")


if __name__ == "__main__":
    test_input_validation()
    test_missing_data_defaults()
    test_malformed_data_handling()
    test_percentage_object_handling()
    test_boundary_conditions()
    test_extreme_values()

    print("\n" + "="*80)
    print("ALL EDGE CASE TESTS COMPLETED")
    print("="*80)
    print("\nSummary: Defensive programming features working correctly")
    print("- Input validation catches non-dict inputs")
    print("- Missing data uses conservative defaults")
    print("- Malformed data is handled gracefully with fallbacks")
    print("- Percentage objects and strings both supported")
    print("- Boundary conditions work as expected")
    print("- Extreme values handled (no hard cap in continuous mode)")
    print("="*80 + "\n")
