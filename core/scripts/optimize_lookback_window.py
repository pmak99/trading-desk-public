#!/usr/bin/env python3
"""
Historical Lookback Window Optimization.

Tests different lookback windows for historical earnings data:
- Current: Fixed 12 quarters (3 years)
- Tests: 4, 8, 12, 16, 20 quarters
- Adaptive: Dynamic lookback based on consistency score

Hypothesis: Different tickers have different optimal lookback windows.
Highly consistent tickers benefit from shorter windows (recent data).
Erratic tickers benefit from longer windows (more data points).

Usage:
    python scripts/optimize_lookback_window.py --db data/ivcrush.db
"""

import sys
import argparse
import logging
import json
import sqlite3
from datetime import datetime, date
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
import statistics

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)


@dataclass
class LookbackConfig:
    """Configuration for lookback window testing."""
    name: str
    quarters: int  # Number of quarters to look back


@dataclass
class LookbackResults:
    """Results from testing a lookback configuration."""
    config_name: str
    quarters: int
    tickers_analyzed: int
    avg_consistency: float
    avg_move_count: int
    avg_std_dev: float
    coverage_rate: float  # % of tickers with enough data


def create_test_configs() -> List[LookbackConfig]:
    """Create test configurations for lookback windows."""
    return [
        LookbackConfig("Very Short (1 year)", quarters=4),
        LookbackConfig("Short (2 years)", quarters=8),
        LookbackConfig("Baseline (3 years)", quarters=12),
        LookbackConfig("Long (4 years)", quarters=16),
        LookbackConfig("Very Long (5 years)", quarters=20),
    ]


def get_tickers_with_historical_data(db_path: Path, min_quarters: int = 4) -> List[str]:
    """
    Get list of tickers with sufficient historical data.

    Args:
        db_path: Path to SQLite database
        min_quarters: Minimum number of quarters required

    Returns:
        List of ticker symbols
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get tickers with at least min_quarters of historical moves
        cursor.execute("""
            SELECT ticker, COUNT(*) as move_count
            FROM historical_moves
            GROUP BY ticker
            HAVING move_count >= ?
            ORDER BY move_count DESC
        """, (min_quarters,))

        tickers = [row[0] for row in cursor.fetchall()]

        conn.close()

        logger.info(f"✓ Found {len(tickers)} tickers with >= {min_quarters} quarters of data")

        return tickers

    except Exception as e:
        logger.error(f"Failed to get tickers: {e}")
        return []


def calculate_consistency_score(moves: List[float]) -> float:
    """
    Calculate consistency score from historical moves.

    Uses exponentially-weighted consistency metric similar to Phase 4.

    Args:
        moves: List of historical moves (most recent first)

    Returns:
        Consistency score (0-100)
    """
    if not moves or len(moves) < 2:
        return 0.0

    # Calculate mean and std dev
    mean_move = statistics.mean(moves)
    std_dev = statistics.stdev(moves)

    if mean_move == 0:
        return 0.0

    # Coefficient of variation (inverted for consistency)
    cv = std_dev / mean_move
    raw_consistency = max(0, 1 - cv)

    # Apply exponential weighting (recent quarters matter more)
    decay_factor = 0.85
    weights = [decay_factor ** i for i in range(len(moves))]
    weight_sum = sum(weights)

    weighted_consistency = sum(
        w * (1 - abs(move - mean_move) / mean_move if mean_move > 0 else 0)
        for w, move in zip(weights, moves)
    ) / weight_sum if weight_sum > 0 else 0

    # Combine raw and weighted (60/40)
    final_consistency = 0.6 * raw_consistency + 0.4 * weighted_consistency

    return min(100, max(0, final_consistency * 100))


def analyze_ticker_with_window(
    db_path: Path,
    ticker: str,
    quarters: int
) -> Optional[Dict]:
    """
    Analyze a ticker with a specific lookback window.

    Args:
        db_path: Path to SQLite database
        ticker: Ticker symbol
        quarters: Number of quarters to look back

    Returns:
        Analysis results or None if insufficient data
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # Get historical moves (most recent first)
        cursor.execute("""
            SELECT ABS(close_move_pct)
            FROM historical_moves
            WHERE ticker = ?
            ORDER BY earnings_date DESC
            LIMIT ?
        """, (ticker, quarters))

        moves = [row[0] for row in cursor.fetchall()]

        conn.close()

        if len(moves) < quarters:
            return None  # Insufficient data

        # Calculate metrics
        consistency = calculate_consistency_score(moves)
        avg_move = statistics.mean(moves)
        std_dev = statistics.stdev(moves) if len(moves) > 1 else 0

        return {
            'ticker': ticker,
            'move_count': len(moves),
            'consistency': consistency,
            'avg_move': avg_move,
            'std_dev': std_dev
        }

    except Exception as e:
        logger.warning(f"Failed to analyze {ticker} with {quarters} quarters: {e}")
        return None


def test_lookback_window(
    db_path: Path,
    config: LookbackConfig,
    test_tickers: List[str]
) -> LookbackResults:
    """
    Test a specific lookback window configuration.

    Args:
        db_path: Path to SQLite database
        config: Lookback configuration to test
        test_tickers: List of tickers to analyze

    Returns:
        Results from testing this configuration
    """
    logger.info(f"Testing: {config.name} ({config.quarters} quarters)...")

    results = []

    for ticker in test_tickers:
        result = analyze_ticker_with_window(db_path, ticker, config.quarters)
        if result:
            results.append(result)

    if not results:
        logger.warning(f"No results for {config.name}")
        return LookbackResults(
            config_name=config.name,
            quarters=config.quarters,
            tickers_analyzed=0,
            avg_consistency=0.0,
            avg_move_count=0,
            avg_std_dev=0.0,
            coverage_rate=0.0
        )

    # Calculate aggregate metrics
    avg_consistency = statistics.mean(r['consistency'] for r in results)
    avg_move_count = statistics.mean(r['move_count'] for r in results)
    avg_std_dev = statistics.mean(r['std_dev'] for r in results)
    coverage_rate = (len(results) / len(test_tickers)) * 100

    return LookbackResults(
        config_name=config.name,
        quarters=config.quarters,
        tickers_analyzed=len(results),
        avg_consistency=avg_consistency,
        avg_move_count=avg_move_count,
        avg_std_dev=avg_std_dev,
        coverage_rate=coverage_rate
    )


def print_results(results: List[LookbackResults]):
    """Print lookback window test results."""

    print("\n" + "="*80)
    print("HISTORICAL LOOKBACK WINDOW OPTIMIZATION")
    print("="*80)

    print("\n📊 LOOKBACK WINDOW COMPARISON:\n")
    print(f"{'Configuration':<25} {'Quarters':<10} {'Coverage':<12} {'Avg Consistency':<18} {'Avg Std Dev'}")
    print("─" * 80)

    for r in results:
        print(f"{r.config_name:<25} {r.quarters:<10} {r.coverage_rate:>6.1f}%      {r.avg_consistency:>12.1f}        {r.avg_std_dev:>10.2f}%")

    print("\n" + "="*80)

    # Find best configuration
    best_consistency = max(results, key=lambda x: x.avg_consistency)
    best_coverage = max(results, key=lambda x: x.coverage_rate)
    best_stability = min(results, key=lambda x: x.avg_std_dev)

    print("\n📈 KEY INSIGHTS:")
    print(f"  • Best Consistency: {best_consistency.config_name} ({best_consistency.avg_consistency:.1f})")
    print(f"  • Best Coverage: {best_coverage.config_name} ({best_coverage.coverage_rate:.1f}%)")
    print(f"  • Most Stable: {best_stability.config_name} (Std Dev: {best_stability.avg_std_dev:.2f}%)")

    # Analysis
    print("\n🔍 ANALYSIS:")

    # Check if consistency improves with shorter windows
    if results[0].avg_consistency > results[2].avg_consistency:
        diff = results[0].avg_consistency - results[2].avg_consistency
        print(f"  ✅ Shorter windows ({results[0].quarters}Q) show {diff:.1f} higher consistency vs baseline ({results[2].quarters}Q)")
        print(f"     → Recent data is more predictive for these tickers")
    elif results[2].avg_consistency > results[0].avg_consistency:
        diff = results[2].avg_consistency - results[0].avg_consistency
        print(f"  ✅ Baseline ({results[2].quarters}Q) shows {diff:.1f} higher consistency vs shorter windows ({results[0].quarters}Q)")
        print(f"     → More historical data provides better predictions")

    # Coverage trade-off
    coverage_diff = best_coverage.coverage_rate - results[0].coverage_rate
    if coverage_diff > 10:
        print(f"  ⚠️  Shorter windows reduce coverage by {coverage_diff:.1f}% (fewer tickers have data)")
        print(f"     → Trade-off: Better consistency vs fewer tradeable tickers")

    print("\n" + "="*80 + "\n")


def generate_recommendations(results: List[LookbackResults]) -> List[str]:
    """Generate actionable recommendations."""
    recommendations = []

    baseline = next((r for r in results if "Baseline" in r.config_name), None)
    if not baseline:
        recommendations.append("⚠️  No baseline results to compare against")
        return recommendations

    # Find best consistency
    best_consistency = max(results, key=lambda x: x.avg_consistency)

    if best_consistency.config_name != baseline.config_name:
        improvement = best_consistency.avg_consistency - baseline.avg_consistency

        if improvement > 5:
            recommendations.append(
                f"✅ {best_consistency.config_name} shows {improvement:.1f} better consistency than baseline. "
                f"Consider using {best_consistency.quarters} quarters for VRP calculations."
            )
        else:
            recommendations.append(
                f"✅ {best_consistency.config_name} is slightly better (+{improvement:.1f}) but difference is marginal. "
                f"Keep baseline ({baseline.quarters}Q) for simplicity."
            )
    else:
        recommendations.append(
            f"✅ Current baseline ({baseline.quarters}Q) is optimal. No changes needed."
        )

    # Check coverage impact
    best_consistency_coverage = best_consistency.coverage_rate
    baseline_coverage = baseline.coverage_rate

    if best_consistency_coverage < baseline_coverage - 10:
        coverage_loss = baseline_coverage - best_consistency_coverage
        recommendations.append(
            f"⚠️  Using {best_consistency.quarters}Q reduces coverage by {coverage_loss:.1f}%. "
            f"Consider adaptive approach: {best_consistency.quarters}Q when available, else {baseline.quarters}Q."
        )

    # Adaptive recommendation
    recommendations.append(
        "💡 Future Enhancement: Implement adaptive lookback windows based on ticker characteristics. "
        "High-consistency tickers → shorter window, erratic tickers → longer window."
    )

    return recommendations


def save_results(results: List[LookbackResults], output_path: Path):
    """Save results to JSON file."""
    output = {
        'analysis_date': datetime.now().isoformat(),
        'configurations': []
    }

    for r in results:
        output['configurations'].append({
            'name': r.config_name,
            'quarters': r.quarters,
            'tickers_analyzed': r.tickers_analyzed,
            'avg_consistency': r.avg_consistency,
            'avg_move_count': r.avg_move_count,
            'avg_std_dev': r.avg_std_dev,
            'coverage_rate': r.coverage_rate
        })

    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    logger.info(f"✓ Results saved to {output_path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Optimize historical lookback window')
    parser.add_argument('--db', type=Path, default=Path('data/ivcrush.db'),
                      help='Path to SQLite database')
    parser.add_argument('--output', '-o', type=Path,
                      default=Path('results/lookback_analysis.json'),
                      help='Output path for results')
    parser.add_argument('--min-quarters', type=int, default=4,
                      help='Minimum quarters required for ticker inclusion')

    args = parser.parse_args()

    # Setup logging
    setup_logging()

    logger.info("="*80)
    logger.info("HISTORICAL LOOKBACK WINDOW OPTIMIZATION")
    logger.info("="*80)

    # Check database
    if not args.db.exists():
        logger.error(f"Database not found: {args.db}")
        return 1

    # Get test tickers
    test_tickers = get_tickers_with_historical_data(args.db, args.min_quarters)

    if not test_tickers:
        logger.error("No tickers with sufficient historical data")
        return 1

    # Create test configurations
    configs = create_test_configs()

    # Test each configuration
    results = []
    for config in configs:
        result = test_lookback_window(args.db, config, test_tickers)
        results.append(result)

    # Print results
    print_results(results)

    # Generate recommendations
    recommendations = generate_recommendations(results)
    print("\n💡 RECOMMENDATIONS:")
    for i, rec in enumerate(recommendations, 1):
        print(f"\n{i}. {rec}")

    # Save results
    args.output.parent.mkdir(parents=True, exist_ok=True)
    save_results(results, args.output)

    logger.info("\n✓ Lookback window optimization complete!")

    return 0


if __name__ == '__main__':
    sys.exit(main())
