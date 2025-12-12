import pytest
from src.domain.scoring import calculate_score, apply_sentiment_modifier

def test_calculate_score_weights():
    """Score = VRP(55%) + Move(25%) + Liquidity(20%)."""
    result = calculate_score(
        vrp_ratio=4.0,       # VRP score normalized
        implied_move_pct=5.0,
        liquidity_tier="EXCELLENT"
    )
    assert "total_score" in result
    assert "components" in result
    score = result["total_score"]
    assert 0 <= score <= 100

def test_calculate_score_excellent_vrp():
    """EXCELLENT VRP should score high."""
    result = calculate_score(
        vrp_ratio=7.5,
        implied_move_pct=5.0,
        liquidity_tier="EXCELLENT"
    )
    score = result["total_score"]
    assert score >= 80

def test_calculate_score_reject_liquidity():
    """REJECT liquidity should score lower than EXCELLENT liquidity."""
    reject_result = calculate_score(
        vrp_ratio=7.5,
        implied_move_pct=5.0,
        liquidity_tier="REJECT"
    )
    excellent_result = calculate_score(
        vrp_ratio=7.5,
        implied_move_pct=5.0,
        liquidity_tier="EXCELLENT"
    )
    # REJECT scores lower due to 20 vs 100 liquidity component
    assert reject_result["total_score"] < excellent_result["total_score"]
    assert reject_result["total_score"] < 90  # Penalized from perfect score

def test_apply_sentiment_modifier_bullish():
    """Strong bullish adds +12%."""
    modified = apply_sentiment_modifier(80, sentiment_score=0.8)
    assert modified == 89.6  # 80 * 1.12

def test_apply_sentiment_modifier_bearish():
    """Strong bearish subtracts -12%."""
    modified = apply_sentiment_modifier(80, sentiment_score=-0.8)
    assert modified == 70.4  # 80 * 0.88

def test_apply_sentiment_modifier_neutral():
    """Neutral sentiment has no effect."""
    modified = apply_sentiment_modifier(80, sentiment_score=0.0)
    assert modified == 80.0
