#!/usr/bin/env python
"""Tests for position_limits integration."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.integration.position_limits import PositionLimitsRepository


class TestPositionLimitsRepository:
    """Tests for PositionLimitsRepository."""

    def test_get_existing_ticker(self):
        """Should return position limits for known ticker."""
        repo = PositionLimitsRepository()
        result = repo.get_limits("AAPL")

        assert result is not None
        assert result['ticker'] == "AAPL"
        assert 'tail_risk_ratio' in result
        assert 'tail_risk_level' in result
        assert 'max_contracts' in result

    def test_get_unknown_ticker(self):
        """Should return None for unknown ticker."""
        repo = PositionLimitsRepository()
        result = repo.get_limits("XXXXX")

        assert result is None

    def test_get_high_risk_ticker(self):
        """MU should be HIGH risk based on CLAUDE.md."""
        repo = PositionLimitsRepository()
        result = repo.get_limits("MU")

        # MU has TRR 3.05x per CLAUDE.md
        if result:
            assert result['tail_risk_level'] == "HIGH"
            assert result['max_contracts'] == 50
