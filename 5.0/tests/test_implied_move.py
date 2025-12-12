# 5.0/tests/test_implied_move.py
import pytest
from src.domain.implied_move import calculate_implied_move, find_atm_straddle

def test_calculate_implied_move_basic():
    """Implied move = straddle_price / stock_price * 100."""
    result = calculate_implied_move(
        stock_price=100.0,
        call_price=5.0,
        put_price=4.5,
    )
    # Straddle = 5.0 + 4.5 = 9.5
    # Implied move = 9.5 / 100 * 100 = 9.5%
    assert result["implied_move_pct"] == 9.5
    assert result["straddle_price"] == 9.5

def test_calculate_implied_move_with_iv():
    """Include IV in result if provided."""
    result = calculate_implied_move(
        stock_price=100.0,
        call_price=5.0,
        put_price=4.5,
        call_iv=0.45,
        put_iv=0.42,
    )
    assert result["avg_iv"] == pytest.approx(0.435, rel=0.01)

def test_find_atm_straddle():
    """Find ATM options from chain."""
    chain = [
        {"strike": 95.0, "option_type": "call", "bid": 8.0, "ask": 8.5},
        {"strike": 95.0, "option_type": "put", "bid": 2.5, "ask": 3.0},
        {"strike": 100.0, "option_type": "call", "bid": 5.0, "ask": 5.5},
        {"strike": 100.0, "option_type": "put", "bid": 4.0, "ask": 4.5},
        {"strike": 105.0, "option_type": "call", "bid": 2.5, "ask": 3.0},
        {"strike": 105.0, "option_type": "put", "bid": 7.0, "ask": 7.5},
    ]

    call, put = find_atm_straddle(chain, stock_price=101.0)

    assert call["strike"] == 100.0
    assert put["strike"] == 100.0

def test_find_atm_straddle_empty_chain():
    """Empty chain returns None."""
    call, put = find_atm_straddle([], stock_price=100.0)
    assert call is None
    assert put is None
