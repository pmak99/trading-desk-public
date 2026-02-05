# 5.0/tests/test_strategies.py
import pytest
from src.domain.strategies import generate_strategies, Strategy

def test_generate_bull_put_spread():
    """Bullish direction generates bull put spread."""
    strategies = generate_strategies(
        ticker="NVDA",
        price=135.0,
        implied_move_pct=8.0,
        direction="BULLISH",
        liquidity_tier="EXCELLENT"
    )

    bull_put = next((s for s in strategies if s.name == "Bull Put Spread"), None)
    assert bull_put is not None
    assert bull_put.short_strike < 135.0  # Below current price
    assert bull_put.long_strike < bull_put.short_strike
    assert bull_put.max_profit > 0
    assert bull_put.pop >= 60  # Probability of profit

def test_generate_bear_call_spread():
    """Bearish direction generates bear call spread."""
    strategies = generate_strategies(
        ticker="NVDA",
        price=135.0,
        implied_move_pct=8.0,
        direction="BEARISH",
        liquidity_tier="EXCELLENT"
    )

    bear_call = next((s for s in strategies if s.name == "Bear Call Spread"), None)
    assert bear_call is not None
    assert bear_call.short_strike > 135.0  # Above current price

def test_generate_iron_condor_neutral():
    """Neutral direction generates iron condor."""
    strategies = generate_strategies(
        ticker="NVDA",
        price=135.0,
        implied_move_pct=8.0,
        direction="NEUTRAL",
        liquidity_tier="EXCELLENT"
    )

    ic = next((s for s in strategies if s.name == "Iron Condor"), None)
    assert ic is not None

def test_reject_liquidity_no_strategies():
    """REJECT liquidity still generates strategies (relaxed Feb 2026)."""
    strategies = generate_strategies(
        ticker="NVDA",
        price=135.0,
        implied_move_pct=8.0,
        direction="BULLISH",
        liquidity_tier="REJECT"
    )
    # REJECT now allowed but penalized in scoring
    assert len(strategies) > 0

def test_strategy_has_required_fields():
    """Strategy has all required fields."""
    strategies = generate_strategies(
        ticker="NVDA",
        price=135.0,
        implied_move_pct=8.0,
        direction="BULLISH",
        liquidity_tier="GOOD"
    )

    assert len(strategies) > 0
    s = strategies[0]
    assert hasattr(s, 'name')
    assert hasattr(s, 'max_profit')
    assert hasattr(s, 'max_risk')
    assert hasattr(s, 'pop')
    assert hasattr(s, 'description')
