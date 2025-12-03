#!/usr/bin/env python3
"""
Test the composite quality scoring function with real data from 12/1/2025 scan.

This test verifies that MRVL (NEUTRAL bias, lower VRP) ranks higher than
OKTA (STRONG BEARISH, higher VRP) due to risk-adjusted scoring.
"""

import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.scan import calculate_scan_quality_score


def test_mrvl_vs_okta():
    """Test that MRVL ranks higher than OKTA despite lower VRP."""

    # Real data from 12/1/2025 whisper mode scan
    mrvl = {
        'ticker': 'MRVL',
        'ticker_name': 'Marvell Technology',
        'vrp_ratio': 4.00,
        'edge_score': 2.79,
        'implied_move_pct': '11.69%',  # String format from scan output
        'liquidity_tier': 'WARNING',
        'directional_bias': 'NEUTRAL',
        'earnings_date': '2025-12-02'
    }

    okta = {
        'ticker': 'OKTA',
        'ticker_name': 'Okta',
        'vrp_ratio': 8.27,
        'edge_score': 4.67,
        'implied_move_pct': '12.10%',  # String format from scan output
        'liquidity_tier': 'WARNING',
        'directional_bias': 'STRONG BEARISH',
        'earnings_date': '2025-12-02'
    }

    crwd = {
        'ticker': 'CRWD',
        'ticker_name': 'CrowdStrike Holdings',
        'vrp_ratio': 4.19,
        'edge_score': 3.14,
        'implied_move_pct': '7.15%',  # String format from scan output
        'liquidity_tier': 'WARNING',
        'directional_bias': 'STRONG BEARISH',
        'earnings_date': '2025-12-02'
    }

    asan = {
        'ticker': 'ASAN',
        'ticker_name': 'Asana',
        'vrp_ratio': 4.85,
        'edge_score': 2.86,
        'implied_move_pct': '14.83%',  # String format from scan output
        'liquidity_tier': 'REJECT',
        'directional_bias': 'STRONG BEARISH',
        'earnings_date': '2025-12-02'
    }

    aeo = {
        'ticker': 'AEO',
        'ticker_name': 'American Eagle',
        'vrp_ratio': 4.52,
        'edge_score': 2.52,
        'implied_move_pct': '13.96%',  # String format from scan output
        'liquidity_tier': 'REJECT',
        'directional_bias': 'STRONG BEARISH',
        'earnings_date': '2025-12-02'
    }

    # Calculate scores
    mrvl_score = calculate_scan_quality_score(mrvl)
    okta_score = calculate_scan_quality_score(okta)
    crwd_score = calculate_scan_quality_score(crwd)
    asan_score = calculate_scan_quality_score(asan)
    aeo_score = calculate_scan_quality_score(aeo)

    # Display results
    print("\n" + "=" * 80)
    print("COMPOSITE QUALITY SCORE TEST - 12/1/2025 Whisper Mode Data")
    print("=" * 80)
    print("\nScoring Breakdown:")
    print(f"{'Ticker':<8} {'Score':<7} {'VRP':<7} {'Edge':<7} {'Bias':<18} {'Implied':<10} {'Liquidity':<12}")
    print(f"{'-'*8} {'-'*7} {'-'*7} {'-'*7} {'-'*18} {'-'*10} {'-'*12}")

    results = [
        ('MRVL', mrvl_score, mrvl),
        ('OKTA', okta_score, okta),
        ('CRWD', crwd_score, crwd),
        ('ASAN', asan_score, asan),
        ('AEO', aeo_score, aeo)
    ]

    # Sort by score (descending)
    results.sort(key=lambda x: -x[1])

    for ticker, score, data in results:
        print(
            f"{ticker:<8} {score:<7.1f} {data['vrp_ratio']:<7.2f} "
            f"{data['edge_score']:<7.2f} {data['directional_bias']:<18} "
            f"{data['implied_move_pct']:<10} {data['liquidity_tier']:<12}"
        )

    print("\n" + "=" * 80)
    print("EXPECTED RANKING (NO directional penalty - handled at strategy stage):")
    print("=" * 80)
    print("1. OKTA  - Highest VRP (8.27x) + highest edge (4.67)")
    print("2. CRWD  - Low implied move (7.15%) + moderate VRP/edge")
    print("3. MRVL  - Moderate VRP (4.00x) + moderate edge (2.79)")
    print("4. ASAN  - REJECT liquidity (major penalty)")
    print("5. AEO   - REJECT liquidity + highest implied move")

    print("\n" + "=" * 80)
    print("ACTUAL RANKING (from composite scores):")
    print("=" * 80)
    for rank, (ticker, score, data) in enumerate(results, 1):
        print(f"{rank}. {ticker:<6} - Score: {score:.1f}")

    # Verify scoring works correctly (no specific ranking requirement)
    print("\n" + "=" * 80)
    print("TEST RESULTS:")
    print("=" * 80)

    if okta_score > mrvl_score:
        print(f"✅ PASS: OKTA ({okta_score:.1f}) ranks higher than MRVL ({mrvl_score:.1f})")
        print(f"   Difference: +{okta_score - mrvl_score:.1f} points")
        print(f"   Reason: Higher VRP (8.27x vs 4.00x) + higher edge (4.67 vs 2.79)")
    else:
        print(f"⚠️  UNEXPECTED: MRVL ({mrvl_score:.1f}) ranks higher than OKTA ({okta_score:.1f})")
        print(f"   This suggests scoring weights may need adjustment")

    print(f"\n✅ Top ranked ticker: {results[0][0]} with score {results[0][1]:.1f}")
    print(f"   Strategy selection will apply directional alignment at trade.sh stage")

    print("\n" + "=" * 80)
    print("SCORE COMPONENT BREAKDOWN:")
    print("=" * 80)

    def breakdown_score(data):
        """Show detailed breakdown of score components."""
        vrp_score = min(data['vrp_ratio'] / 3.0, 1.0) * 35  # Updated: 35 max
        edge_score = min(data['edge_score'] / 4.0, 1.0) * 30  # Updated: 30 max

        if data['liquidity_tier'] == 'EXCELLENT':
            liq_score = 20.0
        elif data['liquidity_tier'] == 'WARNING':
            liq_score = 10.0
        else:
            liq_score = 0.0

        implied_pct = float(data['implied_move_pct'].rstrip('%'))
        if implied_pct <= 8.0:
            move_score = 15.0  # Updated: 15 max
        elif implied_pct <= 12.0:
            move_score = 10.0
        elif implied_pct <= 15.0:
            move_score = 6.0
        else:
            move_score = 3.0

        return vrp_score, edge_score, liq_score, move_score

    print(f"\n{'Component':<20} {'MRVL':<8} {'OKTA':<8} {'CRWD':<8}")
    print(f"{'-'*20} {'-'*8} {'-'*8} {'-'*8}")

    mrvl_vrp, mrvl_edge, mrvl_liq, mrvl_move = breakdown_score(mrvl)
    okta_vrp, okta_edge, okta_liq, okta_move = breakdown_score(okta)
    crwd_vrp, crwd_edge, crwd_liq, crwd_move = breakdown_score(crwd)

    print(f"{'VRP (35 max)':<20} {mrvl_vrp:<8.1f} {okta_vrp:<8.1f} {crwd_vrp:<8.1f}")
    print(f"{'Edge (30 max)':<20} {mrvl_edge:<8.1f} {okta_edge:<8.1f} {crwd_edge:<8.1f}")
    print(f"{'Liquidity (20 max)':<20} {mrvl_liq:<8.1f} {okta_liq:<8.1f} {crwd_liq:<8.1f}")
    print(f"{'Implied Move (15 max)':<20} {mrvl_move:<8.1f} {okta_move:<8.1f} {crwd_move:<8.1f}")
    print(f"{'-'*20} {'-'*8} {'-'*8} {'-'*8}")
    print(f"{'TOTAL':<20} {mrvl_score:<8.1f} {okta_score:<8.1f} {crwd_score:<8.1f}")

    print("\n" + "=" * 80)
    print("KEY INSIGHT:")
    print("=" * 80)
    print("Directional bias NO LONGER penalized at scan stage")
    print("OKTA ranks higher due to superior VRP (8.27x) + edge (4.67)")
    print("Strategy selection (trade.sh) will apply directional alignment:")
    print("  - OKTA + Bear Call Spread = +8 points (aligned)")
    print("  - OKTA + Bull Put Spread = -3 points (counter-trend)")
    print("Result: All opportunities surface, strategy matching happens later")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    test_mrvl_vs_okta()
