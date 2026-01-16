#!/usr/bin/env python
"""Tests for pattern recognition integration in AnalyzeOrchestrator."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.orchestrators.analyze import AnalyzeOrchestrator


class TestAnalyzePatternIntegration:
    """Tests for pattern recognition integration."""

    def test_format_results_includes_patterns_section(self):
        """Formatted output should include Historical Patterns section."""
        orchestrator = AnalyzeOrchestrator()

        # Create mock result with patterns
        result = {
            'success': True,
            'ticker': 'AAPL',
            'earnings_date': '2026-02-05',
            'report': {
                'ticker': 'AAPL',
                'earnings_date': '2026-02-05',
                'summary': {
                    'vrp_ratio': 4.5,
                    'recommendation': 'EXCELLENT',
                    'liquidity_tier': 'GOOD',
                    'score': 75,
                    'sentiment_direction': 'bullish',
                    'sentiment_score': 0.6
                },
                'vrp_analysis': {
                    'ratio': 4.5,
                    'recommendation': 'EXCELLENT',
                    'explanation': 'High VRP due to earnings uncertainty'
                },
                'liquidity': {'tier': 'GOOD', 'tradeable': True},
                'sentiment': {'direction': 'bullish', 'score': 0.6, 'catalysts': [], 'risks': []},
                'strategies': [],
                'anomalies': [],
                'key_factors': [],
                'historical_context': '',
                'position_limits': None,
                'patterns': {
                    'ticker': 'AAPL',
                    'quarters_analyzed': 16,
                    'bullish_pct': 0.69,
                    'bearish_pct': 0.31,
                    'directional_bias': 'BULLISH',
                    'current_streak': 3,
                    'streak_direction': 'UP',
                    'avg_move_recent': 5.2,
                    'avg_move_overall': 3.8,
                    'magnitude_trend': 'EXPANDING',
                    'recent_moves': [
                        {'date': '2025-10-15', 'move': 4.5, 'direction': 'UP'},
                        {'date': '2025-07-15', 'move': 3.2, 'direction': 'UP'},
                        {'date': '2025-04-15', 'move': -2.1, 'direction': 'DOWN'},
                        {'date': '2025-01-15', 'move': 5.8, 'direction': 'UP'}
                    ]
                }
            },
            'recommendation': {'action': 'TRADE', 'reason': 'test', 'details': 'test'}
        }

        output = orchestrator.format_results(result)

        # Verify patterns section is included
        assert 'Historical Patterns' in output
        assert 'Quarters Analyzed' in output
        assert '16' in output
        assert 'Directional Bias' in output
        assert 'BULLISH' in output
        assert 'Current Streak' in output
        assert 'Recent Earnings' in output

    def test_format_results_shows_magnitude_trend(self):
        """Magnitude trend should be shown when not STABLE."""
        orchestrator = AnalyzeOrchestrator()

        result = {
            'success': True,
            'ticker': 'NVDA',
            'earnings_date': '2026-02-05',
            'report': {
                'ticker': 'NVDA',
                'earnings_date': '2026-02-05',
                'summary': {
                    'vrp_ratio': 5.0,
                    'recommendation': 'EXCELLENT',
                    'liquidity_tier': 'GOOD',
                    'score': 80,
                    'sentiment_direction': 'bullish',
                    'sentiment_score': 0.7
                },
                'vrp_analysis': {
                    'ratio': 5.0,
                    'recommendation': 'EXCELLENT',
                    'explanation': 'High VRP'
                },
                'liquidity': {'tier': 'GOOD', 'tradeable': True},
                'sentiment': {'direction': 'bullish', 'score': 0.7, 'catalysts': [], 'risks': []},
                'strategies': [],
                'anomalies': [],
                'key_factors': [],
                'historical_context': '',
                'position_limits': None,
                'patterns': {
                    'ticker': 'NVDA',
                    'quarters_analyzed': 12,
                    'bullish_pct': 0.58,
                    'bearish_pct': 0.42,
                    'directional_bias': 'NEUTRAL',
                    'current_streak': 2,
                    'streak_direction': 'UP',
                    'avg_move_recent': 8.5,
                    'avg_move_overall': 5.2,
                    'magnitude_trend': 'EXPANDING',
                    'recent_moves': []
                }
            },
            'recommendation': {'action': 'TRADE', 'reason': 'test', 'details': 'test'}
        }

        output = orchestrator.format_results(result)

        assert 'EXPANDING' in output
        assert '8.5% recent vs 5.2% avg' in output

    def test_format_results_no_patterns_when_insufficient_data(self):
        """Patterns section should not appear when quarters_analyzed < 8."""
        orchestrator = AnalyzeOrchestrator()

        result = {
            'success': True,
            'ticker': 'TEST',
            'earnings_date': '2026-02-05',
            'report': {
                'ticker': 'TEST',
                'earnings_date': '2026-02-05',
                'summary': {
                    'vrp_ratio': 3.0,
                    'recommendation': 'GOOD',
                    'liquidity_tier': 'GOOD',
                    'score': 65,
                    'sentiment_direction': 'neutral',
                    'sentiment_score': 0.0
                },
                'vrp_analysis': {
                    'ratio': 3.0,
                    'recommendation': 'GOOD',
                    'explanation': 'Moderate VRP'
                },
                'liquidity': {'tier': 'GOOD', 'tradeable': True},
                'sentiment': {'direction': 'neutral', 'score': 0.0, 'catalysts': [], 'risks': []},
                'strategies': [],
                'anomalies': [],
                'key_factors': [],
                'historical_context': '',
                'position_limits': None,
                'patterns': {
                    'ticker': 'TEST',
                    'quarters_analyzed': 4,  # Less than 8
                    'bullish_pct': 0.5,
                    'bearish_pct': 0.5,
                    'directional_bias': 'NEUTRAL',
                    'current_streak': 1,
                    'streak_direction': 'UP',
                    'avg_move_recent': 3.0,
                    'avg_move_overall': 3.0,
                    'magnitude_trend': 'STABLE',
                    'recent_moves': []
                }
            },
            'recommendation': {'action': 'SKIP', 'reason': 'test', 'details': 'test'}
        }

        output = orchestrator.format_results(result)

        # Should not include patterns section for insufficient data
        assert 'Historical Patterns' not in output

    def test_format_results_no_patterns_when_none(self):
        """Patterns section should not appear when patterns is None."""
        orchestrator = AnalyzeOrchestrator()

        result = {
            'success': True,
            'ticker': 'UNKNOWN',
            'earnings_date': '2026-02-05',
            'report': {
                'ticker': 'UNKNOWN',
                'earnings_date': '2026-02-05',
                'summary': {
                    'vrp_ratio': 2.0,
                    'recommendation': 'SKIP',
                    'liquidity_tier': 'REJECT',
                    'score': 40,
                    'sentiment_direction': None,
                    'sentiment_score': None
                },
                'vrp_analysis': {
                    'ratio': 2.0,
                    'recommendation': 'SKIP',
                    'explanation': 'Low VRP'
                },
                'liquidity': {'tier': 'REJECT', 'tradeable': False},
                'sentiment': {'direction': 'neutral', 'score': 0.0, 'catalysts': [], 'risks': []},
                'strategies': [],
                'anomalies': [],
                'key_factors': [],
                'historical_context': '',
                'position_limits': None,
                'patterns': None
            },
            'recommendation': {'action': 'DO_NOT_TRADE', 'reason': 'test', 'details': 'test'}
        }

        output = orchestrator.format_results(result)

        assert 'Historical Patterns' not in output

    def test_streak_only_shown_when_significant(self):
        """Streak should only be shown when >= 3 consecutive moves."""
        orchestrator = AnalyzeOrchestrator()

        result = {
            'success': True,
            'ticker': 'MSFT',
            'earnings_date': '2026-02-05',
            'report': {
                'ticker': 'MSFT',
                'earnings_date': '2026-02-05',
                'summary': {
                    'vrp_ratio': 4.0,
                    'recommendation': 'GOOD',
                    'liquidity_tier': 'EXCELLENT',
                    'score': 70,
                    'sentiment_direction': 'neutral',
                    'sentiment_score': 0.0
                },
                'vrp_analysis': {
                    'ratio': 4.0,
                    'recommendation': 'GOOD',
                    'explanation': 'Good VRP'
                },
                'liquidity': {'tier': 'EXCELLENT', 'tradeable': True},
                'sentiment': {'direction': 'neutral', 'score': 0.0, 'catalysts': [], 'risks': []},
                'strategies': [],
                'anomalies': [],
                'key_factors': [],
                'historical_context': '',
                'position_limits': None,
                'patterns': {
                    'ticker': 'MSFT',
                    'quarters_analyzed': 10,
                    'bullish_pct': 0.6,
                    'bearish_pct': 0.4,
                    'directional_bias': 'NEUTRAL',
                    'current_streak': 2,  # Less than 3
                    'streak_direction': 'UP',
                    'avg_move_recent': 3.5,
                    'avg_move_overall': 3.5,
                    'magnitude_trend': 'STABLE',
                    'recent_moves': []
                }
            },
            'recommendation': {'action': 'TRADE', 'reason': 'test', 'details': 'test'}
        }

        output = orchestrator.format_results(result)

        # Historical Patterns section should appear (quarters_analyzed >= 8)
        assert 'Historical Patterns' in output
        # But streak should NOT be shown (current_streak < 3)
        assert 'Current Streak' not in output
