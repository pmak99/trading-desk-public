#!/usr/bin/env python
"""Tests for TRR in analyze output."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.orchestrators.analyze import AnalyzeOrchestrator


def test_analyze_output_includes_position_limits():
    """Formatted output should include position limits section for HIGH TRR."""
    orchestrator = AnalyzeOrchestrator()

    # Create mock result with HIGH TRR
    result = {
        'success': True,
        'ticker': 'MU',
        'earnings_date': '2026-02-05',
        'report': {
            'ticker': 'MU',
            'earnings_date': '2026-02-05',
            'summary': {
                'vrp_ratio': 5.1,
                'recommendation': 'EXCELLENT',
                'liquidity_tier': 'GOOD',
                'score': 78,
                'sentiment_direction': 'bullish',
                'sentiment_score': 0.6
            },
            'vrp_analysis': {
                'ratio': 5.1,
                'recommendation': 'EXCELLENT',
                'explanation': 'High VRP due to earnings uncertainty'
            },
            'liquidity': {'tier': 'GOOD', 'tradeable': True},
            'sentiment': {'direction': 'bullish', 'score': 0.6, 'catalysts': [], 'risks': []},
            'strategies': [],
            'anomalies': [],
            'key_factors': [],
            'historical_context': '',
            'position_limits': {
                'tail_risk_ratio': 3.05,
                'tail_risk_level': 'HIGH',
                'max_contracts': 50,
                'max_notional': 25000.0,
                'avg_move': 3.68,
                'max_move': 11.21
            }
        },
        'recommendation': {'action': 'TRADE', 'reason': 'test', 'details': 'test'}
    }

    output = orchestrator.format_results(result)

    assert 'Position Limits' in output
    assert 'HIGH' in output
    assert '50' in output  # max contracts
