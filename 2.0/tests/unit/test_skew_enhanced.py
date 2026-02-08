"""
Tests for enhanced skew analyzer (Phase 4).
"""

import pytest
from datetime import date, timedelta
from unittest.mock import Mock

from src.application.metrics.skew_enhanced import SkewAnalyzerEnhanced, SkewAnalysis
from src.domain.types import Money, Percentage, Strike, OptionChain, OptionQuote
from src.domain.errors import ErrorCode


class TestSkewAnalyzerEnhanced:
    """Test polynomial skew fitting."""

    @pytest.fixture
    def provider(self):
        """Mock options data provider."""
        return Mock()

    @pytest.fixture
    def analyzer(self, provider):
        """Create skew analyzer."""
        return SkewAnalyzerEnhanced(provider)

    def create_chain_with_skew(
        self,
        ticker="TEST",
        stock_price=100.0,
        skew_type="normal"  # normal, smile, inverse, flat
    ):
        """
        Create option chain with specific skew pattern.

        Args:
            ticker: Stock ticker
            stock_price: Stock price
            skew_type: Type of skew pattern to generate

        Returns:
            OptionChain with specified skew
        """
        expiration = date(2026, 4, 15)

        # Create strikes from -15% to +15%
        strikes = []
        calls = {}
        puts = {}

        for pct in range(-15, 16, 3):  # -15%, -12%, ..., +12%, +15%
            strike_price = stock_price * (1 + pct / 100.0)
            strike = Strike(strike_price)
            strikes.append(strike)

            # Skip near-ATM strikes (±2%)
            if abs(pct) < 2:
                continue

            # Calculate IVs based on skew type
            moneyness = pct / 100.0  # Distance from ATM

            if skew_type == "normal":
                # Normal skew: puts expensive (positive skew ATM)
                put_iv = 30.0 + moneyness * 10 + moneyness**2 * 5
                call_iv = 30.0 + moneyness * 5 + moneyness**2 * 5

            elif skew_type == "smile":
                # Volatility smile: U-shaped skew (symmetric)
                # Both sides show positive skew (puts expensive relative to calls)
                put_iv = 30.0 + abs(moneyness) * 20 + moneyness**2 * 100
                call_iv = 30.0 + abs(moneyness) * 10 + moneyness**2 * 50

            elif skew_type == "inverse":
                # Inverse smile: both OTM cheap
                put_iv = 30.0 - abs(moneyness) * 5 - moneyness**2 * 10
                call_iv = 30.0 - abs(moneyness) * 5 - moneyness**2 * 10

            else:  # flat
                # Flat skew: no skew
                put_iv = 30.0
                call_iv = 30.0

            # Create quotes
            calls[strike] = OptionQuote(
                bid=Money(strike_price * 0.05),
                ask=Money(strike_price * 0.06),
                implied_volatility=Percentage(call_iv),
                open_interest=1000,
                volume=100
            )

            puts[strike] = OptionQuote(
                bid=Money(strike_price * 0.05),
                ask=Money(strike_price * 0.06),
                implied_volatility=Percentage(put_iv),
                open_interest=1000,
                volume=100
            )

        return OptionChain(
            ticker=ticker,
            expiration=expiration,
            stock_price=Money(stock_price),
            calls=calls,
            puts=puts
        )

    def test_normal_skew_detection(self, analyzer, provider):
        """Test detection of normal volatility skew (puts expensive)."""
        chain = self.create_chain_with_skew(skew_type="normal")

        from src.domain.errors import Ok
        from src.domain.enums import DirectionalBias
        provider.get_option_chain.return_value = Ok(chain)

        result = analyzer.analyze_skew_curve("TEST", chain.expiration)

        assert result.is_ok
        analysis = result.value

        assert isinstance(analysis, SkewAnalysis)
        assert analysis.ticker == "TEST"
        assert analysis.num_points >= analyzer.MIN_POINTS
        assert analysis.strength in ["smile", "smirk", "inverse_smile"]
        # Check that directional_bias is a valid DirectionalBias enum value
        assert isinstance(analysis.directional_bias, DirectionalBias)
        assert 0.0 <= analysis.confidence <= 1.0

    def test_smile_detection(self, analyzer, provider):
        """Test detection of volatility smile (both OTM expensive)."""
        chain = self.create_chain_with_skew(skew_type="smile")

        from src.domain.errors import Ok
        provider.get_option_chain.return_value = Ok(chain)

        result = analyzer.analyze_skew_curve("TEST", chain.expiration)

        assert result.is_ok
        analysis = result.value

        assert analysis.strength == "smile"
        assert analysis.curvature > analyzer.SMILE_THRESHOLD

    def test_flat_skew(self, analyzer, provider):
        """Test detection of flat skew (no smile or skew)."""
        chain = self.create_chain_with_skew(skew_type="flat")

        from src.domain.errors import Ok
        provider.get_option_chain.return_value = Ok(chain)

        result = analyzer.analyze_skew_curve("TEST", chain.expiration)

        assert result.is_ok
        analysis = result.value

        assert analysis.strength == "smirk"  # Low curvature
        assert abs(analysis.curvature) < analyzer.SMILE_THRESHOLD

    def test_insufficient_data_points(self, analyzer, provider):
        """Test error when insufficient data points for fit."""
        # Create chain with very few strikes
        expiration = date(2026, 4, 15)
        chain = OptionChain(
            ticker="TEST",
            expiration=expiration,
            stock_price=Money(100.0),
            calls={},
            puts={}
        )

        from src.domain.errors import Ok
        provider.get_option_chain.return_value = Ok(chain)

        result = analyzer.analyze_skew_curve("TEST", expiration)

        assert result.is_err
        assert result.error.code == ErrorCode.NODATA

    def test_provider_error_propagation(self, analyzer, provider):
        """Test that provider errors are propagated."""
        from src.domain.errors import Err, AppError

        provider.get_option_chain.return_value = Err(
            AppError(ErrorCode.EXTERNAL, "API error")
        )

        result = analyzer.analyze_skew_curve("TEST", date(2026, 4, 15))

        assert result.is_err
        assert result.error.code == ErrorCode.EXTERNAL

    def test_illiquid_options_skipped(self, analyzer, provider):
        """Test that illiquid options are excluded from fit."""
        chain = self.create_chain_with_skew(skew_type="normal")

        # Mark some options as illiquid
        for strike in list(chain.calls.keys())[:3]:
            call = chain.calls[strike]
            # Create new quote with zero volume (illiquid)
            chain.calls[strike] = OptionQuote(
                bid=call.bid,
                ask=call.ask,
                implied_volatility=call.implied_volatility,
                open_interest=0,  # Illiquid
                volume=0
            )

        from src.domain.errors import Ok
        provider.get_option_chain.return_value = Ok(chain)

        result = analyzer.analyze_skew_curve("TEST", chain.expiration)

        # Should still work with remaining liquid options
        if len(chain.calls) - 3 >= analyzer.MIN_POINTS:
            assert result.is_ok
        else:
            assert result.is_err
            assert result.error.code == ErrorCode.NODATA

    def test_confidence_score_range(self, analyzer, provider):
        """Test that confidence score is in valid range."""
        chain = self.create_chain_with_skew(skew_type="normal")

        from src.domain.errors import Ok
        provider.get_option_chain.return_value = Ok(chain)

        result = analyzer.analyze_skew_curve("TEST", chain.expiration)

        assert result.is_ok
        analysis = result.value

        assert 0.0 <= analysis.confidence <= 1.0

    def test_atm_skew_extracted(self, analyzer, provider):
        """Test that ATM skew is correctly extracted from polynomial."""
        chain = self.create_chain_with_skew(skew_type="normal", stock_price=100.0)

        from src.domain.errors import Ok
        provider.get_option_chain.return_value = Ok(chain)

        result = analyzer.analyze_skew_curve("TEST", chain.expiration)

        assert result.is_ok
        analysis = result.value

        # ATM skew should exist
        assert analysis.skew_atm is not None
        assert isinstance(analysis.skew_atm, Percentage)
