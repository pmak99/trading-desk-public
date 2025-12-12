"""
Liquidity tier classification.

Ported from 2.0/src/application/metrics/liquidity_scorer.py with simplified interface.

4-Tier System (from CLAUDE.md):
- EXCELLENT: OI >= 5x position, spread <= 8%
- GOOD: OI 2-5x position, spread 8-12%
- WARNING: OI 1-2x position, spread 12-15%
- REJECT: OI < 1x position, spread > 15%

Final tier = worse of (OI tier, Spread tier)
"""

# Spread thresholds
SPREAD_EXCELLENT = 8.0   # <= 8%
SPREAD_GOOD = 12.0       # <= 12%
SPREAD_WARNING = 15.0    # <= 15%
# > 15% = REJECT

TIER_ORDER = {"REJECT": 0, "WARNING": 1, "GOOD": 2, "EXCELLENT": 3}


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
