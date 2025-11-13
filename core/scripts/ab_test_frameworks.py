#!/usr/bin/env python3
"""
A/B Test: Old Framework (Phase 3) vs New Framework (Phase 4)

Compares the performance of:
- Phase 3: Basic VRP, simple skew, unweighted consistency
- Phase 4: Interpolated move, polynomial skew, exponential-weighted consistency

Usage:
    python scripts/ab_test_frameworks.py --start-date 2024-01-01 --end-date 2024-12-31
"""

import sys
import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple
import sqlite3
from dataclasses import dataclass

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.config.config import Config, AlgorithmConfig
from src.container import Container
from src.domain.types import TickerAnalysis, ImpliedMove, VRPResult

logger = logging.getLogger(__name__)


@dataclass
class FrameworkResult:
    """Results from testing a framework configuration."""
    framework_name: str
    total_analyzed: int
    successful_analyses: int
    avg_vrp_ratio: float
    tradeable_count: int
    avg_implied_move: float
    avg_historical_move: float
    avg_edge_score: float
    algorithm_config: AlgorithmConfig


def get_historical_earnings(db_path: Path, start_date: str, end_date: str) -> List[Tuple[str, str]]:
    """
    Get all historical earnings events in date range.

    Returns:
        List of (ticker, earnings_date) tuples
    """
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    query = """
        SELECT DISTINCT ticker, earnings_date
        FROM historical_moves
        WHERE earnings_date BETWEEN ? AND ?
        ORDER BY earnings_date, ticker
    """

    cursor.execute(query, (start_date, end_date))
    results = cursor.fetchall()
    conn.close()

    return results


def analyze_with_framework(
    ticker: str,
    earnings_date: str,
    algorithm_config: AlgorithmConfig,
    base_config: Config
) -> Tuple[bool, Dict]:
    """
    Analyze a ticker with a specific algorithm configuration.

    Returns:
        (success, analysis_dict)
    """
    from datetime import datetime, timedelta

    # Create config with algorithm overrides
    config = Config(
        api=base_config.api,
        database=base_config.database,
        cache=base_config.cache,
        thresholds=base_config.thresholds,
        rate_limits=base_config.rate_limits,
        resilience=base_config.resilience,
        algorithms=algorithm_config,
        logging=base_config.logging
    )

    # Create container with this config
    container = Container(config)

    try:
        # Calculate expiration (earnings_date + 1 day)
        earnings_dt = datetime.strptime(earnings_date, "%Y-%m-%d").date()
        expiration = earnings_dt + timedelta(days=1)

        # Get calculators
        implied_calc = container.implied_move_calculator
        vrp_calc = container.vrp_calculator
        prices_repo = container.prices_repository

        # Calculate implied move
        implied_result = implied_calc.calculate(ticker, expiration)
        if implied_result.is_err:
            return False, {"error": "Implied move failed"}

        implied_move = implied_result.value

        # Get historical moves
        hist_result = prices_repo.get_historical_moves(ticker, limit=12)
        if hist_result.is_err:
            return False, {"error": "No historical data"}

        historical_moves = hist_result.value

        # Calculate VRP
        vrp_result = vrp_calc.calculate(
            ticker=ticker,
            expiration=expiration,
            implied_move=implied_move,
            historical_moves=historical_moves
        )

        if vrp_result.is_err:
            return False, {"error": "VRP calculation failed"}

        vrp = vrp_result.value

        return True, {
            "ticker": ticker,
            "earnings_date": earnings_date,
            "implied_move_pct": float(vrp.implied_move_pct.value),
            "historical_mean_pct": float(vrp.historical_mean_move_pct.value),
            "vrp_ratio": float(vrp.vrp_ratio),
            "edge_score": float(vrp.edge_score),
            "is_tradeable": vrp.is_tradeable,
            "recommendation": vrp.recommendation.value
        }

    except Exception as e:
        logger.debug(f"Analysis failed for {ticker}: {e}")
        return False, {"error": str(e)}


def test_framework(
    framework_name: str,
    algorithm_config: AlgorithmConfig,
    earnings_events: List[Tuple[str, str]],
    base_config: Config
) -> FrameworkResult:
    """
    Test a framework configuration on all earnings events.
    """
    logger.info(f"\n{'=' * 80}")
    logger.info(f"Testing Framework: {framework_name}")
    logger.info(f"{'=' * 80}")
    logger.info(f"Events to analyze: {len(earnings_events)}")

    successful = 0
    analyses = []

    for i, (ticker, earnings_date) in enumerate(earnings_events):
        if (i + 1) % 20 == 0:
            logger.info(f"Progress: {i + 1}/{len(earnings_events)}")

        success, result = analyze_with_framework(
            ticker, earnings_date, algorithm_config, base_config
        )

        if success:
            successful += 1
            analyses.append(result)

    # Calculate aggregate metrics
    if analyses:
        avg_vrp = sum(a["vrp_ratio"] for a in analyses) / len(analyses)
        avg_implied = sum(a["implied_move_pct"] for a in analyses) / len(analyses)
        avg_historical = sum(a["historical_mean_pct"] for a in analyses) / len(analyses)
        avg_edge = sum(a["edge_score"] for a in analyses) / len(analyses)
        tradeable = sum(1 for a in analyses if a["is_tradeable"])
    else:
        avg_vrp = avg_implied = avg_historical = avg_edge = tradeable = 0

    return FrameworkResult(
        framework_name=framework_name,
        total_analyzed=len(earnings_events),
        successful_analyses=successful,
        avg_vrp_ratio=avg_vrp,
        tradeable_count=tradeable,
        avg_implied_move=avg_implied,
        avg_historical_move=avg_historical,
        avg_edge_score=avg_edge,
        algorithm_config=algorithm_config
    )


def print_comparison(old_result: FrameworkResult, new_result: FrameworkResult):
    """Print side-by-side comparison of framework results."""

    print("\n" + "=" * 120)
    print("A/B TEST RESULTS: OLD FRAMEWORK (Phase 3) vs NEW FRAMEWORK (Phase 4)")
    print("=" * 120)

    # Configuration comparison
    print("\nALGORITHM CONFIGURATION:")
    print("-" * 120)
    print(f"{'Metric':<40} {'Old Framework':<25} {'New Framework':<25} {'Change':<20}")
    print("-" * 120)

    print(f"{'Interpolated Implied Move':<40} "
          f"{str(old_result.algorithm_config.use_interpolated_move):<25} "
          f"{str(new_result.algorithm_config.use_interpolated_move):<25} "
          f"{'✓ Enhanced' if new_result.algorithm_config.use_interpolated_move else '':<20}")

    print(f"{'Enhanced Skew (Polynomial)':<40} "
          f"{str(old_result.algorithm_config.use_enhanced_skew):<25} "
          f"{str(new_result.algorithm_config.use_enhanced_skew):<25} "
          f"{'✓ Enhanced' if new_result.algorithm_config.use_enhanced_skew else '':<20}")

    print(f"{'Enhanced Consistency (Exponential)':<40} "
          f"{str(old_result.algorithm_config.use_enhanced_consistency):<25} "
          f"{str(new_result.algorithm_config.use_enhanced_consistency):<25} "
          f"{'✓ Enhanced' if new_result.algorithm_config.use_enhanced_consistency else '':<20}")

    # Performance metrics
    print("\n" + "=" * 120)
    print("PERFORMANCE COMPARISON:")
    print("-" * 120)
    print(f"{'Metric':<40} {'Old Framework':<25} {'New Framework':<25} {'Improvement':<20}")
    print("-" * 120)

    # Success rate
    old_success_rate = (old_result.successful_analyses / old_result.total_analyzed * 100) if old_result.total_analyzed > 0 else 0
    new_success_rate = (new_result.successful_analyses / new_result.total_analyzed * 100) if new_result.total_analyzed > 0 else 0
    success_diff = new_success_rate - old_success_rate

    print(f"{'Analysis Success Rate':<40} "
          f"{old_success_rate:>6.1f}% ({old_result.successful_analyses}/{old_result.total_analyzed}){'':<9} "
          f"{new_success_rate:>6.1f}% ({new_result.successful_analyses}/{new_result.total_analyzed}){'':<9} "
          f"{'+' if success_diff > 0 else ''}{success_diff:>6.1f}%{'':<11}")

    # Average VRP ratio
    vrp_diff = new_result.avg_vrp_ratio - old_result.avg_vrp_ratio
    vrp_pct_change = (vrp_diff / old_result.avg_vrp_ratio * 100) if old_result.avg_vrp_ratio > 0 else 0

    print(f"{'Average VRP Ratio':<40} "
          f"{old_result.avg_vrp_ratio:>8.3f}x{'':<16} "
          f"{new_result.avg_vrp_ratio:>8.3f}x{'':<16} "
          f"{'+' if vrp_diff > 0 else ''}{vrp_pct_change:>6.1f}%{'':<11}")

    # Tradeable opportunities
    old_tradeable_rate = (old_result.tradeable_count / old_result.successful_analyses * 100) if old_result.successful_analyses > 0 else 0
    new_tradeable_rate = (new_result.tradeable_count / new_result.successful_analyses * 100) if new_result.successful_analyses > 0 else 0
    tradeable_diff = new_tradeable_rate - old_tradeable_rate

    print(f"{'Tradeable Opportunities':<40} "
          f"{old_tradeable_rate:>6.1f}% ({old_result.tradeable_count}/{old_result.successful_analyses}){'':<9} "
          f"{new_tradeable_rate:>6.1f}% ({new_result.tradeable_count}/{new_result.successful_analyses}){'':<9} "
          f"{'+' if tradeable_diff > 0 else ''}{tradeable_diff:>6.1f}%{'':<11}")

    # Average implied move
    implied_diff = new_result.avg_implied_move - old_result.avg_implied_move
    implied_pct_change = (implied_diff / old_result.avg_implied_move * 100) if old_result.avg_implied_move > 0 else 0

    print(f"{'Avg Implied Move':<40} "
          f"{old_result.avg_implied_move:>7.2f}%{'':<17} "
          f"{new_result.avg_implied_move:>7.2f}%{'':<17} "
          f"{'+' if implied_diff > 0 else ''}{implied_pct_change:>6.1f}%{'':<11}")

    # Average edge score
    edge_diff = new_result.avg_edge_score - old_result.avg_edge_score
    edge_pct_change = (edge_diff / old_result.avg_edge_score * 100) if old_result.avg_edge_score > 0 else 0

    print(f"{'Avg Edge Score':<40} "
          f"{old_result.avg_edge_score:>8.3f}{'':<17} "
          f"{new_result.avg_edge_score:>8.3f}{'':<17} "
          f"{'+' if edge_diff > 0 else ''}{edge_pct_change:>6.1f}%{'':<11}")

    print("=" * 120)

    # Summary
    print("\nKEY INSIGHTS:")
    print("-" * 120)

    improvements = []

    if success_diff > 0:
        improvements.append(f"✓ Analysis success rate improved by {success_diff:.1f}%")

    if vrp_diff > 0:
        improvements.append(f"✓ VRP ratio increased by {vrp_pct_change:.1f}% (better edge detection)")

    if tradeable_diff > 0:
        improvements.append(f"✓ Tradeable opportunities increased by {tradeable_diff:.1f}%")

    if edge_diff > 0:
        improvements.append(f"✓ Edge score improved by {edge_pct_change:.1f}% (better risk/reward)")

    if improvements:
        for improvement in improvements:
            print(f"  {improvement}")
    else:
        print("  No significant improvements detected")

    # Recommendation
    print("\nRECOMMENDATION:")
    print("-" * 120)

    total_improvement_score = (
        (success_diff * 0.2) +
        (vrp_pct_change * 0.4) +
        (tradeable_diff * 0.2) +
        (edge_pct_change * 0.2)
    )

    if total_improvement_score > 5:
        print("  ✅ NEW FRAMEWORK (Phase 4) shows significant improvements")
        print("  → Recommend using Phase 4 enhanced algorithms for production")
    elif total_improvement_score > 0:
        print("  ⚠️  NEW FRAMEWORK (Phase 4) shows modest improvements")
        print("  → Consider using Phase 4 with monitoring")
    else:
        print("  ❌ OLD FRAMEWORK (Phase 3) performs better or equivalent")
        print("  → Stick with Phase 3 algorithms")

    print("\n")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="A/B test old vs new algorithm frameworks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Compares:
  • Old Framework (Phase 3): Basic algorithms without enhancements
  • New Framework (Phase 4): Interpolated move, polynomial skew, exponential consistency

Example:
    python scripts/ab_test_frameworks.py --start-date 2024-01-01 --end-date 2024-12-31
        """,
    )

    parser.add_argument(
        "--start-date",
        type=str,
        required=True,
        help="Start date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        required=True,
        help="End date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="data/ivcrush.db",
        help="Path to database (default: data/ivcrush.db)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    args = parser.parse_args()

    # Setup logging
    setup_logging(level=args.log_level)

    logger.info("=" * 80)
    logger.info("IV Crush 2.0 - Framework A/B Testing")
    logger.info("=" * 80)
    logger.info(f"Period: {args.start_date} to {args.end_date}")
    logger.info(f"Database: {args.db_path}")
    logger.info("=" * 80)

    # Verify database exists
    db_path = Path(args.db_path)
    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        logger.error("Run backfill first: python scripts/backfill_yfinance.py")
        return 1

    # Get historical earnings events
    logger.info("\nFetching historical earnings events...")
    earnings_events = get_historical_earnings(db_path, args.start_date, args.end_date)
    logger.info(f"Found {len(earnings_events)} earnings events")

    if not earnings_events:
        logger.error("No earnings events found in date range")
        return 1

    # Load base config
    base_config = Config.from_env()

    # Define framework configurations
    old_framework_config = AlgorithmConfig(
        use_interpolated_move=False,
        use_enhanced_skew=False,
        use_enhanced_consistency=False,
        skew_min_points=5,
        consistency_decay_factor=0.85,
        interpolation_tolerance=0.01
    )

    new_framework_config = AlgorithmConfig(
        use_interpolated_move=True,
        use_enhanced_skew=True,
        use_enhanced_consistency=True,
        skew_min_points=5,
        consistency_decay_factor=0.85,
        interpolation_tolerance=0.01
    )

    # Test old framework
    old_result = test_framework(
        "Phase 3 (Old Framework)",
        old_framework_config,
        earnings_events,
        base_config
    )

    # Test new framework
    new_result = test_framework(
        "Phase 4 (New Framework)",
        new_framework_config,
        earnings_events,
        base_config
    )

    # Print comparison
    print_comparison(old_result, new_result)

    return 0


if __name__ == "__main__":
    sys.exit(main())
