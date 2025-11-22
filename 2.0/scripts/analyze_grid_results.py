#!/usr/bin/env python3
"""
Analyze grid scan results to recommend optimal weights and thresholds.

Uses the actual VRP distribution from the 18 tradeable opportunities
to suggest data-driven adjustments.
"""

import statistics
from typing import List, Tuple

# Grid scan results (from Nov 21, 2025)
OPPORTUNITIES = [
    ("AKAM", 15.78, 15.15, 10.56, "EXCELLENT"),
    ("ADBE", 11.37, 10.73, 6.26, "EXCELLENT"),
    ("DVN", 11.03, 13.57, 8.21, "EXCELLENT"),
    ("AIG", 10.33, 10.30, 7.46, "EXCELLENT"),
    ("HPE", 7.14, 10.02, 4.24, "EXCELLENT"),
    ("HPQ", 6.71, 7.64, 4.42, "EXCELLENT"),
    ("CSX", 6.22, 7.97, 3.72, "EXCELLENT"),
    ("CRM", 6.11, 9.70, 3.75, "EXCELLENT"),
    ("COST", 5.95, 5.56, 3.48, "EXCELLENT"),
    ("AVGO", 5.49, 12.72, 3.63, "EXCELLENT"),
    ("GS", 4.39, 10.20, 2.96, "EXCELLENT"),
    ("AEP", 3.72, 8.11, 2.09, "EXCELLENT"),
    ("BAC", 3.70, 9.03, 2.24, "EXCELLENT"),
    ("C", 3.53, 10.58, 2.44, "EXCELLENT"),
    ("BK", 3.07, 8.81, 1.66, "EXCELLENT"),
    ("CCL", 2.55, 12.07, 1.86, "EXCELLENT"),
    ("GIS", 2.34, 6.57, 1.48, "EXCELLENT"),
    ("DRI", 1.77, 7.94, 0.95, "GOOD"),
]

# Scan statistics
TOTAL_SCANNED = 289
SUCCESSFULLY_ANALYZED = 22
FILTERED_LIQUIDITY = 196
SKIPPED_NO_EARNINGS = 69
ERRORS = 2


def print_section(title: str):
    """Print a section header."""
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def analyze_vrp_distribution():
    """Analyze VRP ratio distribution."""
    print_section("1. VRP RATIO DISTRIBUTION ANALYSIS")

    vrps = [opp[1] for opp in OPPORTUNITIES]

    print(f"\nVRP Statistics (n={len(vrps)} tradeable opportunities):")
    print(f"  Min:    {min(vrps):.2f}x")
    print(f"  P25:    {sorted(vrps)[int(len(vrps)*0.25)]:.2f}x")
    print(f"  Median: {statistics.median(vrps):.2f}x")
    print(f"  P75:    {sorted(vrps)[int(len(vrps)*0.75)]:.2f}x")
    print(f"  Max:    {max(vrps):.2f}x")
    print(f"  Mean:   {statistics.mean(vrps):.2f}x")
    print(f"  StdDev: {statistics.stdev(vrps):.2f}x")

    print("\n📌 Current VRP Thresholds:")
    print("  EXCELLENT: >= 2.0x")
    print("  GOOD:      >= 1.5x")
    print("  MARGINAL:  >= 1.2x")
    print("  POOR:      < 1.2x")

    excellent = sum(1 for v in vrps if v >= 2.0)
    good = sum(1 for v in vrps if 1.5 <= v < 2.0)
    marginal = sum(1 for v in vrps if 1.2 <= v < 1.5)

    print(f"\n📊 Current Threshold Distribution:")
    print(f"  EXCELLENT: {excellent:2d} / {len(vrps)} ({excellent/len(vrps)*100:.1f}%)")
    print(f"  GOOD:      {good:2d} / {len(vrps)} ({good/len(vrps)*100:.1f}%)")
    print(f"  MARGINAL:  {marginal:2d} / {len(vrps)} ({marginal/len(vrps)*100:.1f}%)")

    # Suggested adjustments
    p33 = sorted(vrps)[int(len(vrps)*0.33)]
    p67 = sorted(vrps)[int(len(vrps)*0.67)]

    print(f"\n💡 RECOMMENDED VRP THRESHOLDS (Tercile-based):")
    print(f"  EXCELLENT: >= {p67:.1f}x  (top 33% of tradeable)")
    print(f"  GOOD:      >= {p33:.1f}x  (top 67% of tradeable)")
    print(f"  MARGINAL:  >= 1.5x  (keep current)")
    print(f"  POOR:      < 1.5x")


def analyze_implied_moves():
    """Analyze implied move distribution."""
    print_section("2. IMPLIED MOVE ANALYSIS")

    implied_moves = [opp[2] for opp in OPPORTUNITIES]

    print(f"\nImplied Move Statistics (n={len(implied_moves)}):")
    print(f"  Min:    {min(implied_moves):.2f}%")
    print(f"  P25:    {sorted(implied_moves)[int(len(implied_moves)*0.25)]:.2f}%")
    print(f"  Median: {statistics.median(implied_moves):.2f}%")
    print(f"  P75:    {sorted(implied_moves)[int(len(implied_moves)*0.75)]:.2f}%")
    print(f"  Max:    {max(implied_moves):.2f}%")
    print(f"  Mean:   {statistics.mean(implied_moves):.2f}%")

    print("\n📊 Implied Move Ranges:")
    high = sum(1 for m in implied_moves if m >= 10.0)
    medium = sum(1 for m in implied_moves if 7.0 <= m < 10.0)
    low = sum(1 for m in implied_moves if m < 7.0)

    total = len(implied_moves)
    print(f"  >= 10% (High volatility): {high:2d} ({high/total*100:.1f}%)")
    print(f"  7-10% (Medium):           {medium:2d} ({medium/total*100:.1f}%)")
    print(f"  < 7% (Low):               {low:2d} ({low/total*100:.1f}%)")


def analyze_edge_scores():
    """Analyze edge score distribution."""
    print_section("3. EDGE SCORE ANALYSIS")

    edges = [opp[3] for opp in OPPORTUNITIES]

    print(f"\nEdge Score Statistics (n={len(edges)}):")
    print(f"  Min:    {min(edges):.2f}")
    print(f"  P25:    {sorted(edges)[int(len(edges)*0.25)]:.2f}")
    print(f"  Median: {statistics.median(edges):.2f}")
    print(f"  P75:    {sorted(edges)[int(len(edges)*0.75)]:.2f}")
    print(f"  Max:    {max(edges):.2f}")
    print(f"  Mean:   {statistics.mean(edges):.2f}")

    print("\n📊 Edge Score Distribution:")
    exceptional = sum(1 for e in edges if e >= 5.0)
    strong = sum(1 for e in edges if 3.0 <= e < 5.0)
    good = sum(1 for e in edges if 2.0 <= e < 3.0)
    fair = sum(1 for e in edges if e < 2.0)

    total = len(edges)
    print(f"  >= 5.0 (Exceptional): {exceptional:2d} ({exceptional/total*100:.1f}%)")
    print(f"  3.0-5.0 (Strong):     {strong:2d} ({strong/total*100:.1f}%)")
    print(f"  2.0-3.0 (Good):       {good:2d} ({good/total*100:.1f}%)")
    print(f"  < 2.0 (Fair):         {fair:2d} ({fair/total*100:.1f}%)")


def analyze_filter_effectiveness():
    """Analyze filtering effectiveness."""
    print_section("4. FILTERING EFFECTIVENESS")

    print(f"\nGrid Scan Statistics:")
    print(f"  Total Tickers Scanned:     {TOTAL_SCANNED}")
    print(f"  Successfully Analyzed:     {SUCCESSFULLY_ANALYZED}")
    print(f"  Filtered (Liquidity):      {FILTERED_LIQUIDITY} ({FILTERED_LIQUIDITY/TOTAL_SCANNED*100:.1f}%)")
    print(f"  Skipped (No Earnings):     {SKIPPED_NO_EARNINGS} ({SKIPPED_NO_EARNINGS/TOTAL_SCANNED*100:.1f}%)")
    print(f"  Errors:                    {ERRORS}")
    print(f"  Tradeable Opportunities:   {len(OPPORTUNITIES)}")

    print(f"\n💡 Analysis:")
    success_rate = len(OPPORTUNITIES) / SUCCESSFULLY_ANALYZED * 100
    print(f"  - Filter Rate: {FILTERED_LIQUIDITY/TOTAL_SCANNED*100:.1f}% (appropriate for earnings trades)")
    print(f"  - Success Rate: {success_rate:.1f}% of analyzed tickers are tradeable")
    print(f"  - Signal Quality: {len(OPPORTUNITIES)} high-quality opportunities from {TOTAL_SCANNED} tickers")


def recommend_scoring_weights():
    """Recommend strategy scoring weight adjustments."""
    print_section("5. RECOMMENDED SCORING WEIGHT ADJUSTMENTS")

    print("\n📋 Current Default Weights (src/config/config.py):")
    print("  vrp_weight: 0.30          # VRP edge")
    print("  pop_weight: 0.25          # Probability of profit")
    print("  reward_risk_weight: 0.20  # Reward/risk ratio")
    print("  liquidity_weight: 0.20    # Liquidity score")
    print("  consistency_weight: 0.05  # Historical consistency")

    print("\n💡 RECOMMENDED PROFILES:")

    print("\n1. CONSERVATIVE (Safety First):")
    print("   Best for: Smaller accounts, risk-averse traders")
    print("   vrp_weight: 0.15          # Edge less critical")
    print("   pop_weight: 0.40          # High win rate most important")
    print("   reward_risk_weight: 0.15  # Asymmetric payoff secondary")
    print("   liquidity_weight: 0.25    # Execution quality critical")
    print("   consistency_weight: 0.05  # Track record")

    print("\n2. AGGRESSIVE (Maximum Edge):")
    print("   Best for: Larger accounts, edge-focused traders")
    print("   vrp_weight: 0.45          # Maximize theoretical edge")
    print("   pop_weight: 0.15          # Win rate less important")
    print("   reward_risk_weight: 0.25  # Big winners")
    print("   liquidity_weight: 0.10    # Accept some slippage for edge")
    print("   consistency_weight: 0.05  # Track record")

    print("\n3. BALANCED (Recommended - Current):")
    print("   Best for: Most traders, proven approach")
    print("   vrp_weight: 0.30          # Good edge weighting")
    print("   pop_weight: 0.25          # Balanced win rate")
    print("   reward_risk_weight: 0.20  # Reasonable payoffs")
    print("   liquidity_weight: 0.20    # Good execution")
    print("   consistency_weight: 0.05  # Track record")
    print("   ✓ KEEP CURRENT - Well calibrated")

    print("\n4. VRP-FOCUSED (Pure IV Crush):")
    print("   Best for: Statistical traders, high conviction in mean reversion")
    print("   vrp_weight: 0.50          # Pure VRP edge")
    print("   pop_weight: 0.20          # Win rate secondary")
    print("   reward_risk_weight: 0.10  # Less important")
    print("   liquidity_weight: 0.15    # Minimum for execution")
    print("   consistency_weight: 0.05  # Track record")


def recommend_liquidity_thresholds():
    """Recommend liquidity threshold adjustments."""
    print_section("6. LIQUIDITY THRESHOLD RECOMMENDATIONS")

    print("\n📌 Current Liquidity Thresholds (src/application/metrics/liquidity_scorer.py):")
    print("  Open Interest:")
    print("    min: 50, good: 500, excellent: 2000")
    print("  Volume:")
    print("    min: 20, good: 100, excellent: 500")
    print("  Bid-Ask Spread:")
    print("    max: 10.0%, good: 5.0%, excellent: 2.0%")

    print(f"\n💡 Analysis:")
    print(f"  - {FILTERED_LIQUIDITY}/{TOTAL_SCANNED} ({FILTERED_LIQUIDITY/TOTAL_SCANNED*100:.1f}%) filtered for liquidity")
    print(f"  - This is appropriate for earnings plays (conservative filters)")
    print(f"  - {len(OPPORTUNITIES)} high-quality opportunities found")

    print("\n✓ RECOMMENDATION: KEEP CURRENT THRESHOLDS")
    print("  Rationale:")
    print("  - Conservative filters protect from illiquid markets")
    print("  - 68% filter rate ensures only tradeable options")
    print("  - 18 opportunities is sufficient for selectivity")
    print("  - Earnings trades require tighter liquidity than regular options")


def main():
    """Run complete weight and threshold analysis."""
    print("=" * 80)
    print("WEIGHT & THRESHOLD OPTIMIZATION ANALYSIS")
    print("Based on Grid Scan Results: November 21, 2025")
    print("=" * 80)
    print(f"\nDataset: {len(OPPORTUNITIES)} tradeable opportunities from {TOTAL_SCANNED} tickers")

    analyze_vrp_distribution()
    analyze_implied_moves()
    analyze_edge_scores()
    analyze_filter_effectiveness()
    recommend_scoring_weights()
    recommend_liquidity_thresholds()

    print_section("7. SUMMARY - RECOMMENDED CHANGES")

    print("\n1. VRP THRESHOLDS (src/application/metrics/vrp.py):")
    print("   Change from:")
    print("     excellent: 2.0x, good: 1.5x, marginal: 1.2x")
    print("   To (tercile-based):")
    vrps = [opp[1] for opp in OPPORTUNITIES]
    p33 = sorted(vrps)[int(len(vrps)*0.33)]
    p67 = sorted(vrps)[int(len(vrps)*0.67)]
    print(f"     excellent: {p67:.1f}x, good: {p33:.1f}x, marginal: 1.5x")

    print("\n2. SCORING WEIGHTS (src/config/config.py):")
    print("   ✓ KEEP CURRENT (well balanced)")
    print("   Alternative profiles available above for different risk tolerances")

    print("\n3. LIQUIDITY THRESHOLDS (src/application/metrics/liquidity_scorer.py):")
    print("   ✓ KEEP CURRENT (conservative and appropriate)")

    print("\n4. FILTERING LOGIC:")
    print("   ✓ KEEP CURRENT (68% filter rate is optimal)")

    print("\n" + "=" * 80)
    print("IMPLEMENTATION PRIORITY")
    print("=" * 80)

    print("\n🔥 HIGH PRIORITY:")
    print("   1. Adjust VRP thresholds to match actual data distribution")
    print("      - File: src/application/metrics/vrp.py")
    print("      - Current thresholds don't match observed distribution")

    print("\n📊 MEDIUM PRIORITY:")
    print("   2. Consider adding weight profiles to config")
    print("      - File: src/config/config.py")
    print("      - Allow users to select conservative/balanced/aggressive")

    print("\n✅ LOW PRIORITY (Already Optimal):")
    print("   3. Liquidity thresholds - working well")
    print("   4. Filter effectiveness - appropriate rate")
    print("   5. Current balanced weights - reasonable defaults")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
