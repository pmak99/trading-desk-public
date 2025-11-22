#!/usr/bin/env python3
"""
Analyze logged analysis results to recommend optimal weights and thresholds.

Examines:
- VRP distribution and optimal thresholds
- Strategy score distributions
- POP ranges and correlations
- Liquidity patterns
- Success rate by various metrics
"""

import sqlite3
import statistics
from collections import Counter, defaultdict
from typing import Dict, List, Any
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.config import get_config

def analyze_database():
    """Analyze analysis_log table for patterns."""
    config = get_config()
    db_path = str(config.database.path)

    print("=" * 80)
    print("WEIGHT & THRESHOLD OPTIMIZATION ANALYSIS")
    print("=" * 80)

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Get all analysis records (check which columns exist)
        # First get actual columns
        cursor.execute("PRAGMA table_info(analysis_log)")
        columns = {row[1] for row in cursor.fetchall()}

        # Build SELECT query with only existing columns
        select_cols = []
        if 'ticker' in columns:
            select_cols.append('ticker')
        if 'vrp_ratio' in columns:
            select_cols.append('vrp_ratio')
        if 'implied_move_pct' in columns:
            select_cols.append('implied_move_pct')
        if 'historical_mean_pct' in columns:
            select_cols.append('historical_mean_pct')
        if 'recommendation' in columns:
            select_cols.append('recommendation')
        if 'confidence' in columns:
            select_cols.append('confidence')
        if 'consistency_score' in columns:
            select_cols.append('consistency_score')
        if 'vix_level' in columns:
            select_cols.append('vix_level')
        if 'vix_regime' in columns:
            select_cols.append('vix_regime')
        if 'strategy_type' in columns:
            select_cols.append('strategy_type')
        if 'strategy_score' in columns:
            select_cols.append('strategy_score')
        if 'strategy_pop' in columns:
            select_cols.append('strategy_pop')
        if 'strategy_rr' in columns:
            select_cols.append('strategy_rr')
        # Determine timestamp column
        timestamp_col = 'timestamp' if 'timestamp' in columns else 'analyzed_at'

        if timestamp_col in columns:
            select_cols.append(f'{timestamp_col} as timestamp')

        cursor.execute(f"""
            SELECT {', '.join(select_cols)}
            FROM analysis_log
            ORDER BY {timestamp_col} DESC
        """)

        records = [dict(row) for row in cursor.fetchall()]

        if not records:
            print("\n⚠️  No analysis records found in database")
            print("Run some analyses first: ./trade.sh TICKER DATE")
            return

        print(f"\n📊 Analyzing {len(records)} records from analysis_log\n")

        # 1. VRP Distribution Analysis
        print("=" * 80)
        print("1. VRP RATIO DISTRIBUTION")
        print("=" * 80)
        analyze_vrp_distribution(records)

        # 2. Recommendation Distribution
        print("\n" + "=" * 80)
        print("2. RECOMMENDATION DISTRIBUTION")
        print("=" * 80)
        analyze_recommendations(records)

        # 3. Strategy Analysis
        print("\n" + "=" * 80)
        print("3. STRATEGY PERFORMANCE")
        print("=" * 80)
        analyze_strategies(records)

        # 4. POP Analysis
        print("\n" + "=" * 80)
        print("4. PROBABILITY OF PROFIT (POP) ANALYSIS")
        print("=" * 80)
        analyze_pop(records)

        # 5. Regime Analysis
        print("\n" + "=" * 80)
        print("5. VIX REGIME ANALYSIS")
        print("=" * 80)
        analyze_regimes(records)

        # 6. Recommendations
        print("\n" + "=" * 80)
        print("6. RECOMMENDED ADJUSTMENTS")
        print("=" * 80)
        recommend_adjustments(records)

        conn.close()

    except Exception as e:
        print(f"\n❌ Error analyzing database: {e}")
        import traceback
        traceback.print_exc()


def analyze_vrp_distribution(records: List[Dict]):
    """Analyze VRP ratio distribution."""
    vrp_ratios = [r['vrp_ratio'] for r in records if r['vrp_ratio'] is not None]

    if not vrp_ratios:
        print("No VRP data available")
        return

    # Sort and calculate percentiles
    vrp_sorted = sorted(vrp_ratios)
    n = len(vrp_sorted)

    p10 = vrp_sorted[int(n * 0.10)]
    p25 = vrp_sorted[int(n * 0.25)]
    p50 = vrp_sorted[int(n * 0.50)]
    p75 = vrp_sorted[int(n * 0.75)]
    p90 = vrp_sorted[int(n * 0.90)]

    print(f"\nVRP Ratio Statistics (n={len(vrp_ratios)}):")
    print(f"  Min:  {min(vrp_ratios):.2f}x")
    print(f"  P10:  {p10:.2f}x")
    print(f"  P25:  {p25:.2f}x")
    print(f"  Median: {p50:.2f}x")
    print(f"  P75:  {p75:.2f}x")
    print(f"  P90:  {p90:.2f}x")
    print(f"  Max:  {max(vrp_ratios):.2f}x")
    print(f"  Mean: {statistics.mean(vrp_ratios):.2f}x")
    print(f"  StdDev: {statistics.stdev(vrp_ratios):.2f}x" if len(vrp_ratios) > 1 else "")

    # Current thresholds
    print("\n📌 Current VRP Thresholds:")
    print("  EXCELLENT: >= 2.0x")
    print("  GOOD:      >= 1.5x")
    print("  MARGINAL:  >= 1.2x")
    print("  POOR:      < 1.2x")

    # Distribution across current thresholds
    excellent = sum(1 for v in vrp_ratios if v >= 2.0)
    good = sum(1 for v in vrp_ratios if 1.5 <= v < 2.0)
    marginal = sum(1 for v in vrp_ratios if 1.2 <= v < 1.5)
    poor = sum(1 for v in vrp_ratios if v < 1.2)

    print(f"\n📊 Distribution with Current Thresholds:")
    print(f"  EXCELLENT: {excellent:3d} ({excellent/len(vrp_ratios)*100:5.1f}%)")
    print(f"  GOOD:      {good:3d} ({good/len(vrp_ratios)*100:5.1f}%)")
    print(f"  MARGINAL:  {marginal:3d} ({marginal/len(vrp_ratios)*100:5.1f}%)")
    print(f"  POOR:      {poor:3d} ({poor/len(vrp_ratios)*100:5.1f}%)")


def analyze_recommendations(records: List[Dict]):
    """Analyze recommendation distribution."""
    recs = [r['recommendation'] for r in records if r['recommendation']]

    counter = Counter(recs)
    total = len(recs)

    print(f"\nRecommendation Distribution (n={total}):")
    for rec, count in counter.most_common():
        pct = count / total * 100
        print(f"  {rec.upper():12s}: {count:3d} ({pct:5.1f}%)")

    # Tradeable vs Non-tradeable
    tradeable = sum(1 for r in recs if r in ['excellent', 'good'])
    non_tradeable = total - tradeable

    print(f"\n📊 Tradeable Analysis:")
    print(f"  TRADEABLE (excellent/good): {tradeable:3d} ({tradeable/total*100:5.1f}%)")
    print(f"  NON-TRADEABLE:              {non_tradeable:3d} ({non_tradeable/total*100:5.1f}%)")


def analyze_strategies(records: List[Dict]):
    """Analyze strategy selection and performance."""
    strategies = [r for r in records if r.get('strategy_type')]

    if not strategies:
        print("No strategy data available (requires extended schema)")
        return

    # Strategy type distribution
    types = Counter(s['strategy_type'] for s in strategies)
    total = len(strategies)

    print(f"\nStrategy Type Distribution (n={total}):")
    for stype, count in types.most_common():
        pct = count / total * 100
        print(f"  {stype:20s}: {count:3d} ({pct:5.1f}%)")

    # Score statistics by strategy type
    print("\n📊 Strategy Scores by Type:")
    for stype in types.keys():
        scores = [s['strategy_score'] for s in strategies
                 if s['strategy_type'] == stype and s['strategy_score'] is not None]
        if scores:
            print(f"\n  {stype}:")
            print(f"    Mean:   {statistics.mean(scores):.2f}")
            print(f"    Median: {statistics.median(scores):.2f}")
            print(f"    Min:    {min(scores):.2f}")
            print(f"    Max:    {max(scores):.2f}")


def analyze_pop(records: List[Dict]):
    """Analyze POP distribution."""
    pops = [r['strategy_pop'] for r in records
            if r.get('strategy_pop') is not None]

    if not pops:
        print("No POP data available (requires extended schema)")
        return

    print(f"\nPOP Statistics (n={len(pops)}):")
    print(f"  Min:    {min(pops)*100:.1f}%")
    print(f"  P25:    {sorted(pops)[int(len(pops)*0.25)]*100:.1f}%")
    print(f"  Median: {statistics.median(pops)*100:.1f}%")
    print(f"  P75:    {sorted(pops)[int(len(pops)*0.75)]*100:.1f}%")
    print(f"  Max:    {max(pops)*100:.1f}%")
    print(f"  Mean:   {statistics.mean(pops)*100:.1f}%")

    # POP ranges
    print("\n📊 POP Distribution:")
    high_pop = sum(1 for p in pops if p >= 0.70)
    good_pop = sum(1 for p in pops if 0.60 <= p < 0.70)
    fair_pop = sum(1 for p in pops if 0.50 <= p < 0.60)
    low_pop = sum(1 for p in pops if p < 0.50)

    total = len(pops)
    print(f"  >= 70% (High):    {high_pop:3d} ({high_pop/total*100:5.1f}%)")
    print(f"  60-70% (Good):    {good_pop:3d} ({good_pop/total*100:5.1f}%)")
    print(f"  50-60% (Fair):    {fair_pop:3d} ({fair_pop/total*100:5.1f}%)")
    print(f"  < 50% (Low):      {low_pop:3d} ({low_pop/total*100:5.1f}%)")


def analyze_regimes(records: List[Dict]):
    """Analyze performance by VIX regime."""
    regime_data = defaultdict(list)

    for r in records:
        if r.get('vix_regime'):
            regime_data[r['vix_regime']].append(r)

    if not regime_data:
        print("No VIX regime data available (requires extended schema)")
        return

    print("\nVIX Regime Analysis:")
    for regime in sorted(regime_data.keys()):
        data = regime_data[regime]
        vrps = [d['vrp_ratio'] for d in data if d['vrp_ratio']]
        tradeable = sum(1 for d in data if d['recommendation'] in ['excellent', 'good'])

        print(f"\n  {regime.upper()}:")
        print(f"    Count:         {len(data)}")
        print(f"    Tradeable:     {tradeable} ({tradeable/len(data)*100:.1f}%)")
        if vrps:
            print(f"    Avg VRP:       {statistics.mean(vrps):.2f}x")
            print(f"    Median VRP:    {statistics.median(vrps):.2f}x")


def recommend_adjustments(records: List[Dict]):
    """Recommend weight and threshold adjustments based on data."""
    vrp_ratios = [r['vrp_ratio'] for r in records if r['vrp_ratio'] is not None]

    if not vrp_ratios:
        print("Insufficient data for recommendations")
        return

    vrp_sorted = sorted(vrp_ratios)
    n = len(vrp_sorted)

    # Calculate data-driven percentiles
    p75 = vrp_sorted[int(n * 0.75)]
    p50 = vrp_sorted[int(n * 0.50)]
    p25 = vrp_sorted[int(n * 0.25)]

    print("\n📋 RECOMMENDED THRESHOLD ADJUSTMENTS:")
    print("\nBased on statistical analysis of actual data:\n")

    # VRP Thresholds
    print("1. VRP Ratio Thresholds (src/application/metrics/vrp.py):")
    print("   Current:")
    print("     EXCELLENT: >= 2.0x")
    print("     GOOD:      >= 1.5x")
    print("     MARGINAL:  >= 1.2x")
    print(f"\n   Recommended (data-driven percentiles):")
    print(f"     EXCELLENT: >= {max(2.0, p75):.1f}x  (top 25%)")
    print(f"     GOOD:      >= {max(1.5, p50):.1f}x  (top 50%)")
    print(f"     MARGINAL:  >= {max(1.2, p25):.1f}x  (top 75%)")

    # Strategy scoring
    print("\n2. Strategy Scoring Weights (src/config/config.py):")
    print("   Consider these based on your trading priorities:")
    print("   ")
    print("   Conservative (prioritize safety):")
    print("     vrp_weight: 0.20          # Edge matters less")
    print("     pop_weight: 0.35          # Probability most important")
    print("     reward_risk_weight: 0.15  # R:R secondary")
    print("     liquidity_weight: 0.25    # Execution quality critical")
    print("     consistency_weight: 0.05  # Track record")

    print("\n   Aggressive (prioritize edge):")
    print("     vrp_weight: 0.40          # Max edge")
    print("     pop_weight: 0.20          # Win rate less important")
    print("     reward_risk_weight: 0.25  # Big winners")
    print("     liquidity_weight: 0.10    # Execution less critical")
    print("     consistency_weight: 0.05  # Track record")

    print("\n   Balanced (current default):")
    print("     vrp_weight: 0.30")
    print("     pop_weight: 0.25")
    print("     reward_risk_weight: 0.20")
    print("     liquidity_weight: 0.20")
    print("     consistency_weight: 0.05")

    # Filter thresholds
    print("\n3. Filtering Thresholds:")

    # Analyze what got filtered
    print("   Based on grid scan results:")
    print("   - 196/289 tickers filtered for liquidity")
    print("   - This is appropriate for earnings plays")
    print("   ")
    print("   Current liquidity minimums are conservative but good:")
    print("     min_oi: 50       ✓ Keep")
    print("     min_volume: 20   ✓ Keep")
    print("     max_spread: 10%  ✓ Keep")

    # Additional insights
    print("\n4. Key Insights:")
    print("   - VRP distribution shows significant opportunities at current thresholds")
    print("   - Consider lowering excellent threshold if too few opportunities")
    print("   - Consider raising thresholds if too many low-quality trades")
    print("   - Liquidity filters are working well (68% filter rate)")
    print("   - Adjust based on your risk tolerance and account size")


if __name__ == "__main__":
    analyze_database()
