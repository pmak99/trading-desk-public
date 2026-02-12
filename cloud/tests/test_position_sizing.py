# cloud/tests/test_position_sizing.py
import pytest
from src.domain.position_sizing import calculate_position_size, half_kelly

def test_half_kelly_formula():
    """Half-Kelly = 0.5 * (bp - q) / b where b=odds, p=win_rate, q=1-p."""
    # Win rate 60%, risk/reward 1:2 (lose $1 to win $2)
    fraction = half_kelly(win_rate=0.60, risk_reward=0.5)
    # Kelly = (0.6 * 2 - 0.4) / 2 = 0.8 / 2 = 0.4
    # Half-Kelly = 0.2
    assert abs(fraction - 0.20) < 0.01

def test_half_kelly_negative_edge():
    """Negative edge returns 0 (don't trade)."""
    fraction = half_kelly(win_rate=0.30, risk_reward=2.0)
    assert fraction == 0.0

def test_calculate_position_size_basic():
    """Calculate contracts based on account and risk."""
    size = calculate_position_size(
        account_value=100000,
        max_risk_per_contract=500,
        win_rate=0.60,
        risk_reward=0.5,
    )
    # Half-Kelly ~0.2, so risk $20k, at $500/contract = 40 contracts
    # But capped at max 5% of account = 10 contracts
    assert size <= 20  # Reasonable cap

def test_calculate_position_size_respects_max():
    """Position size respects maximum percentage."""
    size = calculate_position_size(
        account_value=100000,
        max_risk_per_contract=100,
        win_rate=0.60,
        risk_reward=0.5,
        max_position_pct=0.02,  # 2% max
    )
    # 2% of $100k = $2k risk, at $100/contract = 20 contracts max
    assert size <= 20

def test_calculate_position_size_minimum():
    """Always returns at least 1 if edge exists."""
    size = calculate_position_size(
        account_value=10000,
        max_risk_per_contract=5000,
        win_rate=0.55,
        risk_reward=1.0,
    )
    assert size >= 1
