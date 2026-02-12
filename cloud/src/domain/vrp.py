"""
VRP (Volatility Risk Premium) Calculator.

Ported from core/src/application/metrics/vrp.py with simplified interface.
Thresholds imported from common/constants.py.
"""

import sys
from pathlib import Path
from typing import List, Dict, Any
import statistics

# Ensure common/ is importable
_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from common.constants import VRP_EXCELLENT, VRP_GOOD, VRP_MARGINAL, MIN_QUARTERS  # noqa: E402


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
