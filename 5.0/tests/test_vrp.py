import pytest
from src.domain.vrp import calculate_vrp, get_vrp_tier

def test_calculate_vrp_basic():
    """VRP = implied_move / historical_mean."""
    result = calculate_vrp(
        implied_move_pct=8.0,
        historical_moves=[4.0, 5.0, 3.0, 4.0]  # mean = 4.0
    )
    assert result["vrp_ratio"] == 2.0
    assert result["historical_mean"] == 4.0

def test_calculate_vrp_excellent():
    """VRP >= 7.0 is EXCELLENT tier."""
    result = calculate_vrp(
        implied_move_pct=14.0,
        historical_moves=[2.0, 2.0, 2.0, 2.0]  # mean = 2.0, VRP = 7.0
    )
    assert result["tier"] == "EXCELLENT"

def test_calculate_vrp_good():
    """VRP >= 4.0 is GOOD tier."""
    result = calculate_vrp(
        implied_move_pct=8.0,
        historical_moves=[2.0, 2.0, 2.0, 2.0]  # mean = 2.0, VRP = 4.0
    )
    assert result["tier"] == "GOOD"

def test_calculate_vrp_marginal():
    """VRP >= 1.5 is MARGINAL tier."""
    result = calculate_vrp(
        implied_move_pct=3.0,
        historical_moves=[2.0, 2.0, 2.0, 2.0]  # mean = 2.0, VRP = 1.5
    )
    assert result["tier"] == "MARGINAL"

def test_calculate_vrp_skip():
    """VRP < 1.5 is SKIP tier."""
    result = calculate_vrp(
        implied_move_pct=2.0,
        historical_moves=[2.0, 2.0, 2.0, 2.0]  # mean = 2.0, VRP = 1.0
    )
    assert result["tier"] == "SKIP"

def test_calculate_vrp_insufficient_data():
    """Need at least 4 quarters of data."""
    result = calculate_vrp(
        implied_move_pct=8.0,
        historical_moves=[4.0, 5.0]  # Only 2 quarters
    )
    assert result["error"] == "insufficient_data"

def test_get_vrp_tier():
    """get_vrp_tier returns correct tier for ratio."""
    assert get_vrp_tier(7.5) == "EXCELLENT"
    assert get_vrp_tier(5.0) == "GOOD"
    assert get_vrp_tier(2.0) == "MARGINAL"
    assert get_vrp_tier(1.0) == "SKIP"
