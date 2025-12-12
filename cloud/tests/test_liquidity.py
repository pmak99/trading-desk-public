import pytest
from src.domain.liquidity import classify_liquidity_tier

def test_liquidity_excellent():
    """EXCELLENT: OI >= 5x, spread <= 8%."""
    tier = classify_liquidity_tier(oi=1000, spread_pct=5.0, position_size=100)
    assert tier == "EXCELLENT"

def test_liquidity_good():
    """GOOD: OI 2-5x, spread 8-12%."""
    tier = classify_liquidity_tier(oi=300, spread_pct=10.0, position_size=100)
    assert tier == "GOOD"

def test_liquidity_warning():
    """WARNING: OI 1-2x, spread 12-15%."""
    tier = classify_liquidity_tier(oi=150, spread_pct=13.0, position_size=100)
    assert tier == "WARNING"

def test_liquidity_reject_low_oi():
    """REJECT: OI < 1x position."""
    tier = classify_liquidity_tier(oi=50, spread_pct=5.0, position_size=100)
    assert tier == "REJECT"

def test_liquidity_reject_wide_spread():
    """REJECT: spread > 15%."""
    tier = classify_liquidity_tier(oi=1000, spread_pct=20.0, position_size=100)
    assert tier == "REJECT"

def test_liquidity_final_tier_is_worse():
    """Final tier = worse of (OI tier, Spread tier)."""
    # Excellent OI but warning spread -> WARNING
    tier = classify_liquidity_tier(oi=1000, spread_pct=13.0, position_size=100)
    assert tier == "WARNING"
