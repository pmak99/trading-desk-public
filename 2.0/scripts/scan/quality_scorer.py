"""
Composite quality scoring for scan ranking.

calculate_scan_quality_score ranks scan and whisper results on VRP, liquidity
and implied-move size; calculate_harvest_score ranks 45 DTE sleeve candidates.
"""

import logging
from typing import List, Optional

from .constants import (
    HARVEST_IV_HV_CAP,
    HARVEST_IV_HV_MAX_POINTS,
    HARVEST_IV_RANK_MAX_POINTS,
    HARVEST_IV_RANK_MIN,
    HARVEST_SKEW_NULL_SCORE,
    HARVEST_SKEW_SCORES,
    SCORE_DEFAULT_MOVE_POINTS,
    SCORE_LIQUIDITY_EXCELLENT_POINTS,
    SCORE_LIQUIDITY_GOOD_POINTS,
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
    SCORE_MOVE_USE_CONTINUOUS,
    SCORE_VRP_MAX_POINTS,
    SCORE_VRP_TARGET,
    SCORE_VRP_USE_LINEAR,
)
from .formatters import parse_liquidity_tier

logger = logging.getLogger(__name__)


def calculate_scan_quality_score(result: dict, vrp_target: Optional[float] = None) -> float:
    """
    Composite quality score used to rank scan and whisper results.

    score = VRP points + liquidity points + implied-move points, rounded to 0.1.
    Weights live in constants.py (SCORE_*):

    - VRP: vrp_ratio / SCORE_VRP_TARGET (2.16) * SCORE_VRP_MAX_POINTS (55),
      linear and uncapped (SCORE_VRP_USE_LINEAR). VRP 1.08 = 27.5, 2.16 = 55,
      4.32 = 110. Negative VRP scores 0.
    - Liquidity: EXCELLENT 20, GOOD 16, WARNING 12, REJECT 4. A trailing
      market-closed "*" is stripped first; an unknown or missing tier scores
      as WARNING.
    - Implied move: (1 - implied_move_pct / SCORE_MOVE_BASELINE_PCT (20)) *
      SCORE_MOVE_MAX_POINTS (25), floored at 0 (SCORE_MOVE_USE_CONTINUOUS).
      0% = 25, 10% = 12.5, 20% or more = 0.
    - edge_score is not scored. It is vrp_ratio / (1 + consistency)
      (src/application/metrics/vrp.py), a rescaled VRP, so adding it would
      count VRP twice.
    - Directional bias is not scored here, nor by the strategy scorer
      (src/domain/scoring/strategy_scorer.py, since 2026-09-14); it only
      decides which spreads strategy/bias.py builds.

    The score is NOT bounded at 100. Liquidity and implied move together give
    4 to 45 points; VRP has no ceiling, so VRP 4.47 alone is worth 113.8 and a
    total of 100 means nothing special. It is a ranking key, not a percentage.

    Why the VRP term stays linear and uncapped: a pre-registered study on 5,948
    historical events (2026-09-13) found that capping VRP at 3.0, 3.6 or 4.0
    (intraday units), or scoring log(VRP) instead, each lowered the top-20%
    crush rate.

    Provenance of the 55/20/25 weights: set by hand on 2025-12-06, raising VRP
    from 45 and cutting implied move from 35 so that ORCL (3.87x VRP, intraday
    units) would outrank COST (2.54x, intraday units). The Monte Carlo A/B
    cited for the earlier 45/35 split
    (scoring_ab_test.py, since deleted) generated its VRP, liquidity and
    implied-move inputs from each trade's win/loss outcome, so it is not
    evidence for either split.

    Missing or unparseable data:
    - vrp_ratio missing or None: 0.0 (no VRP points); non-numeric: 0.0 and a
      warning is logged
    - liquidity_tier key missing or tier unrecognised: WARNING (12). A None
      value is not handled and raises AttributeError.
    - implied_move_pct missing or None: SCORE_DEFAULT_MOVE_POINTS (12.5, the
      midpoint of 25); unparseable: the same, and a warning is logged

    Args:
        result: Analysis result dictionary. Keys read:
            - vrp_ratio (float): implied move / mean historical move
            - liquidity_tier (str): 'EXCELLENT', 'GOOD', 'WARNING', 'REJECT'
              or 'UNKNOWN', optionally suffixed with '*'
            - implied_move_pct (str|Percentage|float|None): e.g. '11.69%'
            Any other key (edge_score, directional_bias, ...) is ignored.
        vrp_target: VRP that earns SCORE_VRP_MAX_POINTS; None = SCORE_VRP_TARGET.
            Research scores history against the target it was run with.

    Returns:
        Composite quality score rounded to 0.1: at least 4.0 (REJECT, no VRP,
        implied move of 20% or more), with no upper bound.

    Raises:
        TypeError: If result is not a dictionary

    Examples:
        >>> result = {'vrp_ratio': 4.47, 'edge_score': 4.67,
        ...           'implied_move_pct': '12.10%', 'liquidity_tier': 'WARNING'}
        >>> calculate_scan_quality_score(result)
        135.7

        >>> result = {'vrp_ratio': 2.16, 'edge_score': 2.79,
        ...           'implied_move_pct': '11.69%', 'liquidity_tier': 'WARNING'}
        >>> calculate_scan_quality_score(result)
        77.4
    """
    # Input validation - defensive programming
    if not isinstance(result, dict):
        logger.error(f"calculate_scan_quality_score requires dict, got {type(result)}")
        raise TypeError(f"result must be dict, not {type(result).__name__}")

    target = SCORE_VRP_TARGET if vrp_target is None else vrp_target

    # Factor 1: VRP (SCORE_VRP_MAX_POINTS = 55 at SCORE_VRP_TARGET = 2.16; no ceiling)
    vrp_ratio = result.get('vrp_ratio', 0.0)
    try:
        vrp_ratio = float(vrp_ratio) if vrp_ratio is not None else 0.0
    except (TypeError, ValueError) as e:
        logger.warning(f"Invalid vrp_ratio '{vrp_ratio}': {e}. Using 0.0")
        vrp_ratio = 0.0

    if SCORE_VRP_USE_LINEAR:
        # Linear, uncapped: VRP 2.16 = 55pts, 2.7 = 68.75pts, 4.32 = 110pts.
        # Kept uncapped by the 2026-09-13 study (intraday units; the shape
        # carries over): caps at 3.0/3.6/4.0 and a log term all lowered
        # top-20% crush.
        vrp_normalized = vrp_ratio / target
        vrp_score = max(0.0, vrp_normalized) * SCORE_VRP_MAX_POINTS
    else:
        # Capped at target (legacy; not live, SCORE_VRP_USE_LINEAR is True)
        vrp_score = max(0.0, min(vrp_ratio / target, 1.0)) * SCORE_VRP_MAX_POINTS

    # Factor 2: edge_score is not scored (SCORE_EDGE_MAX_POINTS = 0).
    # edge_score = vrp_ratio / (1 + consistency), a rescaled VRP, so scoring
    # it as well would count VRP twice.
    edge_points = 0.0

    # Factor 3: Liquidity Quality (max: SCORE_LIQUIDITY_MAX_POINTS = 20)
    # 4-Tier System: EXCELLENT (20pts), GOOD (16pts), WARNING (12pts), REJECT (4pts)
    # Strip market-closed indicator (*) to get base tier for scoring
    liquidity_tier_raw = result.get('liquidity_tier', 'UNKNOWN')
    base_tier, _ = parse_liquidity_tier(liquidity_tier_raw)
    if base_tier == 'EXCELLENT':
        liquidity_score = SCORE_LIQUIDITY_EXCELLENT_POINTS  # 20 (>=5x OI, <=8% spread)
    elif base_tier == 'GOOD':
        liquidity_score = SCORE_LIQUIDITY_GOOD_POINTS       # 16 (2-5x OI, 8-12% spread)
    elif base_tier == 'WARNING':
        liquidity_score = SCORE_LIQUIDITY_WARNING_POINTS    # 12 (1-2x OI, 12-15% spread)
    elif base_tier == 'REJECT':
        liquidity_score = SCORE_LIQUIDITY_REJECT_POINTS     # 4 (<1x OI, >15% spread)
    else:
        # Unknown = assume WARNING (conservative default)
        liquidity_score = SCORE_LIQUIDITY_WARNING_POINTS

    # Factor 4: Implied move size (max: SCORE_MOVE_MAX_POINTS = 25)
    # A smaller implied move scores higher; 0 points at SCORE_MOVE_BASELINE_PCT (20%) or more.
    implied_move_pct = result.get('implied_move_pct')
    if implied_move_pct is None:
        move_score = SCORE_DEFAULT_MOVE_POINTS  # Missing: midpoint, 12.5 of 25
    else:
        try:
            # Extract percentage value (handles both Percentage objects and strings)
            if hasattr(implied_move_pct, 'value'):
                implied_pct = implied_move_pct.value
            else:
                # Parse string like "11.69%"
                implied_str = str(implied_move_pct).rstrip('%')
                implied_pct = float(implied_str)

            if SCORE_MOVE_USE_CONTINUOUS:
                # Linear: 0% = 25pts, 10% = 12.5pts, 20% or more = 0pts
                move_normalized = max(0.0, 1.0 - (implied_pct / SCORE_MOVE_BASELINE_PCT))
                move_score = move_normalized * SCORE_MOVE_MAX_POINTS
            else:
                # Discrete buckets (legacy; not live, SCORE_MOVE_USE_CONTINUOUS is True)
                if implied_pct <= SCORE_MOVE_EASY_THRESHOLD:
                    move_score = SCORE_MOVE_MAX_POINTS
                elif implied_pct <= SCORE_MOVE_MODERATE_THRESHOLD:
                    move_score = SCORE_MOVE_MODERATE_POINTS
                elif implied_pct <= SCORE_MOVE_CHALLENGING_THRESHOLD:
                    move_score = SCORE_MOVE_CHALLENGING_POINTS
                else:
                    move_score = SCORE_MOVE_EXTREME_POINTS
        except (TypeError, ValueError, AttributeError) as e:
            logger.warning(
                f"Failed to parse implied_move_pct '{implied_move_pct}': {e}. "
                f"Using default {SCORE_DEFAULT_MOVE_POINTS}"
            )
            move_score = SCORE_DEFAULT_MOVE_POINTS

    # Calculate total score (no directional penalty - handled at strategy stage)
    total = vrp_score + edge_points + liquidity_score + move_score

    return round(total, 1)


def _rslp30_to_skew_label(r_slp_30) -> str | None:
    """Map raw r_slp_30 float to skew label string. Returns None when input is None."""
    if r_slp_30 is None:
        return None
    try:
        v = float(r_slp_30)
    except (TypeError, ValueError):
        return None
    if v < -0.905:
        return 'STRONG_BEARISH'
    if v < 0.065:
        return 'BEARISH'
    if v < 0.712:
        return 'WEAK_BEARISH'
    if v < 1.359:
        return 'NEUTRAL'
    if v < 2.006:
        return 'WEAK_BULLISH'
    if v < 2.976:
        return 'BULLISH'
    return 'STRONG_BULLISH'


def calculate_harvest_score(candidate: dict) -> float:
    """
    Composite harvest score for 30-45 DTE non-earnings premium candidates (0-100).

    Factors:
      - IV Rank     (40 pts): linear from HARVEST_IV_RANK_MIN (25) → 0pts to 100 → 40pts
      - IV/HV ratio (25 pts): linear from 1.0 → 0pts to 2.0+ → 25pts (capped)
      - Skew        (15 pts): label-based from HARVEST_SKEW_SCORES; NULL → 8pts
      - Liquidity   (20 pts): max_contracts proxy (≥100 → 20, ≥50 → 12, <50 → 6)

    Args:
        candidate: Dict with keys iv_rank_1y, iv_hv_ratio, r_slp_30, max_contracts.
                   Missing keys fall back to conservative defaults (0 pts).

    Returns:
        Score in [0.0, 100.0], rounded to 1 decimal.

    Raises:
        TypeError: If candidate is not a dict.
    """
    if not isinstance(candidate, dict):
        raise TypeError(f"candidate must be dict, not {type(candidate).__name__}")

    # Factor 1: IV Rank (max 40 pts)
    iv_rank = candidate.get('iv_rank_1y', 0.0) or 0.0
    try:
        iv_rank = float(iv_rank)
    except (TypeError, ValueError):
        iv_rank = 0.0
    iv_rank_pts = max(0.0, (iv_rank - HARVEST_IV_RANK_MIN) / (100.0 - HARVEST_IV_RANK_MIN)) * HARVEST_IV_RANK_MAX_POINTS

    # Factor 2: IV/HV ratio (max 25 pts, capped at 2.0x)
    iv_hv = candidate.get('iv_hv_ratio', 1.0) or 1.0
    try:
        iv_hv = float(iv_hv)
    except (TypeError, ValueError):
        iv_hv = 1.0
    iv_hv_capped = min(iv_hv, HARVEST_IV_HV_CAP)
    iv_hv_pts = max(0.0, (iv_hv_capped - 1.0) / (HARVEST_IV_HV_CAP - 1.0)) * HARVEST_IV_HV_MAX_POINTS

    # Factor 3: Skew (max 15 pts)
    skew_label = _rslp30_to_skew_label(candidate.get('r_slp_30'))
    skew_pts = HARVEST_SKEW_SCORES.get(skew_label, HARVEST_SKEW_NULL_SCORE) if skew_label else HARVEST_SKEW_NULL_SCORE

    # Factor 4: Liquidity proxy via max_contracts (max 20 pts)
    max_contracts = candidate.get('max_contracts', 0) or 0
    try:
        max_contracts = int(max_contracts)
    except (TypeError, ValueError):
        max_contracts = 0
    if max_contracts >= 100:
        liq_pts = 20.0
    elif max_contracts >= 50:
        liq_pts = 12.0
    else:
        liq_pts = 6.0

    return round(iv_rank_pts + iv_hv_pts + skew_pts + liq_pts, 1)


def _precalculate_quality_scores(tradeable_results: List[dict]) -> None:
    """
    Pre-calculate quality scores for all tradeable results.

    Performance optimization (Dec 2025): Calculates scores once and caches
    in '_quality_score' field, avoiding O(n log n) recalculations during
    sorting and n recalculations during display (~82% savings).

    Modifies results in-place by adding '_quality_score' field. The leading
    underscore indicates this is an internal/temporary field used only for
    sorting and display within the scan module.

    Args:
        tradeable_results: List of result dictionaries to score

    Returns:
        None (modifies input list in-place)
    """
    for result in tradeable_results:
        result['_quality_score'] = calculate_scan_quality_score(result)
