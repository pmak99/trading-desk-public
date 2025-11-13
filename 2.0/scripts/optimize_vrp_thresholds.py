#!/usr/bin/env python3
"""
Optimize VRP thresholds through grid search.

Tests different VRP threshold configurations to find the optimal
balance between trade selection and performance.

Usage:
    python scripts/optimize_vrp_thresholds.py --start-date 2024-01-01 --end-date 2024-12-31
"""

import sys
import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict
from dataclasses import dataclass

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.config.scoring_config import ScoringConfig, ScoringWeights, ScoringThresholds
from src.application.services.backtest_engine import BacktestEngine, BacktestResult

logger = logging.getLogger(__name__)


@dataclass
class ThresholdConfig:
    """VRP threshold configuration to test."""
    name: str
    vrp_good: float
    vrp_excellent: float
    vrp_marginal: float


def create_test_configs() -> List[ThresholdConfig]:
    """
    Create test configurations for VRP thresholds.

    Returns:
        List of threshold configurations to test
    """
    return [
        # More permissive - catch more opportunities
        ThresholdConfig("Very Permissive", vrp_good=1.3, vrp_excellent=1.8, vrp_marginal=1.1),
        ThresholdConfig("Permissive", vrp_good=1.4, vrp_excellent=1.9, vrp_marginal=1.2),

        # Current baseline
        ThresholdConfig("Baseline (Current)", vrp_good=1.5, vrp_excellent=2.0, vrp_marginal=1.2),

        # More strict - higher quality trades
        ThresholdConfig("Strict", vrp_good=1.6, vrp_excellent=2.1, vrp_marginal=1.3),
        ThresholdConfig("Very Strict", vrp_good=1.7, vrp_excellent=2.2, vrp_marginal=1.4),

        # Aggressive sweet spot
        ThresholdConfig("Sweet Spot Low", vrp_good=1.45, vrp_excellent=1.95, vrp_marginal=1.15),
        ThresholdConfig("Sweet Spot High", vrp_good=1.55, vrp_excellent=2.05, vrp_marginal=1.25),
    ]


def create_scoring_config(threshold_config: ThresholdConfig, base_weights: ScoringWeights) -> ScoringConfig:
    """
    Create a ScoringConfig with custom VRP thresholds.

    Args:
        threshold_config: VRP threshold configuration
        base_weights: Base scoring weights to use

    Returns:
        ScoringConfig instance
    """
    thresholds = ScoringThresholds(
        vrp_excellent=threshold_config.vrp_excellent,
        vrp_good=threshold_config.vrp_good,
        vrp_marginal=threshold_config.vrp_marginal,
    )

    return ScoringConfig(
        name=threshold_config.name,
        description=f"VRP thresholds: Good={threshold_config.vrp_good:.2f}x, Excellent={threshold_config.vrp_excellent:.2f}x",
        weights=base_weights,
        thresholds=thresholds,
        max_positions=10,
        min_score=60.0
    )


def print_results_table(results: List[BacktestResult]):
    """Print formatted results table."""

    print("\n" + "=" * 140)
    print("VRP THRESHOLD OPTIMIZATION RESULTS")
    print("=" * 140)

    # Header
    header = (
        f"{'Configuration':<25} "
        f"{'VRP Good':<10} "
        f"{'VRP Exc':<10} "
        f"{'Trades':<8} "
        f"{'Win%':<8} "
        f"{'Avg P&L':<10} "
        f"{'Total P&L':<10} "
        f"{'Sharpe':<8} "
        f"{'Max DD':<10}"
    )
    print(header)
    print("-" * 140)

    # Sort by Sharpe ratio
    sorted_results = sorted(results, key=lambda r: r.sharpe_ratio, reverse=True)

    for result in sorted_results:
        # Extract thresholds from description
        desc = result.config_description
        vrp_good = desc.split("Good=")[1].split("x")[0] if "Good=" in desc else "N/A"
        vrp_exc = desc.split("Excellent=")[1].split("x")[0] if "Excellent=" in desc else "N/A"

        row = (
            f"{result.config_name:<25} "
            f"{vrp_good:<10} "
            f"{vrp_exc:<10} "
            f"{result.selected_trades:<8} "
            f"{result.win_rate:>6.1f}%  "
            f"{result.avg_pnl_per_trade:>8.2f}%  "
            f"{result.total_pnl:>8.2f}%  "
            f"{result.sharpe_ratio:>7.2f}  "
            f"{result.max_drawdown:>8.2f}%"
        )
        print(row)

    print("=" * 140)


def print_analysis(results: List[BacktestResult]):
    """Print detailed analysis of results."""

    print("\n" + "=" * 100)
    print("KEY INSIGHTS")
    print("=" * 100)

    # Best performers
    best_sharpe = max(results, key=lambda r: r.sharpe_ratio)
    best_winrate = max(results, key=lambda r: r.win_rate)
    best_total_pnl = max(results, key=lambda r: r.total_pnl)
    most_trades = max(results, key=lambda r: r.selected_trades)

    print(f"\n✓ Best Sharpe Ratio: {best_sharpe.config_name} ({best_sharpe.sharpe_ratio:.2f})")
    print(f"  Thresholds: {best_sharpe.config_description}")
    print(f"  Trades: {best_sharpe.selected_trades}, Win Rate: {best_sharpe.win_rate:.1f}%")

    print(f"\n✓ Best Win Rate: {best_winrate.config_name} ({best_winrate.win_rate:.1f}%)")
    print(f"  Thresholds: {best_winrate.config_description}")
    print(f"  Sharpe: {best_winrate.sharpe_ratio:.2f}, Trades: {best_winrate.selected_trades}")

    print(f"\n✓ Best Total P&L: {best_total_pnl.config_name} ({best_total_pnl.total_pnl:.2f}%)")
    print(f"  Thresholds: {best_total_pnl.config_description}")
    print(f"  Sharpe: {best_total_pnl.sharpe_ratio:.2f}, Trades: {best_total_pnl.selected_trades}")

    print(f"\n✓ Most Trades: {most_trades.config_name} ({most_trades.selected_trades} trades)")
    print(f"  Thresholds: {most_trades.config_description}")
    print(f"  Sharpe: {most_trades.sharpe_ratio:.2f}, Win Rate: {most_trades.win_rate:.1f}%")

    # Trade-off analysis
    print("\n" + "=" * 100)
    print("TRADE-OFF ANALYSIS")
    print("=" * 100)

    # Sort by VRP good threshold
    by_threshold = sorted(results, key=lambda r: float(r.config_description.split("Good=")[1].split("x")[0]))

    print("\nAs VRP thresholds increase (more strict):")
    print(f"  • Trades: {by_threshold[0].selected_trades} → {by_threshold[-1].selected_trades}")
    print(f"  • Win Rate: {by_threshold[0].win_rate:.1f}% → {by_threshold[-1].win_rate:.1f}%")
    print(f"  • Avg P&L: {by_threshold[0].avg_pnl_per_trade:.2f}% → {by_threshold[-1].avg_pnl_per_trade:.2f}%")
    print(f"  • Sharpe: {by_threshold[0].sharpe_ratio:.2f} → {by_threshold[-1].sharpe_ratio:.2f}")

    # Recommendation
    print("\n" + "=" * 100)
    print("RECOMMENDATION")
    print("=" * 100)

    # Find sweet spot: balance Sharpe, trades, and win rate
    def score_config(r: BacktestResult) -> float:
        """Composite score for recommendation."""
        return (
            r.sharpe_ratio * 0.4 +
            (r.win_rate / 100) * 0.3 +
            (r.selected_trades / 15) * 0.3  # Normalize to ~15 trades
        )

    recommended = max(results, key=score_config)

    print(f"\n✅ RECOMMENDED: {recommended.config_name}")
    print(f"   {recommended.config_description}")
    print(f"\n   Performance:")
    print(f"   • Sharpe Ratio: {recommended.sharpe_ratio:.2f}")
    print(f"   • Win Rate: {recommended.win_rate:.1f}%")
    print(f"   • Avg P&L per Trade: {recommended.avg_pnl_per_trade:.2f}%")
    print(f"   • Total P&L: {recommended.total_pnl:.2f}%")
    print(f"   • Trades: {recommended.selected_trades}")
    print(f"   • Max Drawdown: {recommended.max_drawdown:.2f}%")

    print("\n")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Optimize VRP thresholds through grid search",
        formatter_class=argparse.RawDescriptionHelpFormatter,
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
        "--weights",
        type=str,
        default="balanced",
        choices=["balanced", "liquidity_first", "vrp_dominant"],
        help="Base weight configuration to use",
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
    logger.info("VRP Threshold Optimization - Grid Search")
    logger.info("=" * 80)
    logger.info(f"Period: {args.start_date} to {args.end_date}")
    logger.info(f"Database: {args.db_path}")
    logger.info(f"Base Weights: {args.weights}")
    logger.info("=" * 80)

    # Verify database exists
    db_path = Path(args.db_path)
    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        return 1

    # Parse dates
    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()

    # Define base weights
    weights_map = {
        "balanced": ScoringWeights(
            vrp_weight=0.40,
            consistency_weight=0.25,
            skew_weight=0.15,
            liquidity_weight=0.20
        ),
        "liquidity_first": ScoringWeights(
            vrp_weight=0.30,
            consistency_weight=0.20,
            skew_weight=0.15,
            liquidity_weight=0.35
        ),
        "vrp_dominant": ScoringWeights(
            vrp_weight=0.70,
            consistency_weight=0.20,
            skew_weight=0.05,
            liquidity_weight=0.05
        )
    }

    base_weights = weights_map[args.weights]

    # Create test configurations
    threshold_configs = create_test_configs()
    logger.info(f"\nTesting {len(threshold_configs)} threshold configurations...")

    # Initialize backtest engine
    engine = BacktestEngine(db_path)

    # Run backtests
    results: List[BacktestResult] = []

    for i, threshold_config in enumerate(threshold_configs, 1):
        logger.info(f"\n[{i}/{len(threshold_configs)}] Testing: {threshold_config.name}")
        logger.info(f"  VRP Marginal: {threshold_config.vrp_marginal:.2f}x")
        logger.info(f"  VRP Good: {threshold_config.vrp_good:.2f}x")
        logger.info(f"  VRP Excellent: {threshold_config.vrp_excellent:.2f}x")

        try:
            scoring_config = create_scoring_config(threshold_config, base_weights)
            result = engine.run_backtest(scoring_config, start_date, end_date)
            results.append(result)

            logger.info(f"  ✓ Sharpe: {result.sharpe_ratio:.2f}, Win Rate: {result.win_rate:.1f}%, Trades: {result.selected_trades}")

        except Exception as e:
            logger.error(f"  ✗ Error: {e}", exc_info=True)
            continue

    # Print results
    if results:
        print_results_table(results)
        print_analysis(results)
    else:
        logger.error("No successful backtest results")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
