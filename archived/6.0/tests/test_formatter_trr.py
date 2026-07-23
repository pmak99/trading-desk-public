#!/usr/bin/env python
"""Tests for TRR badge in formatter."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.formatter import format_whisper_results


def test_high_trr_badge_shown():
    """HIGH TRR tickers should show warning badge."""
    results = [{
        'ticker': 'MU',
        'earnings_date': '2026-02-05',
        'vrp_ratio': 5.1,
        'liquidity_tier': 'GOOD',
        'recommendation': 'EXCELLENT',
        'score': 78,
        'explanation': 'High VRP',
        'position_limits': {
            'tail_risk_level': 'HIGH',
            'max_contracts': 50
        }
    }]

    output = format_whisper_results(results)

    assert 'HIGH TRR' in output or 'max 50' in output


def test_normal_trr_no_badge():
    """NORMAL TRR tickers should not show TRR badge."""
    results = [{
        'ticker': 'AAPL',
        'earnings_date': '2026-02-05',
        'vrp_ratio': 4.1,
        'liquidity_tier': 'GOOD',
        'recommendation': 'GOOD',
        'score': 72,
        'explanation': 'Decent VRP',
        'position_limits': {
            'tail_risk_level': 'NORMAL',
            'max_contracts': 100
        }
    }]

    output = format_whisper_results(results)

    assert 'HIGH TRR' not in output
