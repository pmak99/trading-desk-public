"""VRP tier: implied move over the historical mean close-to-close move, graded per CLAUDE.md §1.

Every caller imports this module -- the VRP calculator, strategy selection
(through the recommendation it produces), the rationale text, /analyze's
display and the A/B diagnostics -- so the tier a
trade is sized on and the tier every other surface reports cannot drift apart.
Until 2026-09-14 the ladder was written out five times and two copies graded at
2.0/1.5 instead of 1.8/1.4.

No path moves the thresholds: the VIX-regime factor that scaled all three on
/analyze was removed 2026-09-14 after its pre-registered test came back
insufficient (scripts/research/vix_adaptive_thresholds.py).
"""

from src.domain.enums import Recommendation

# Close-to-close VRP (implied move / mean |prev close -> reaction close|) since the
# 2026-09 migration. Mapped from the intraday 1.8 / 1.4 / 1.2 at matched selectivity on
# live first-log events (scripts/research/vrp_close_calibration.py,
# docs/RESEARCH_RESULTS_2026-09.md §7): each admits as many live events as its
# intraday predecessor did. Below 1.0 the straddle prices less than the average
# realized move -- the intraday ladder admitted those too, in units that hid it.
VRP_EXCELLENT_FROM = 0.99
VRP_GOOD_FROM = 0.78
VRP_MARGINAL_FROM = 0.71   # below it: SKIP


def vrp_tier(
    ratio: float,
    excellent: float = VRP_EXCELLENT_FROM,
    good: float = VRP_GOOD_FROM,
    marginal: float = VRP_MARGINAL_FROM,
) -> Recommendation:
    """EXCELLENT from 0.99, GOOD from 0.78, MARGINAL from 0.71, SKIP below."""
    if ratio >= excellent:
        return Recommendation.EXCELLENT
    if ratio >= good:
        return Recommendation.GOOD
    if ratio >= marginal:
        return Recommendation.MARGINAL
    return Recommendation.SKIP
