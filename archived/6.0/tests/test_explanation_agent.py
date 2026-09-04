#!/usr/bin/env python3
"""Test ExplanationAgent scenarios."""

import pytest
import sys
from pathlib import Path

# Add 6.0/ to path (not 6.0/src/ to allow "from src.agents..." imports)
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents.explanation import ExplanationAgent


@pytest.fixture
def agent():
    """Create ExplanationAgent instance."""
    return ExplanationAgent()


class TestExplanationAgent:
    """Test cases for ExplanationAgent."""

    def test_high_vrp_with_historical_data(self, agent):
        """Test explanation for high VRP with historical data."""
        result = agent.explain(
            ticker="NVDA",
            vrp_ratio=6.2,
            liquidity_tier="GOOD",
            earnings_date="2026-02-05"
        )

        assert result['ticker'] == 'NVDA'
        assert result['explanation']  # Non-empty explanation
        assert len(result['key_factors']) > 0
        assert result['historical_context']

    def test_high_vrp_no_sentiment(self, agent):
        """Test explanation for high VRP without cached sentiment."""
        result = agent.explain(
            ticker="AAPL",
            vrp_ratio=5.5,
            liquidity_tier="EXCELLENT",
            earnings_date=None  # No earnings date = no sentiment lookup
        )

        assert result['ticker'] == 'AAPL'
        assert result['explanation']
        assert len(result['key_factors']) > 0

    def test_exceptional_vrp(self, agent):
        """Test explanation for exceptional VRP (7x+)."""
        result = agent.explain(
            ticker="TSLA",
            vrp_ratio=7.8,
            liquidity_tier="GOOD",
            earnings_date=None
        )

        assert result['ticker'] == 'TSLA'
        assert result['explanation']
        # Exceptional VRP should be mentioned
        assert '7' in result['explanation'] or 'exceptional' in result['explanation'].lower()

    def test_marginal_vrp(self, agent):
        """Test explanation for marginal VRP (3-4x)."""
        result = agent.explain(
            ticker="JPM",
            vrp_ratio=3.5,
            liquidity_tier="WARNING",
            earnings_date=None
        )

        assert result['ticker'] == 'JPM'
        assert result['explanation']  # Has some explanation
        # VRP ratio should be mentioned
        assert '3.5' in result['explanation']

    def test_unknown_ticker(self, agent):
        """Test explanation handles unknown ticker gracefully."""
        result = agent.explain(
            ticker="FAKESYM",
            vrp_ratio=5.0,
            liquidity_tier="REJECT",
            earnings_date=None
        )

        assert result['ticker'] == 'FAKESYM'
        assert result['explanation']  # Has some explanation
        # VRP ratio should be mentioned
        assert '5.0' in result['explanation']
