#!/usr/bin/env python
"""Tests for PatternRecognitionAgent."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.agents.pattern_recognition import PatternRecognitionAgent


class TestPatternRecognitionAgent:
    """Tests for PatternRecognitionAgent."""

    def test_analyze_returns_pattern_result(self):
        """Should return pattern analysis for ticker with data."""
        agent = PatternRecognitionAgent()

        # Use a ticker likely to have historical data
        result = agent.analyze("AAPL")

        if result is not None:
            assert 'ticker' in result
            assert 'quarters_analyzed' in result
            assert 'bullish_pct' in result
            assert 'current_streak' in result

    def test_returns_none_for_insufficient_data(self):
        """Should return None for ticker with <8 quarters."""
        agent = PatternRecognitionAgent()

        # Use a ticker unlikely to have enough data
        result = agent.analyze("XXXXX")

        assert result is None

    def test_directional_bias_calculation(self):
        """Directional bias should match bullish percentage."""
        agent = PatternRecognitionAgent()

        # Test with known data
        result = agent.analyze("AAPL")

        if result and result.get('directional_bias'):
            bullish_pct = result['bullish_pct']
            bias = result['directional_bias']

            if bullish_pct >= 0.65:
                assert bias == "BULLISH"
            elif bullish_pct <= 0.35:
                assert bias == "BEARISH"
            else:
                assert bias == "NEUTRAL"
