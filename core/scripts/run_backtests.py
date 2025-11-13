#!/usr/bin/env python3
"""
Run A/B backtests on all scoring configurations.

Tests multiple weight configurations on historical data to identify
the optimal approach for ticker selection.

Usage:
    python scripts/run_backtests.py --start-date 2024-07-01 --end-date 2024-12-31
    python scripts/run_backtests.py --configs balanced liquidity_first conservative
"""

import sys
import argparse
import logging
from datetime import date, datetime
from pathlib import Path
from typing import List
import json

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.config.scoring_config import get_all_configs, get_config, list_configs
from src.application.services.backtest_engine import BacktestEngine, BacktestResult

logger = logging.getLogger(__name__)


def print_summary_table(results: List[BacktestResult]):
    """
    Print a formatted comparison table of backtest results.

    Args:
        results: List of BacktestResult objects
    """
    # Sort by Sharpe ratio (descending)
    sorted_results = sorted(results, key=lambda r: r.sharpe_ratio, reverse=True)

    print("\n" + "=" * 120)
    print("BACKTEST RESULTS COMPARISON")
    print("=" * 120)

    # Header
    header = (
        f"{'Config':<20} "
        f"{'Trades':<8} "
        f"{'Win%':<8} "
        f"{'Avg P&L':<10} "
        f"{'Total P&L':<10} "
        f"{'Sharpe':<8} "
        f"{'Max DD':<10} "
        f"{'Qual':<8}"
    )
    print(header)
    print("-" * 120)

    # Rows
    for result in sorted_results:
        row = (
            f"{result.config_name:<20} "
            f"{result.selected_trades:<8} "
            f"{result.win_rate:>6.1f}%  "
            f"{result.avg_pnl_per_trade:>8.2f}%  "
            f"{result.total_pnl:>8.2f}%  "
            f"{result.sharpe_ratio:>7.2f}  "
            f"{result.max_drawdown:>8.2f}%  "
            f"{result.qualified_opportunities:<8}"
        )
        print(row)

    print("=" * 120)

    # Best configs summary
    print("\n" + "=" * 80)
    print("TOP 3 CONFIGURATIONS")
    print("=" * 80)

    for i, result in enumerate(sorted_results[:3], 1):
        print(f"\n#{i} - {result.config_name}")
        print(f"    {result.config_description}")
        print(f"    • Sharpe Ratio: {result.sharpe_ratio:.2f}")
        print(f"    • Win Rate: {result.win_rate:.1f}%")
        print(f"    • Avg P&L per Trade: {result.avg_pnl_per_trade:.2f}%")
        print(f"    • Total P&L: {result.total_pnl:.2f}%")
        print(f"    • Max Drawdown: {result.max_drawdown:.2f}%")
        print(f"    • Trades Selected: {result.selected_trades}/{result.total_opportunities}")


def save_detailed_results(results: List[BacktestResult], output_path: Path):
    """
    Save detailed backtest results to JSON file.

    Args:
        results: List of BacktestResult objects
        output_path: Path to save JSON file
    """
    data = []

    for result in results:
        result_dict = {
            "run_id": result.run_id,
            "config_name": result.config_name,
            "config_description": result.config_description,
            "period": {
                "start": str(result.start_date),
                "end": str(result.end_date),
            },
            "metrics": {
                "total_opportunities": result.total_opportunities,
                "qualified_opportunities": result.qualified_opportunities,
                "selected_trades": result.selected_trades,
                "win_rate": round(result.win_rate, 2),
                "total_pnl": round(result.total_pnl, 2),
                "avg_pnl_per_trade": round(result.avg_pnl_per_trade, 2),
                "sharpe_ratio": round(result.sharpe_ratio, 2),
                "max_drawdown": round(result.max_drawdown, 2),
                "avg_score_winners": round(result.avg_score_winners, 2),
                "avg_score_losers": round(result.avg_score_losers, 2),
            },
            "trades": [
                {
                    "ticker": t.ticker,
                    "earnings_date": str(t.earnings_date),
                    "score": round(t.composite_score, 2),
                    "rank": t.rank,
                    "selected": t.selected,
                    "actual_move": round(t.actual_move, 2),
                    "avg_historical": round(t.avg_historical_move, 2),
                    "pnl": round(t.simulated_pnl, 2),
                }
                for t in result.trades if t.selected
            ],
        }

        data.append(result_dict)

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)

    logger.info(f"Saved detailed results to {output_path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run A/B backtests on scoring configurations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
    # Run all configurations on Q3-Q4 2024 data
    python scripts/run_backtests.py --start-date 2024-07-01 --end-date 2024-12-31

    # Run specific configurations
    python scripts/run_backtests.py --configs balanced liquidity_first --start-date 2024-07-01 --end-date 2024-12-31

    # Save detailed results
    python scripts/run_backtests.py --start-date 2024-07-01 --end-date 2024-12-31 --output results.json

Available configurations:
{chr(10).join(f'    • {name}' for name in list_configs())}
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
        "--configs",
        nargs="*",
        type=str,
        help="Specific configs to test (default: all)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="data/ivcrush.db",
        help="Path to database (default: data/ivcrush.db)",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Save detailed results to JSON file",
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

    # Parse dates
    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()

    # Get configurations to test
    if args.configs:
        configs_to_test = {name: get_config(name) for name in args.configs}
    else:
        configs_to_test = get_all_configs()

    logger.info("=" * 80)
    logger.info("IV Crush 2.0 - Backtest A/B Testing")
    logger.info("=" * 80)
    logger.info(f"Period: {start_date} to {end_date}")
    logger.info(f"Configurations: {len(configs_to_test)}")
    logger.info(f"Database: {args.db_path}")
    logger.info("=" * 80)

    # Verify database exists
    db_path = Path(args.db_path)
    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        logger.error("Run backfill first: python scripts/backfill_yfinance.py")
        return 1

    # Initialize backtest engine
    engine = BacktestEngine(db_path)

    # Run backtests
    results: List[BacktestResult] = []

    for i, (name, config) in enumerate(configs_to_test.items(), 1):
        logger.info(f"\n[{i}/{len(configs_to_test)}] Testing: {name}")

        try:
            result = engine.run_backtest(config, start_date, end_date)
            results.append(result)

        except Exception as e:
            logger.error(f"Error testing {name}: {e}", exc_info=True)
            continue

    # Print summary
    if results:
        print_summary_table(results)

        # Save detailed results if requested
        if args.output:
            output_path = Path(args.output)
            save_detailed_results(results, output_path)

        # Analysis insights
        print("\n" + "=" * 80)
        print("KEY INSIGHTS")
        print("=" * 80)

        best_sharpe = max(results, key=lambda r: r.sharpe_ratio)
        best_winrate = max(results, key=lambda r: r.win_rate)
        best_total_pnl = max(results, key=lambda r: r.total_pnl)

        print(f"\n✓ Best Sharpe Ratio: {best_sharpe.config_name} ({best_sharpe.sharpe_ratio:.2f})")
        print(f"✓ Best Win Rate: {best_winrate.config_name} ({best_winrate.win_rate:.1f}%)")
        print(f"✓ Best Total P&L: {best_total_pnl.config_name} ({best_total_pnl.total_pnl:.2f}%)")

        # Recommendation based on user profile (Balanced, Liquidity First)
        print("\n" + "=" * 80)
        print("RECOMMENDED CONFIGURATION")
        print("=" * 80)

        # Find balanced or liquidity_first in results
        recommended = None
        for result in results:
            if result.config_name.lower() in ["balanced", "liquidity-first", "liquidity_first"]:
                if recommended is None or result.sharpe_ratio > recommended.sharpe_ratio:
                    recommended = result

        if recommended:
            print(f"\nBased on your profile (Balanced risk, Liquidity First), we recommend:")
            print(f"\n  → {recommended.config_name}")
            print(f"    Sharpe Ratio: {recommended.sharpe_ratio:.2f}")
            print(f"    Win Rate: {recommended.win_rate:.1f}%")
            print(f"    Avg P&L per Trade: {recommended.avg_pnl_per_trade:.2f}%")
            print(f"    Expected Trades/Week: ~{recommended.selected_trades / 26 * 7:.0f}")

        print("\n")

    else:
        logger.error("No successful backtest results")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
