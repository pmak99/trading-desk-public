# 5.0/tests/test_skew.py
"""Tests for skew analysis module."""
import pytest
from src.domain.skew import (
    analyze_skew,
    DirectionalBias,
    SkewAnalysis,
    THRESHOLD_NEUTRAL,
    THRESHOLD_WEAK,
    THRESHOLD_STRONG,
)


def make_option(strike: float, opt_type: str, iv: float) -> dict:
    """Create a mock option with greeks."""
    return {
        "strike": strike,
        "option_type": opt_type,
        "greeks": {"mid_iv": iv},
        "bid": 1.0,
        "ask": 1.2,
        "open_interest": 1000,
    }


def make_chain(stock_price: float, slope_bias: float = 0.0) -> list:
    """
    Create a mock options chain with configurable skew slope.

    The skew (put_iv - call_iv) varies linearly with moneyness.

    slope_bias > 0: skew increases with strike (bullish - calls expensive at higher strikes)
    slope_bias < 0: skew decreases with strike (bearish - puts expensive at lower strikes)
    slope_bias = 0: flat skew (neutral)

    The slope is in IV percentage points per moneyness unit.
    A slope of 0.5 means 10% moneyness change → 5% IV skew change.
    """
    chain = []
    base_iv = 0.30  # 30% base IV
    base_skew = 0.02  # 2% base put-call skew

    # Create 8 strikes from -14% to +14% of stock price (skipping ATM ±2%)
    # This ensures we have enough points after filtering
    for pct in [-0.14, -0.10, -0.06, -0.03, 0.03, 0.06, 0.10, 0.14]:
        strike = round(stock_price * (1 + pct), 2)
        moneyness = pct

        # Skew = base + slope * moneyness
        # slope_bias of 0.5 means 10% moneyness → 5% skew change
        skew = base_skew + (slope_bias * moneyness)

        # Set IVs so that put_iv - call_iv = skew
        # Clamp IVs to reasonable range to avoid negative values
        call_iv = max(0.10, base_iv)
        put_iv = max(0.10, base_iv + skew)

        chain.append(make_option(strike, "put", put_iv))
        chain.append(make_option(strike, "call", call_iv))

    return chain


class TestAnalyzeSkew:
    """Tests for analyze_skew function."""

    def test_returns_none_for_empty_chain(self):
        """Empty chain returns None."""
        result = analyze_skew("AAPL", 150.0, [])
        assert result is None

    def test_returns_none_for_zero_price(self):
        """Zero stock price returns None."""
        chain = make_chain(150.0)
        result = analyze_skew("AAPL", 0, chain)
        assert result is None

    def test_returns_none_for_insufficient_points(self):
        """Chain with too few valid points returns None."""
        # Only 2 strikes = 2 points (need 5)
        chain = [
            make_option(145.0, "put", 0.35),
            make_option(145.0, "call", 0.30),
            make_option(155.0, "put", 0.32),
            make_option(155.0, "call", 0.31),
        ]
        result = analyze_skew("AAPL", 150.0, chain)
        assert result is None

    def test_neutral_skew_detection(self):
        """Flat skew produces neutral bias."""
        # Zero slope = no directional bias
        chain = make_chain(150.0, slope_bias=0.0)
        result = analyze_skew("AAPL", 150.0, chain)

        assert result is not None
        assert result.directional_bias == DirectionalBias.NEUTRAL

    def test_bearish_skew_detection(self):
        """Negative slope (puts expensive at lower strikes) = bearish."""
        # Negative slope = bearish protection demand
        # Thresholds: NEUTRAL <= 30, WEAK <= 80, STRONG > 150
        # slope_bias of -100 produces slope ≈ -100, exceeding WEAK threshold
        chain = make_chain(150.0, slope_bias=-100)
        result = analyze_skew("AAPL", 150.0, chain)

        assert result is not None
        assert result.directional_bias.is_bearish()

    def test_bullish_skew_detection(self):
        """Positive slope (calls expensive at higher strikes) = bullish."""
        # Positive slope = bullish speculation
        # slope_bias of 100 produces slope ≈ 100, exceeding WEAK threshold
        chain = make_chain(150.0, slope_bias=100)
        result = analyze_skew("AAPL", 150.0, chain)

        assert result is not None
        assert result.directional_bias.is_bullish()

    def test_skew_analysis_has_all_fields(self):
        """Result has all required fields."""
        chain = make_chain(150.0, slope_bias=0.5)
        result = analyze_skew("AAPL", 150.0, chain)

        assert result is not None
        assert result.ticker == "AAPL"
        assert isinstance(result.directional_bias, DirectionalBias)
        assert isinstance(result.slope, float)
        assert 0 <= result.confidence <= 1
        assert result.num_points >= 5


class TestDirectionalBias:
    """Tests for DirectionalBias enum."""

    def test_bullish_variants(self):
        """All bullish variants report as bullish."""
        assert DirectionalBias.STRONG_BULLISH.is_bullish()
        assert DirectionalBias.BULLISH.is_bullish()
        assert DirectionalBias.WEAK_BULLISH.is_bullish()

    def test_bearish_variants(self):
        """All bearish variants report as bearish."""
        assert DirectionalBias.STRONG_BEARISH.is_bearish()
        assert DirectionalBias.BEARISH.is_bearish()
        assert DirectionalBias.WEAK_BEARISH.is_bearish()

    def test_neutral_not_directional(self):
        """Neutral is neither bullish nor bearish."""
        assert not DirectionalBias.NEUTRAL.is_bullish()
        assert not DirectionalBias.NEUTRAL.is_bearish()
