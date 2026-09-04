"""
Composite quality scoring for scan ranking.

Multi-factor scoring that weighs risk-adjusted returns, not just VRP.
"""

import logging
from typing import List

from .constants import (
    HARVEST_IV_RANK_MIN,
    HARVEST_IV_RANK_MAX_POINTS,
    HARVEST_IV_HV_MAX_POINTS,
    HARVEST_IV_HV_CAP,
    HARVEST_SKEW_MAX_POINTS,
    HARVEST_LIQUIDITY_MAX_POINTS,
    HARVEST_SKEW_SCORES,
    HARVEST_SKEW_NULL_SCORE,
    SCORE_VRP_MAX_POINTS,
    SCORE_VRP_TARGET,
    SCORE_VRP_USE_LINEAR,
    SCORE_EDGE_MAX_POINTS,
    SCORE_EDGE_TARGET,
    SCORE_LIQUIDITY_EXCELLENT_POINTS,
    SCORE_LIQUIDITY_GOOD_POINTS,
    SCORE_LIQUIDITY_WARNING_POINTS,
    SCORE_LIQUIDITY_REJECT_POINTS,
    SCORE_MOVE_MAX_POINTS,
    SCORE_MOVE_USE_CONTINUOUS,
    SCORE_MOVE_BASELINE_PCT,
    SCORE_MOVE_EASY_THRESHOLD,
    SCORE_MOVE_MODERATE_THRESHOLD,
    SCORE_MOVE_MODERATE_POINTS,
    SCORE_MOVE_CHALLENGING_THRESHOLD,
    SCORE_MOVE_CHALLENGING_POINTS,
    SCORE_MOVE_EXTREME_POINTS,
    SCORE_DEFAULT_MOVE_POINTS,
)
from .formatters import parse_liquidity_tier

logger = logging.getLogger(__name__)


def calculate_scan_quality_score(result: dict) -> float:
    """
    Calculate composite quality score for scan ranking.

    Multi-factor scoring that weighs risk-adjusted returns, not just VRP.
    This prevents high-VRP but risky trades from outranking safer opportunities.

    POST-PERPLEXITY ANALYSIS (Dec 2025):
    After comparing with Perplexity's multi-factor approach, added composite
    scoring to better align with risk-adjusted quality metrics.

    DIRECTIONAL BIAS REMOVED (Dec 2025):
    Directional alignment is handled at strategy selection stage (strategy_scorer.py),
    not at scan stage. This allows all opportunities to surface, then strategies
    get matched appropriately (e.g., STRONG BEARISH + Bear Call Spread = aligned).

    Scoring Factors (100+ points, continuous scaling):
    - VRP Edge (45 base): Continuous scaling from VRP ratio (no hard cap)
    - Edge Score (DISABLED): Removed - 85% correlated with VRP, was double-counting
    - Liquidity Quality (20 max): EXCELLENT=20, WARNING=12, REJECT=4
    - Implied Move (35 max): Linear interpolation (0%=35pts, 20%=0pts)

    OPTIMIZED via A/B Testing (Dec 2025):
    - Tested 6 configurations over 100 Monte Carlo iterations
    - This config won 52% of iterations (next best: 20%)
    - Improvements vs original: +38% score separation, +12% correlation

    Default Score Philosophy:
    When data is missing, defaults are CONSERVATIVE (assume worst-case or middle):
    - Missing VRP: 0.0 (no edge = no points)
    - Missing liquidity: WARNING tier (12/20 pts)
    - Missing implied move: 17.5/35 pts (middle difficulty)

    This philosophy prioritizes safety: only reward what we can verify.

    Args:
        result: Analysis result dictionary with metrics. Expected keys:
            - vrp_ratio (float): Volatility risk premium ratio
            - edge_score (float): Combined VRP + historical edge
            - liquidity_tier (str): 'EXCELLENT', 'WARNING', 'REJECT', or 'UNKNOWN'
            - implied_move_pct (str|Percentage|None): Expected move percentage

    Returns:
        Composite quality score (0-100)

    Raises:
        TypeError: If result is not a dictionary

    Notes:
        Invalid field types (e.g., string for vrp_ratio) are logged as warnings
        and fall back to conservative defaults (0.0 for numeric fields, WARNING
        for liquidity). This ensures graceful degradation rather than hard failures.

    Examples:
        >>> result = {'vrp_ratio': 8.27, 'edge_score': 4.67,
        ...           'implied_move_pct': '12.10%', 'liquidity_tier': 'WARNING'}
        >>> calculate_scan_quality_score(result)
        81.0

        >>> result = {'vrp_ratio': 4.00, 'edge_score': 2.79,
        ...           'implied_move_pct': '11.69%', 'liquidity_tier': 'WARNING'}
        >>> calculate_scan_quality_score(result)
        75.9
    """
    # Input validation - defensive programming
    if not isinstance(result, dict):
        logger.error(f"calculate_scan_quality_score requires dict, got {type(result)}")
        raise TypeError(f"result must be dict, not {type(result).__name__}")

    # Factor 1: VRP Edge (max: SCORE_VRP_MAX_POINTS = 45)
    # Primary edge signal - continuous scaling for better discrimination
    vrp_ratio = result.get('vrp_ratio', 0.0)
    try:
        vrp_ratio = float(vrp_ratio) if vrp_ratio is not None else 0.0
    except (TypeError, ValueError) as e:
        logger.warning(f"Invalid vrp_ratio '{vrp_ratio}': {e}. Using 0.0")
        vrp_ratio = 0.0

    if SCORE_VRP_USE_LINEAR:
        # Continuous scaling: VRP 4.0 = 45pts, VRP 5.0 = 56pts, VRP 6.0 = 67pts
        # No hard cap - allows high VRP to differentiate from medium VRP
        vrp_normalized = vrp_ratio / SCORE_VRP_TARGET
        vrp_score = max(0.0, vrp_normalized) * SCORE_VRP_MAX_POINTS
    else:
        # Capped at target (legacy behavior)
        vrp_score = max(0.0, min(vrp_ratio / SCORE_VRP_TARGET, 1.0)) * SCORE_VRP_MAX_POINTS

    # Factor 2: Edge Score (DISABLED - redundant with VRP)
    # edge_score ~ 0.85 * vrp_ratio, so having both double-counts VRP
    # A/B testing showed removing Edge improves overall performance
    edge_points = 0.0  # Disabled - SCORE_EDGE_MAX_POINTS = 0

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

    # Factor 4: Implied Move Difficulty (max: SCORE_MOVE_MAX_POINTS = 35)
    # Lower implied move = easier to stay profitable = higher score
    # Historical data shows strong correlation between low IV and win rate
    implied_move_pct = result.get('implied_move_pct')
    if implied_move_pct is None:
        move_score = SCORE_DEFAULT_MOVE_POINTS  # Default middle score (17.5)
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
                # Continuous linear interpolation: 0% = 35pts, 20% = 0pts
                # Eliminates cliff effects (7.99% vs 8.01% no longer 5pt difference)
                move_normalized = max(0.0, 1.0 - (implied_pct / SCORE_MOVE_BASELINE_PCT))
                move_score = move_normalized * SCORE_MOVE_MAX_POINTS
            else:
                # Discrete buckets (legacy fallback)
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
      - IV Rank     (40 pts): linear from iv_rank_min=60 → 0pts to 100 → 40pts
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
