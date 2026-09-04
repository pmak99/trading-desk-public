"""
Liquidity tier classification.

Ported from 2.0/src/application/metrics/liquidity_scorer.py with simplified interface.
Thresholds imported from common/constants.py.

4-Tier System (RELAXED Feb 2026):
- EXCELLENT: OI >= 5x position, spread <= 12%
- GOOD: OI 2-5x position, spread 12-18%
- WARNING: OI 1-2x position, spread 18-25%
- REJECT: OI < 1x position, spread > 25%

Final tier = worse of (OI tier, Spread tier)
"""

import sys
from pathlib import Path

# Ensure common/ is importable
_root = str(Path(__file__).resolve().parent.parent.parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

from common.constants import (  # noqa: E402
    SPREAD_EXCELLENT,
    SPREAD_GOOD,
    SPREAD_WARNING,
    TIER_ORDER,
)


def classify_liquidity_tier(
    oi: int,
    spread_pct: float,
    position_size: int = 100,
) -> str:
    """
    Classify liquidity into 4-tier system.

    Args:
        oi: Open interest
        spread_pct: Bid-ask spread as percentage of mid
        position_size: Expected position size in contracts

    Returns:
        "EXCELLENT", "GOOD", "WARNING", or "REJECT"
    """
    # OI tier (relative to position size)
    oi_ratio = oi / position_size if position_size > 0 else 0

    if oi_ratio >= 5:
        oi_tier = "EXCELLENT"
    elif oi_ratio >= 2:
        oi_tier = "GOOD"
    elif oi_ratio >= 1:
        oi_tier = "WARNING"
    else:
        oi_tier = "REJECT"

    # Spread tier
    if spread_pct <= SPREAD_EXCELLENT:
        spread_tier = "EXCELLENT"
    elif spread_pct <= SPREAD_GOOD:
        spread_tier = "GOOD"
    elif spread_pct <= SPREAD_WARNING:
        spread_tier = "WARNING"
    else:
        spread_tier = "REJECT"

    # Final tier is the worse of the two
    return min([oi_tier, spread_tier], key=lambda t: TIER_ORDER[t])
