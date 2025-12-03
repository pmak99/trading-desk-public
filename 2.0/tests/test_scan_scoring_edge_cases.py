#!/usr/bin/env python3
"""
Test edge cases and error handling in composite quality scoring.

Tests defensive programming features added in Dec 2025:
- Input validation (non-dict input raises TypeError)
- Type error handling (dict/list values fall back to defaults)
- Conservative defaults for missing/invalid data
- Graceful degradation for parsing errors
- Negative value clamping (negative values clamped to 0)
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
    result = calculate_scan_quality_score({})
    print(f"Empty dict score: {result}")
    expected = 0 + 0 + 10 + 7.5  # VRP=0, Edge=0, Liquidity=WARNING(10), Move=default(7.5)
    assert result == expected, f"Expected {expected}, got {result}"
    print(f"✅ PASS: Empty dict uses conservative defaults = {expected}")

    # Missing implied move
    result = calculate_scan_quality_score({
        'vrp_ratio': 4.0,
        'edge_score': 3.0,
        'liquidity_tier': 'WARNING'
    })
    print(f"Missing implied_move_pct: {result}")
    # VRP: min(4.0/3.0, 1.0)*35 = 35
    # Edge: min(3.0/4.0, 1.0)*30 = 22.5
    # Liq: 10
    # Move: 7.5 (default)
    # Total: 75.0
    assert result == 75.0, f"Expected 75.0, got {result}"
    print(f"✅ PASS: Missing implied_move uses default middle score (7.5)")

    # Unknown liquidity tier
    result = calculate_scan_quality_score({
        'vrp_ratio': 3.0,
        'edge_score': 4.0,
        'liquidity_tier': 'SOMETHING_WEIRD',
        'implied_move_pct': '10%'
    })
    print(f"Unknown liquidity tier: {result}")
    # VRP: 35, Edge: 30, Liq: 10 (WARNING default), Move: 10
    # Total: 85.0
    assert result == 85.0, f"Expected 85.0, got {result}"
    print(f"✅ PASS: Unknown liquidity uses WARNING default (10 pts)")


def test_malformed_data_handling():
    """Test error handling for malformed data."""
    print("\n" + "="*80)
    print("TEST 3: Malformed Data Handling")
    print("="*80)

    # Invalid VRP (string that can't be converted)
    result = calculate_scan_quality_score({
        'vrp_ratio': 'not_a_number',
        'edge_score': 3.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': '10%'
    })
    print(f"Invalid VRP (string): {result}")
    # VRP: 0 (error), Edge: 22.5, Liq: 10, Move: 10 = 42.5
    assert result == 42.5, f"Expected 42.5, got {result}"
    print(f"✅ PASS: Invalid VRP string defaults to 0.0")

    # Invalid VRP (dict type)
    result = calculate_scan_quality_score({
        'vrp_ratio': {'nested': 'dict'},
        'edge_score': 3.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': '10%'
    })
    print(f"Invalid VRP (dict): {result}")
    assert result == 42.5, f"Expected 42.5, got {result}"
    print(f"✅ PASS: Invalid VRP dict defaults to 0.0")

    # Invalid VRP (list type)
    result = calculate_scan_quality_score({
        'vrp_ratio': [1, 2, 3],
        'edge_score': 3.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': '10%'
    })
    print(f"Invalid VRP (list): {result}")
    assert result == 42.5, f"Expected 42.5, got {result}"
    print(f"✅ PASS: Invalid VRP list defaults to 0.0")

    # Invalid edge score
    result = calculate_scan_quality_score({
        'vrp_ratio': 3.0,
        'edge_score': 'invalid',
        'liquidity_tier': 'WARNING',
        'implied_move_pct': '10%'
    })
    print(f"Invalid edge_score (string): {result}")
    # VRP: 35, Edge: 0 (error), Liq: 10, Move: 10 = 55.0
    assert result == 55.0, f"Expected 55.0, got {result}"
    print(f"✅ PASS: Invalid edge_score defaults to 0.0")

    # Invalid implied move (can't parse)
    result = calculate_scan_quality_score({
        'vrp_ratio': 3.0,
        'edge_score': 4.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': 'garbage%'
    })
    print(f"Invalid implied_move_pct: {result}")
    # VRP: 35, Edge: 30, Liq: 10, Move: 7.5 (error default) = 82.5
    assert result == 82.5, f"Expected 82.5, got {result}"
    print(f"✅ PASS: Invalid implied_move uses default (7.5)")


def test_percentage_object_handling():
    """Test handling of Percentage objects vs strings."""
    print("\n" + "="*80)
    print("TEST 4: Percentage Object Handling")
    print("="*80)

    # Mock Percentage object with .value attribute
    class MockPercentage:
        def __init__(self, value):
            self.value = value

    # Test with Percentage object
    result = calculate_scan_quality_score({
        'vrp_ratio': 3.0,
        'edge_score': 4.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': MockPercentage(7.5)
    })
    print(f"Percentage object (7.5%): {result}")
    # VRP: 35, Edge: 30, Liq: 10, Move: 15 (≤8.0 threshold) = 90.0
    assert result == 90.0, f"Expected 90.0, got {result}"
    print(f"✅ PASS: Percentage object handled correctly")

    # Test with string percentage
    result = calculate_scan_quality_score({
        'vrp_ratio': 3.0,
        'edge_score': 4.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': '7.5%'
    })
    print(f"String percentage ('7.5%'): {result}")
    assert result == 90.0, f"Expected 90.0, got {result}"
    print(f"✅ PASS: String percentage handled correctly")


def test_boundary_conditions():
    """Test boundary conditions for scoring thresholds."""
    print("\n" + "="*80)
    print("TEST 5: Boundary Conditions")
    print("="*80)

    # VRP exactly at target
    result = calculate_scan_quality_score({
        'vrp_ratio': 3.0,
        'edge_score': 4.0,
        'liquidity_tier': 'EXCELLENT',
        'implied_move_pct': '8.0%'
    })
    print(f"VRP at target (3.0x): {result}")
    # VRP: 35, Edge: 30, Liq: 20, Move: 15 = 100.0 (perfect score!)
    assert result == 100.0, f"Expected 100.0, got {result}"
    print(f"✅ PASS: Perfect score achievable (100.0)")

    # Implied move at moderate threshold boundary
    result = calculate_scan_quality_score({
        'vrp_ratio': 3.0,
        'edge_score': 4.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': '12.0%'
    })
    print(f"Implied move at moderate boundary (12.0%): {result}")
    # VRP: 35, Edge: 30, Liq: 10, Move: 10 = 85.0
    assert result == 85.0, f"Expected 85.0, got {result}"
    print(f"✅ PASS: 12.0% gets moderate score (10 pts)")

    # Implied move just above moderate threshold
    result = calculate_scan_quality_score({
        'vrp_ratio': 3.0,
        'edge_score': 4.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': '12.1%'
    })
    print(f"Implied move above moderate (12.1%): {result}")
    # VRP: 35, Edge: 30, Liq: 10, Move: 6 (challenging) = 81.0
    assert result == 81.0, f"Expected 81.0, got {result}"
    print(f"✅ PASS: 12.1% gets challenging score (6 pts)")


def test_extreme_values():
    """Test extreme values don't break scoring."""
    print("\n" + "="*80)
    print("TEST 6: Extreme Values")
    print("="*80)

    # Extremely high VRP
    result = calculate_scan_quality_score({
        'vrp_ratio': 100.0,
        'edge_score': 50.0,
        'liquidity_tier': 'EXCELLENT',
        'implied_move_pct': '5%'
    })
    print(f"Extreme VRP (100x) and edge (50.0): {result}")
    # VRP: 35 (capped), Edge: 30 (capped), Liq: 20, Move: 15 = 100.0
    assert result == 100.0, f"Expected 100.0, got {result}"
    print(f"✅ PASS: Extreme values capped at max (100.0)")

    # Zero values
    result = calculate_scan_quality_score({
        'vrp_ratio': 0.0,
        'edge_score': 0.0,
        'liquidity_tier': 'REJECT',
        'implied_move_pct': '50%'
    })
    print(f"All zeros/minimums: {result}")
    # VRP: 0, Edge: 0, Liq: 0, Move: 3 (extreme) = 3.0
    assert result == 3.0, f"Expected 3.0, got {result}"
    print(f"✅ PASS: Minimum score possible (3.0)")

    # Negative values (should be clamped to 0)
    result = calculate_scan_quality_score({
        'vrp_ratio': -5.0,
        'edge_score': -10.0,
        'liquidity_tier': 'WARNING',
        'implied_move_pct': '10%'
    })
    print(f"Negative VRP/edge: {result}")
    # VRP: max(0, min(-5.0/3.0, 1.0)) * 35 = max(0, -1.67) * 35 = 0
    # Edge: max(0, min(-10.0/4.0, 1.0)) * 30 = max(0, -2.5) * 30 = 0
    # Liq: 10, Move: 10 (10% = moderate) = 20.0
    assert result == 20.0, f"Expected 20.0, got {result}"
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
    print("- Extreme values are handled (negative values clamped to 0)")
    print("="*80 + "\n")
