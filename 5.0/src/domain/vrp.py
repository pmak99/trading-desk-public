"""
VRP (Volatility Risk Premium) Calculator.

Ported from 2.0/src/application/metrics/vrp.py with simplified interface.
"""

from typing import List, Dict, Any
import statistics

# Thresholds - BALANCED mode (matching 2.0 default)
# See CLAUDE.md for threshold explanation
VRP_EXCELLENT = 1.8
VRP_GOOD = 1.4
VRP_MARGINAL = 1.2
MIN_QUARTERS = 4


def get_vrp_tier(vrp_ratio: float) -> str:
    """Get VRP tier from ratio."""
    if vrp_ratio >= VRP_EXCELLENT:
        return "EXCELLENT"
    elif vrp_ratio >= VRP_GOOD:
        return "GOOD"
    elif vrp_ratio >= VRP_MARGINAL:
        return "MARGINAL"
    else:
        return "SKIP"


def calculate_vrp(
    implied_move_pct: float,
    historical_moves: List[float],
) -> Dict[str, Any]:
    """
    Calculate VRP ratio and tier.

    Args:
        implied_move_pct: Implied move from ATM straddle (e.g., 8.5 for 8.5%)
        historical_moves: List of historical move percentages (absolute values)

    Returns:
        Dict with vrp_ratio, tier, historical_mean, consistency, or error
    """
    # Validate data
    if len(historical_moves) < MIN_QUARTERS:
        return {
            "error": "insufficient_data",
            "message": f"Need {MIN_QUARTERS}+ quarters, got {len(historical_moves)}"
        }

    # Calculate mean
    historical_mean = statistics.mean(historical_moves)

    if historical_mean <= 0:
        return {
            "error": "invalid_data",
            "message": f"Invalid historical mean: {historical_mean}"
        }

    # Calculate VRP ratio
    vrp_ratio = implied_move_pct / historical_mean

    # Calculate consistency (MAD)
    median = statistics.median(historical_moves)
    mad = statistics.median([abs(x - median) for x in historical_moves])
    consistency = mad / median if median > 0 else 999

    return {
        "vrp_ratio": round(vrp_ratio, 2),
        "tier": get_vrp_tier(vrp_ratio),
        "implied_move_pct": implied_move_pct,
        "historical_mean": round(historical_mean, 2),
        "consistency": round(consistency, 2),
        "sample_size": len(historical_moves),
    }
