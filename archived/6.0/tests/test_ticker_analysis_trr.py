#!/usr/bin/env python
"""Tests for TRR integration in TickerAnalysisAgent."""

import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agents.ticker_analysis import TickerAnalysisAgent


def test_analysis_includes_position_limits():
    """Analysis result should include position_limits when available."""
    agent = TickerAnalysisAgent()

    # Use a future earnings date
    earnings_date = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')

    result = agent.analyze("AAPL", earnings_date, generate_strategies=False)

    # Should include position_limits field
    assert 'position_limits' in result

    if result['position_limits']:
        limits = result['position_limits']
        assert 'tail_risk_ratio' in limits
        assert 'tail_risk_level' in limits
        assert 'max_contracts' in limits


def test_high_risk_ticker_flagged():
    """MU should be flagged as HIGH risk if in database."""
    agent = TickerAnalysisAgent()
    earnings_date = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d')

    result = agent.analyze("MU", earnings_date, generate_strategies=False)

    if result.get('position_limits'):
        # MU has TRR > 2.5 per CLAUDE.md
        assert result['position_limits']['tail_risk_level'] == "HIGH"
