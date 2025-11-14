#!/usr/bin/env python3
"""
Decay Factor Optimization.

Tests different exponential decay factors for historical consistency scoring:
- Current: 0.85 (recent quarters weighted more heavily)
- Tests: 0.75, 0.80, 0.85, 0.90, 0.95, 1.00 (no decay)

Hypothesis: Optimal decay factor balances recency with historical stability.
Too high (0.95+) = overfits to recent data
Too low (0.75-) = ignores recent patterns

Usage:
    python scripts/optimize_decay_factor.py --db data/ivcrush.db
"""

import sys
import argparse
import logging
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass
import statistics

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)


@dataclass
class DecayConfig:
    """Configuration for decay factor testing."""
    name: str
    decay_factor: float


@dataclass
class DecayResults:
    """Results from testing a decay configuration."""
    config_name: str
    decay_factor: float
    tickers_analyzed: int
    avg_consistency: float
    avg_weighted_std: float
    avg_cv: float  # Coefficient of variation


def create_test_configs() -> List[DecayConfig]:
    """Create test configurations for decay factors."""
    return [
        DecayConfig("No Decay (Equal Weights)", decay_factor=1.00),
        DecayConfig("Very Slow Decay", decay_factor=0.95),
        DecayConfig("Slow Decay", decay_factor=0.90),
        DecayConfig("Baseline Decay", decay_factor=0.85),
        DecayConfig("Fast Decay", decay_factor=0.80),
        DecayConfig("Very Fast Decay", decay_factor=0.75),
    ]


def calculate_consistency_with_decay(moves: List[float], decay_factor: float) -> Dict[str, float]:
    """
    Calculate consistency score with specific decay factor.

    Args:
        moves: List of historical moves (most recent first)
        decay_factor: Exponential decay factor (0-1)

    Returns:
        Dictionary with consistency metrics
    """
    if not moves or len(moves) < 2:
        return {
            'consistency': 0.0,
            'weighted_std': 0.0,
            'cv': 0.0
        }

    # Calculate basic statistics
    mean_move = statistics.mean(moves)
    std_dev = statistics.stdev(moves)

    if mean_move == 0:
        return {
            'consistency': 0.0,
            'weighted_std': 0.0,
            'cv': 0.0
        }

    # Coefficient of variation
    cv = std_dev / mean_move

    # Apply exponential weighting
    weights = [decay_factor ** i for i in range(len(moves))]
    weight_sum = sum(weights)

    # Weighted mean
    weighted_mean = sum(w * m for w, m in zip(weights, moves)) / weight_sum

    # Weighted std dev
    weighted_variance = sum(
        w * ((m - weighted_mean) ** 2)
        for w, m in zip(weights, moves)
    ) / weight_sum
    weighted_std = weighted_variance ** 0.5

    # Weighted consistency (how close each move is to mean)
    weighted_consistency = sum(
        w * (1 - abs(move - weighted_mean) / weighted_mean if weighted_mean > 0 else 0)
        for w, move in zip(weights, moves)
    ) / weight_sum if weight_sum > 0 else 0

    # Final consistency score (0-100)
    raw_consistency = max(0, 1 - cv)
    final_consistency = 0.6 * raw_consistency + 0.4 * weighted_consistency

    return {
        'consistency': min(100, max(0, final_consistency * 100)),
        'weighted_std': weighted_std,
        'cv': cv
    }


def get_tickers_with_data(db_path: Path, min_quarters: int = 8) -> List[str]:
    """
    Get tickers with sufficient historical data.

    Args:
        db_path: Path to SQLite database
        min_quarters: Minimum quarters required

    Returns:
        List of ticker symbols
    """
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute("""
            SELECT ticker, COUNT(*) as move_count
            FROM historical_moves
            GROUP BY ticker
            HAVING move_count >= ?
            ORDER BY move_count DESC
        """, (min_quarters,))

        tickers = [row[0] for row in cursor.fetchall()]

        conn.close()

        logger.info(f"✓ Found {len(tickers)} tickers with >= {min_quarters} quarters")

        return tickers

    except Exception as e:
        logger.error(f"Failed to get tickers: {e}")
        return []


def analyze_ticker_with_decay(
    db_path: Path,
    ticker: str,
    decay_factor: float,
    quarters: int = 12
) -> Optional[Dict]:
    """
    Analyze ticker with specific decay factor.

    Args:
        db_path: Path to database
        ticker: Ticker symbol
        decay_factor: Decay factor to use
        quarters: Number of quarters to analyze

    Returns:
        Analysis results or None
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
            return None

        # Calculate metrics with this decay factor
        metrics = calculate_consistency_with_decay(moves, decay_factor)

        return {
            'ticker': ticker,
            'move_count': len(moves),
            **metrics
        }

    except Exception as e:
        logger.warning(f"Failed to analyze {ticker}: {e}")
        return None


def test_decay_factor(
    db_path: Path,
    config: DecayConfig,
    test_tickers: List[str],
    quarters: int = 12
) -> DecayResults:
    """
    Test a specific decay factor configuration.

    Args:
        db_path: Path to database
        config: Decay configuration
        test_tickers: Tickers to test
        quarters: Quarters to analyze

    Returns:
        Results from testing
    """
    logger.info(f"Testing: {config.name} (decay={config.decay_factor:.2f})...")

    results = []

    for ticker in test_tickers:
        result = analyze_ticker_with_decay(
            db_path, ticker, config.decay_factor, quarters
        )
        if result:
            results.append(result)

    if not results:
        return DecayResults(
            config_name=config.name,
            decay_factor=config.decay_factor,
            tickers_analyzed=0,
            avg_consistency=0.0,
            avg_weighted_std=0.0,
            avg_cv=0.0
        )

    # Aggregate metrics
    avg_consistency = statistics.mean(r['consistency'] for r in results)
    avg_weighted_std = statistics.mean(r['weighted_std'] for r in results)
    avg_cv = statistics.mean(r['cv'] for r in results)

    return DecayResults(
        config_name=config.name,
        decay_factor=config.decay_factor,
        tickers_analyzed=len(results),
        avg_consistency=avg_consistency,
        avg_weighted_std=avg_weighted_std,
        avg_cv=avg_cv
    )


def print_results(results: List[DecayResults]):
    """Print decay factor test results."""

    print("\n" + "="*80)
    print("DECAY FACTOR OPTIMIZATION")
    print("="*80)

    print("\n📊 DECAY FACTOR COMPARISON:\n")
    print(f"{'Configuration':<30} {'Decay':<8} {'Consistency':<14} {'Weighted Std':<14} {'CV'}")
    print("─" * 80)

    for r in results:
        print(f"{r.config_name:<30} {r.decay_factor:<8.2f} {r.avg_consistency:>10.2f}    {r.avg_weighted_std:>10.2f}%    {r.avg_cv:>10.3f}")

    print("\n" + "="*80)

    # Find best configuration
    best_consistency = max(results, key=lambda x: x.avg_consistency)
    best_stability = min(results, key=lambda x: x.avg_cv)
    baseline = next((r for r in results if "Baseline" in r.config_name), None)

    print("\n📈 KEY INSIGHTS:")
    print(f"  • Best Consistency: {best_consistency.config_name} ({best_consistency.avg_consistency:.2f})")
    print(f"  • Most Stable (low CV): {best_stability.config_name} (CV: {best_stability.avg_cv:.3f})")

    if baseline:
        print(f"  • Baseline: {baseline.config_name} (Consistency: {baseline.avg_consistency:.2f}, Decay: {baseline.decay_factor:.2f})")

    # Analysis
    print("\n🔍 ANALYSIS:")

    if baseline and best_consistency.config_name != baseline.config_name:
        diff = best_consistency.avg_consistency - baseline.avg_consistency
        if diff > 2:
            print(f"  ✅ {best_consistency.config_name} shows {diff:.2f} better consistency than baseline")
            print(f"     → Decay={best_consistency.decay_factor:.2f} is more optimal")
        else:
            print(f"  ✅ Difference is marginal ({diff:.2f}). Baseline is adequate.")
    elif baseline:
        print(f"  ✅ Baseline decay (0.85) is optimal")

    # Check if no decay is best
    no_decay = next((r for r in results if r.decay_factor == 1.00), None)
    if no_decay and best_consistency.decay_factor == 1.00:
        print(f"  ⚠️  No decay (equal weights) performs best")
        print(f"     → Recent quarters may not be more predictive than historical")

    print("\n" + "="*80 + "\n")


def generate_recommendations(results: List[DecayResults]) -> List[str]:
    """Generate actionable recommendations."""
    recommendations = []

    baseline = next((r for r in results if "Baseline" in r.config_name), None)
    best_consistency = max(results, key=lambda x: x.avg_consistency)

    if not baseline:
        recommendations.append("⚠️  No baseline found for comparison")
        return recommendations

    # Compare to baseline
    diff = best_consistency.avg_consistency - baseline.avg_consistency

    if best_consistency.config_name != baseline.config_name:
        if diff > 3:
            recommendations.append(
                f"✅ {best_consistency.config_name} (decay={best_consistency.decay_factor:.2f}) shows "
                f"{diff:.2f} better consistency. Consider updating decay factor from 0.85 to {best_consistency.decay_factor:.2f}."
            )
        else:
            recommendations.append(
                f"✅ {best_consistency.config_name} is marginally better (+{diff:.2f}). "
                f"Keep baseline (0.85) for simplicity."
            )
    else:
        recommendations.append(
            f"✅ Current baseline decay (0.85) is optimal. No changes needed."
        )

    # Check if no decay is surprisingly good
    no_decay = next((r for r in results if r.decay_factor == 1.00), None)
    if no_decay:
        no_decay_diff = baseline.avg_consistency - no_decay.avg_consistency
        if abs(no_decay_diff) < 2:
            recommendations.append(
                f"📊 Equal weighting (no decay) performs similarly to exponential decay. "
                f"Suggests historical patterns are relatively stable over time."
            )

    # Check for strong recency bias
    very_fast = next((r for r in results if r.decay_factor == 0.75), None)
    if very_fast and very_fast.avg_consistency > baseline.avg_consistency + 3:
        recommendations.append(
            f"💡 Very fast decay (0.75) significantly better. "
            f"Market conditions may be changing rapidly - recent data much more predictive."
        )

    return recommendations


def save_results(results: List[DecayResults], output_path: Path):
    """Save results to JSON."""
    output = {
        'analysis_date': datetime.now().isoformat(),
        'configurations': []
    }

    for r in results:
        output['configurations'].append({
            'name': r.config_name,
            'decay_factor': r.decay_factor,
            'tickers_analyzed': r.tickers_analyzed,
            'avg_consistency': r.avg_consistency,
            'avg_weighted_std': r.avg_weighted_std,
            'avg_cv': r.avg_cv
        })

    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2)

    logger.info(f"✓ Results saved to {output_path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Optimize exponential decay factor')
    parser.add_argument('--db', type=Path, default=Path('data/ivcrush.db'),
                      help='Path to SQLite database')
    parser.add_argument('--output', '-o', type=Path,
                      default=Path('results/decay_analysis.json'),
                      help='Output path for results')
    parser.add_argument('--quarters', type=int, default=12,
                      help='Number of quarters to analyze')
    parser.add_argument('--min-quarters', type=int, default=8,
                      help='Minimum quarters required')

    args = parser.parse_args()

    # Setup logging
    setup_logging()

    logger.info("="*80)
    logger.info("DECAY FACTOR OPTIMIZATION")
    logger.info("="*80)

    # Check database
    if not args.db.exists():
        logger.error(f"Database not found: {args.db}")
        return 1

    # Get test tickers
    test_tickers = get_tickers_with_data(args.db, args.min_quarters)

    if not test_tickers:
        logger.error("No tickers with sufficient data")
        return 1

    # Create test configurations
    configs = create_test_configs()

    # Test each configuration
    results = []
    for config in configs:
        result = test_decay_factor(
            args.db, config, test_tickers, args.quarters
        )
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

    logger.info("\n✓ Decay factor optimization complete!")

    return 0


if __name__ == '__main__':
    sys.exit(main())
