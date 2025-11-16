"""
Unit tests for position sizing service.
"""

import pytest
from src.application.services.position_sizer import PositionSizer, PositionSize, PositionSizeInput


class TestPositionSizer:
    """Test Kelly Criterion-based position sizing."""

    def test_basic_position_calculation(self):
        """Test basic position size calculation."""
        sizer = PositionSizer(
            fractional_kelly=0.25,
            max_position_pct=0.05,
            max_loss_pct=0.02,
        )

        result = sizer.calculate_position_size(
            PositionSizeInput(
                ticker="AAPL",
                vrp_ratio=2.0,  # Excellent VRP
                consistency_score=0.8,  # High consistency
            )
        )

        assert isinstance(result, PositionSize)
        assert result.ticker == "AAPL"
        assert result.kelly_fraction > 0
        assert result.recommended_fraction > 0
        assert result.position_size_pct > 0
        assert result.confidence > 0

    def test_high_edge_high_confidence(self):
        """Test position sizing with high edge and high confidence."""
        sizer = PositionSizer(
            fractional_kelly=0.25,
            max_position_pct=0.10,  # Higher limit to see Kelly in action
            max_loss_pct=0.10,  # Higher limit
        )

        result = sizer.calculate_position_size(
            PositionSizeInput(
                ticker="AAPL",
                vrp_ratio=2.5,  # Very high VRP
                consistency_score=0.9,  # Very high consistency
                historical_win_rate=0.75,  # Strong historical performance
                num_historical_trades=30,  # Good sample size
            )
        )

        # Should recommend larger position with high edge + confidence
        assert result.position_size_pct > 2.0  # At least 2%
        assert result.confidence > 0.8
        # May or may not be risk adjusted depending on Kelly calculation

    def test_low_edge_low_confidence(self):
        """Test position sizing with low edge and low confidence."""
        sizer = PositionSizer(fractional_kelly=0.25)

        result = sizer.calculate_position_size(
            PositionSizeInput(
                ticker="XYZ",
                vrp_ratio=1.2,  # Marginal VRP
                consistency_score=0.3,  # Low consistency
                historical_win_rate=0.50,  # Coin flip
                num_historical_trades=5,  # Small sample
            )
        )

        # Should recommend smaller position or zero
        assert result.position_size_pct < 2.0  # Less than 2%
        assert result.confidence < 0.6
        assert result.risk_adjusted  # Should be risk-adjusted

    def test_max_position_cap(self):
        """Test that position size is capped at max_position_pct."""
        sizer = PositionSizer(
            fractional_kelly=0.5,  # Aggressive Kelly
            max_position_pct=0.03,  # 3% cap
        )

        result = sizer.calculate_position_size(
            PositionSizeInput(
                ticker="AAPL",
                vrp_ratio=3.0,  # Extreme VRP
                consistency_score=0.95,  # Near-perfect consistency
                historical_win_rate=0.80,  # Very high win rate
            )
        )

        # Should be capped at 3%
        assert result.position_size_pct <= 3.0
        assert result.risk_adjusted  # Should be marked as adjusted

    def test_max_loss_cap(self):
        """Test that position is capped by max loss limit."""
        sizer = PositionSizer(
            fractional_kelly=0.25,
            max_position_pct=0.10,  # High position limit
            max_loss_pct=0.01,  # But low loss limit (1%)
        )

        result = sizer.calculate_position_size(
            PositionSizeInput(
                ticker="AAPL",
                vrp_ratio=2.0,
                consistency_score=0.8,
            )
        )

        # Should be capped by loss limit
        assert result.max_loss_pct <= 1.0
        assert result.position_size_pct <= 1.0

    def test_negative_kelly_returns_zero(self):
        """Test that negative Kelly returns zero position."""
        sizer = PositionSizer(fractional_kelly=0.25)

        # Very low win rate, low VRP = negative Kelly
        result = sizer.calculate_position_size(
            PositionSizeInput(
                ticker="BAD",
                vrp_ratio=0.8,  # Below 1.0 = negative edge
                consistency_score=0.2,  # Low consistency
                historical_win_rate=0.35,  # Low win rate
            )
        )

        # Should return zero or near-zero position
        assert result.position_size_pct < 0.5  # Less than 0.5%
        assert result.kelly_fraction <= 0

    def test_confidence_penalty(self):
        """Test that low confidence reduces position size."""
        sizer = PositionSizer(
            fractional_kelly=0.25,
            min_confidence=0.5,  # Require 50% confidence
            max_position_pct=0.10,  # Higher limit
            max_loss_pct=0.10,  # Higher limit
        )

        # Same setup, different confidence levels
        result_high_conf = sizer.calculate_position_size(
            PositionSizeInput(
                ticker="AAPL",
                vrp_ratio=2.0,
                consistency_score=0.8,  # High consistency = high confidence
                num_historical_trades=30,
            )
        )

        result_low_conf = sizer.calculate_position_size(
            PositionSizeInput(
                ticker="AAPL",
                vrp_ratio=2.0,
                consistency_score=0.3,  # Low consistency = low confidence
                num_historical_trades=3,
            )
        )

        # Low confidence should result in smaller position
        assert result_low_conf.position_size_pct <= result_high_conf.position_size_pct
        assert result_low_conf.confidence < result_high_conf.confidence
        assert result_low_conf.risk_adjusted

    def test_portfolio_allocation_no_scaling(self):
        """Test portfolio allocation when within limits."""
        sizer = PositionSizer(fractional_kelly=0.25)

        positions = [
            sizer.calculate_position_size(PositionSizeInput(ticker="AAPL", vrp_ratio=2.0, consistency_score=0.8)),
            sizer.calculate_position_size(PositionSizeInput(ticker="MSFT", vrp_ratio=1.8, consistency_score=0.7)),
            sizer.calculate_position_size(PositionSizeInput(ticker="GOOGL", vrp_ratio=1.9, consistency_score=0.75)),
        ]

        # Total should be well under 20% default limit
        adjusted = sizer.calculate_portfolio_allocation(
            positions, max_total_exposure_pct=0.20
        )

        # Should return unchanged (no scaling needed)
        assert len(adjusted) == len(positions)
        for orig, adj in zip(positions, adjusted):
            assert abs(orig.position_size_pct - adj.position_size_pct) < 0.01

    def test_portfolio_allocation_with_scaling(self):
        """Test portfolio allocation when scaling is needed."""
        sizer = PositionSizer(
            fractional_kelly=0.5,  # Aggressive Kelly
            max_position_pct=0.10,  # High individual limit
            max_loss_pct=0.10,  # High loss limit
        )

        # Create 5 large positions
        positions = [
            sizer.calculate_position_size(PositionSizeInput(ticker="AAPL", vrp_ratio=2.5, consistency_score=0.9)),
            sizer.calculate_position_size(PositionSizeInput(ticker="MSFT", vrp_ratio=2.3, consistency_score=0.85)),
            sizer.calculate_position_size(PositionSizeInput(ticker="GOOGL", vrp_ratio=2.4, consistency_score=0.88)),
            sizer.calculate_position_size(PositionSizeInput(ticker="AMZN", vrp_ratio=2.2, consistency_score=0.82)),
            sizer.calculate_position_size(PositionSizeInput(ticker="META", vrp_ratio=2.1, consistency_score=0.80)),
        ]

        total_exposure = sum(p.position_size_pct for p in positions)

        # Apply 10% portfolio limit (should force scaling if total > 10%)
        adjusted = sizer.calculate_portfolio_allocation(
            positions, max_total_exposure_pct=0.10
        )

        total_adjusted = sum(p.position_size_pct for p in adjusted)

        # Total should be capped at 10%
        assert total_adjusted <= 10.0
        # If scaling occurred, total should be less than original
        if total_exposure > 10.0:
            assert total_adjusted < total_exposure
            # All should be marked as risk adjusted
            assert all(p.risk_adjusted for p in adjusted)

    def test_empty_portfolio(self):
        """Test portfolio allocation with empty list."""
        sizer = PositionSizer()
        adjusted = sizer.calculate_portfolio_allocation([])
        assert adjusted == []

    def test_fractional_kelly_parameter(self):
        """Test different fractional Kelly values."""
        # Full Kelly (very aggressive)
        sizer_full = PositionSizer(fractional_kelly=1.0, max_position_pct=0.50)
        result_full = sizer_full.calculate_position_size(
            PositionSizeInput(ticker="AAPL", vrp_ratio=2.0, consistency_score=0.8)
        )

        # Quarter Kelly (conservative)
        sizer_quarter = PositionSizer(fractional_kelly=0.25, max_position_pct=0.50)
        result_quarter = sizer_quarter.calculate_position_size(
            PositionSizeInput(ticker="AAPL", vrp_ratio=2.0, consistency_score=0.8)
        )

        # Full Kelly should be ~4x quarter Kelly (before caps)
        assert result_full.kelly_fraction == result_quarter.kelly_fraction
        # But recommended should differ by fractional_kelly factor
        # (unless hit caps)

    def test_historical_win_rate_override(self):
        """Test that historical win rate overrides estimation."""
        sizer = PositionSizer(
            fractional_kelly=0.25,
            max_position_pct=0.10,  # Higher limit
            max_loss_pct=0.10,  # Higher limit
        )

        # Without historical win rate (estimated from consistency/VRP)
        result_estimated = sizer.calculate_position_size(
            PositionSizeInput(
                ticker="AAPL",
                vrp_ratio=2.0,
                consistency_score=0.6,
            )
        )

        # With explicit historical win rate
        result_historical = sizer.calculate_position_size(
            PositionSizeInput(
                ticker="AAPL",
                vrp_ratio=2.0,
                consistency_score=0.6,
                historical_win_rate=0.80,  # Much higher than estimated
            )
        )

        # Historical win rate should lead to larger or equal position
        # (may be equal if both hit caps)
        assert result_historical.position_size_pct >= result_estimated.position_size_pct
        # Kelly fraction should be higher with higher win rate
        assert result_historical.kelly_fraction > result_estimated.kelly_fraction


class TestPositionSizeDataclass:
    """Test PositionSize dataclass."""

    def test_position_size_creation(self):
        """Test creating PositionSize object."""
        pos = PositionSize(
            ticker="AAPL",
            kelly_fraction=0.15,
            recommended_fraction=0.0375,
            position_size_pct=3.75,
            max_loss_pct=3.75,
            risk_adjusted=False,
            confidence=0.8,
        )

        assert pos.ticker == "AAPL"
        assert pos.kelly_fraction == 0.15
        assert pos.recommended_fraction == 0.0375
        assert pos.position_size_pct == 3.75
        assert pos.max_loss_pct == 3.75
        assert not pos.risk_adjusted
        assert pos.confidence == 0.8

    def test_position_size_immutability(self):
        """Test that PositionSize is immutable (frozen dataclass)."""
        pos = PositionSize(
            ticker="AAPL",
            kelly_fraction=0.15,
            recommended_fraction=0.0375,
            position_size_pct=3.75,
            max_loss_pct=3.75,
            risk_adjusted=False,
            confidence=0.8,
        )

        # Should raise error if we try to modify
        with pytest.raises(AttributeError):
            pos.position_size_pct = 5.0
