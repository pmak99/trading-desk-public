#!/usr/bin/env python
"""Tests for real sector-based cross-ticker warnings."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.orchestrators.whisper import WhisperOrchestrator


def test_sector_warning_uses_real_sector():
    """Cross-ticker warnings should use real sector names when 3+ tickers in same sector."""
    orchestrator = WhisperOrchestrator()

    # Mock results with sector data - 3 Technology tickers (different first letters)
    results = [
        {'ticker': 'NVDA', 'sector': 'Technology'},
        {'ticker': 'AAPL', 'sector': 'Technology'},
        {'ticker': 'MSFT', 'sector': 'Technology'},
    ]

    warnings = orchestrator._detect_cross_ticker_risks(results)

    # Should warn about Technology concentration (not first letter)
    # This verifies the implementation uses real sector data
    warning_text = ' '.join(warnings)
    assert 'Technology' in warning_text, f"Expected 'Technology' in warnings but got: {warnings}"


def test_no_warning_for_different_sectors():
    """No sector warning when tickers are in different sectors."""
    orchestrator = WhisperOrchestrator()

    results = [
        {'ticker': 'NVDA', 'sector': 'Technology'},
        {'ticker': 'JPM', 'sector': 'Financial Services'},
        {'ticker': 'JNJ', 'sector': 'Healthcare'},
    ]

    warnings = orchestrator._detect_cross_ticker_risks(results)

    # Should not warn about sector concentration (different sectors)
    sector_warning_found = any(
        'sector concentration' in w.lower() or
        ('concentration' in w.lower() and ('Technology' in w or 'Financial' in w or 'Healthcare' in w))
        for w in warnings
    )
    assert not sector_warning_found, f"Should not have sector concentration warning but got: {warnings}"
