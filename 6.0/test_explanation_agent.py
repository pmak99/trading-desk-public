#!/usr/bin/env python3
"""Test script for ExplanationAgent."""

import sys
from pathlib import Path

# Add 6.0/ to path (not 6.0/src/ to allow "from src.agents..." imports)
sys.path.insert(0, str(Path(__file__).parent))

from src.agents.explanation import ExplanationAgent


def test_scenario(name: str, **kwargs):
    """Test a specific explanation scenario."""
    print(f"\n{'=' * 60}")
    print(f"TEST: {name}")
    print('=' * 60)

    agent = ExplanationAgent()
    result = agent.explain(**kwargs)

    print(f"Ticker: {result['ticker']}")
    print(f"\nExplanation:")
    print(f"  {result['explanation']}")

    print(f"\nKey Factors ({len(result['key_factors'])}):")
    for i, factor in enumerate(result['key_factors'], 1):
        print(f"  {i}. {factor}")

    print(f"\nHistorical Context:")
    print(f"  {result['historical_context']}")

    return result


# Test 1: NVDA with high VRP (should have historical data + cached sentiment)
print("\n" + "=" * 60)
print("EXPLANATION AGENT TESTS")
print("=" * 60)

test_scenario(
    "High VRP with Historical Data + Sentiment",
    ticker="NVDA",
    vrp_ratio=6.2,
    liquidity_tier="GOOD",
    earnings_date="2026-02-05"
)

# Test 2: Ticker with historical data but no cached sentiment
test_scenario(
    "High VRP with Historical Data (No Sentiment)",
    ticker="AAPL",
    vrp_ratio=5.5,
    liquidity_tier="EXCELLENT",
    earnings_date=None  # No earnings date = no sentiment lookup
)

# Test 3: EXCELLENT VRP (7x+)
test_scenario(
    "Exceptional VRP (7x+)",
    ticker="TSLA",
    vrp_ratio=7.8,
    liquidity_tier="GOOD",
    earnings_date=None
)

# Test 4: Marginal VRP
test_scenario(
    "Marginal VRP (3-4x)",
    ticker="JPM",
    vrp_ratio=3.5,
    liquidity_tier="WARNING",
    earnings_date=None
)

# Test 5: Unknown ticker (should handle gracefully)
test_scenario(
    "Unknown Ticker (No Historical Data)",
    ticker="FAKESYM",
    vrp_ratio=5.0,
    liquidity_tier="REJECT",
    earnings_date=None
)

print("\n" + "=" * 60)
print("✅ ALL EXPLANATION AGENT TESTS COMPLETE")
print("=" * 60)
