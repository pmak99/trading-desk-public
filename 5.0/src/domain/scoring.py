"""
Composite scoring system for IV Crush 5.0.

2.0 Score = VRP (55%) + Move Difficulty (25%) + Liquidity (20%)
4.0 Score = 2.0 Score × (1 + Sentiment Modifier)

Sentiment Modifiers:
- Strong Bullish (>= +0.6): +12%
- Bullish (+0.2 to +0.6): +7%
- Neutral (-0.2 to +0.2): 0%
- Bearish (-0.6 to -0.2): -7%
- Strong Bearish (<= -0.6): -12%
"""

from typing import Dict, Any

# Scoring weights
WEIGHT_VRP = 0.55
WEIGHT_MOVE = 0.25
WEIGHT_LIQUIDITY = 0.20

# Liquidity tier scores
LIQUIDITY_SCORES = {
    "EXCELLENT": 100,
    "GOOD": 80,
    "WARNING": 50,
    "REJECT": 20,
}


def calculate_score(
    vrp_ratio: float,
    implied_move_pct: float,
    liquidity_tier: str,
    vrp_tier: str = None,  # Optional, for compatibility
) -> Dict[str, Any]:
    """
    Calculate composite score (0-100).

    Args:
        vrp_ratio: VRP ratio (higher is better)
        implied_move_pct: Implied move percentage
        liquidity_tier: EXCELLENT/GOOD/WARNING/REJECT
        vrp_tier: VRP tier string (optional, for info only)

    Returns:
        Dict with total_score (0-100) and components breakdown
    """
    # VRP score: normalize to 0-100 (7x = 100, 1x = 14)
    vrp_score = min(100, (vrp_ratio / 7.0) * 100)

    # Move difficulty score: easier moves score higher
    # 5% move = 100, 15% move = 33
    move_score = min(100, (5.0 / max(implied_move_pct, 1.0)) * 100)

    # Liquidity score
    liq_score = LIQUIDITY_SCORES.get(liquidity_tier, 20)

    # Weighted composite
    score = (
        vrp_score * WEIGHT_VRP +
        move_score * WEIGHT_MOVE +
        liq_score * WEIGHT_LIQUIDITY
    )

    return {
        "total_score": round(score, 1),
        "components": {
            "vrp": round(vrp_score * WEIGHT_VRP, 1),
            "move_difficulty": round(move_score * WEIGHT_MOVE, 1),
            "liquidity": round(liq_score * WEIGHT_LIQUIDITY, 1),
        },
        "vrp_tier": vrp_tier,
        "liquidity_tier": liquidity_tier,
    }


def apply_sentiment_modifier(
    base_score: float,
    sentiment_score: float,
) -> float:
    """
    Apply sentiment modifier to base score.

    Args:
        base_score: 2.0 composite score
        sentiment_score: -1.0 to +1.0

    Returns:
        Modified score (4.0 score), clamped to 0-100
    """
    # Determine modifier based on sentiment strength
    if sentiment_score >= 0.6:
        modifier = 0.12  # Strong bullish
    elif sentiment_score >= 0.2:
        modifier = 0.07  # Bullish
    elif sentiment_score <= -0.6:
        modifier = -0.12  # Strong bearish
    elif sentiment_score <= -0.2:
        modifier = -0.07  # Bearish
    else:
        modifier = 0.0  # Neutral

    # Clamp result to valid 0-100 range
    modified = base_score * (1 + modifier)
    return round(max(0.0, min(100.0, modified)), 1)
