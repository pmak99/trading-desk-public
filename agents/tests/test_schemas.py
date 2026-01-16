#!/usr/bin/env python
"""Unit tests for Pydantic schemas."""

import sys
from pathlib import Path

# Add 6.0/ to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.utils.schemas import PositionLimits


class TestPositionLimits:
    """Tests for PositionLimits schema."""

    def test_valid_high_trr(self):
        """HIGH TRR should have reduced limits."""
        limits = PositionLimits(
            ticker="MU",
            tail_risk_ratio=3.05,
            tail_risk_level="HIGH",
            max_contracts=50,
            max_notional=25000.0,
            avg_move=3.68,
            max_move=11.21
        )
        assert limits.tail_risk_level == "HIGH"
        assert limits.max_contracts == 50

    def test_valid_normal_trr(self):
        """NORMAL TRR should have standard limits."""
        limits = PositionLimits(
            ticker="AAPL",
            tail_risk_ratio=1.66,
            tail_risk_level="NORMAL",
            max_contracts=100,
            max_notional=50000.0,
            avg_move=2.15,
            max_move=3.57
        )
        assert limits.tail_risk_level == "NORMAL"
        assert limits.max_contracts == 100

    def test_invalid_tail_risk_level(self):
        """Invalid tail risk level should raise error."""
        with pytest.raises(ValueError, match="Invalid tail_risk_level"):
            PositionLimits(
                ticker="TEST",
                tail_risk_ratio=1.5,
                tail_risk_level="INVALID",
                max_contracts=100,
                max_notional=50000.0,
                avg_move=2.0,
                max_move=3.0
            )
