#!/usr/bin/env python3
"""
Run backtests for scoring weight optimization.

Tests multiple scoring configurations on historical data to identify
optimal weight combinations for ticker selection.

Usage:
    # Run all configs on Q2-Q4 2024
    python run_backtests.py --start-date 2024-04-01 --end-date 2024-12-31

    # Run specific config
    python run_backtests.py --config balanced --start-date 2024-04-01 --end-date 2024-12-31

    # With position sizing
    python run_backtests.py --start-date 2024-04-01 --end-date 2024-12-31 --position-sizing

    # Walk-forward validation
    python run_backtests.py --walk-forward --start-date 2024-01-01 --end-date 2024-12-31
"""

import argparse
import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path
from typing import List, Dict, Optional

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.scoring_config import get_all_configs, get_config, list_configs
from src.application.services.backtest_engine import BacktestEngine, BacktestResult

logger = logging.getLogger(__name__)


def format_result_summary(result: BacktestResult) -> str:
    """Format backtest result as summary string."""
    lines = [
        f"\n{'='*80}",
        f"Config: {result.config_name}",
        f"{'='*80}",
        f"Period: {result.start_date} to {result.end_date}",
        f"",
        f"Opportunities:",
        f"  Total earnings events: {result.total_opportunities}",
        f"  Qualified (>min_score): {result.qualified_opportunities}",
        f"  Selected for trading: {result.selected_trades}",
        f"",
        f"Performance:",
        f"  Win Rate: {result.win_rate:.1f}%",
    ]

    # Format P&L and drawdown based on position sizing mode
    if result.position_sizing_enabled:
        # Kelly sizing: P&L in dollars, drawdown in %
        lines.extend([
            f"  Total P&L: ${result.total_pnl:,.2f}",
            f"  Avg P&L/Trade: ${result.avg_pnl_per_trade:,.2f}",
            f"  Sharpe Ratio: {result.sharpe_ratio:.2f}",
            f"  Max Drawdown: {result.max_drawdown:.2f}%",
        ])
    else:
        # No Kelly: P&L and drawdown both in percentages
        lines.extend([
            f"  Total P&L: {result.total_pnl:.2f}%",
            f"  Avg P&L/Trade: {result.avg_pnl_per_trade:.2f}%",
            f"  Sharpe Ratio: {result.sharpe_ratio:.2f}",
            f"  Max Drawdown: {result.max_drawdown:.2f}%",
        ])

    lines.extend([
        f"",
        f"Trade Quality:",
        f"  Avg Score (Winners): {result.avg_score_winners:.1f}",
        f"  Avg Score (Losers): {result.avg_score_losers:.1f}",
    ])

    if result.position_sizing_enabled:
        lines.extend([
            f"",
            f"Position Sizing:",
            f"  Kelly Fraction: {result.kelly_fraction:.2%}",
            f"  Total Capital: ${result.total_capital:,.2f}",
        ])

    lines.append("="*80)
    return "\n".join(lines)


def run_single_config(
    config_name: str,
    start_date: date,
    end_date: date,
    db_path: Path,
    position_sizing: bool = False,
    total_capital: float = 40000.0,
) -> BacktestResult:
    """Run backtest for a single configuration."""
    logger.info(f"Running backtest: {config_name}")

    config = get_config(config_name)
    engine = BacktestEngine(db_path)

    result = engine.run_backtest(
        config=config,
        start_date=start_date,
        end_date=end_date,
        position_sizing=position_sizing,
        total_capital=total_capital,
    )

    return result


def run_all_configs(
    start_date: date,
    end_date: date,
    db_path: Path,
    position_sizing: bool = False,
    total_capital: float = 40000.0,
) -> List[BacktestResult]:
    """Run backtests for all predefined configurations."""
    logger.info("Running backtests for all configurations")

    configs = get_all_configs()
    engine = BacktestEngine(db_path)

    results = []

    for config_name, config in configs.items():
        logger.info(f"\n{'='*80}")
        logger.info(f"Testing: {config_name}")
        logger.info(f"{'='*80}")

        result = engine.run_backtest(
            config=config,
            start_date=start_date,
            end_date=end_date,
            position_sizing=position_sizing,
            total_capital=total_capital,
        )

        results.append(result)

        # Print summary
        print(format_result_summary(result))

    return results


def run_walk_forward(
    start_date: date,
    end_date: date,
    db_path: Path,
    train_days: int = 180,
    test_days: int = 90,
    step_days: int = 90,
) -> Dict:
    """Run walk-forward validation."""
    logger.info("Running walk-forward validation")

    configs = list(get_all_configs().values())
    engine = BacktestEngine(db_path)

    results = engine.run_walk_forward_backtest(
        configs=configs,
        start_date=start_date,
        end_date=end_date,
        train_window_days=train_days,
        test_window_days=test_days,
        step_days=step_days,
    )

    return results


def save_results(
    results: List[BacktestResult],
    output_path: Path,
):
    """Save results to JSON file."""
    output_data = {
        'timestamp': datetime.now().isoformat(),
        'results': [
            {
                'config_name': r.config_name,
                'config_description': r.config_description,
                'start_date': str(r.start_date),
                'end_date': str(r.end_date),
                'total_opportunities': r.total_opportunities,
                'qualified_opportunities': r.qualified_opportunities,
                'selected_trades': r.selected_trades,
                'win_rate': r.win_rate,
                'total_pnl': r.total_pnl,
                'avg_pnl_per_trade': r.avg_pnl_per_trade,
                'sharpe_ratio': r.sharpe_ratio,
                'max_drawdown': r.max_drawdown,
                'avg_score_winners': r.avg_score_winners,
                'avg_score_losers': r.avg_score_losers,
                'position_sizing_enabled': r.position_sizing_enabled,
                'total_capital': r.total_capital,
                'kelly_fraction': r.kelly_fraction,
                'trades': [
                    {
                        'ticker': t.ticker,
                        'earnings_date': str(t.earnings_date),
                        'composite_score': t.composite_score,
                        'rank': t.rank,
                        'selected': t.selected,
                        'actual_move': t.actual_move,
                        'simulated_pnl': t.simulated_pnl,
                    }
                    for t in r.trades
                ]
            }
            for r in results
        ]
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    logger.info(f"Results saved to: {output_path}")


def print_comparison_table(results: List[BacktestResult]):
    """Print comparison table of all configs."""
    print("\n" + "="*100)
    print("BACKTEST COMPARISON")
    print("="*100)

    # Sort by Sharpe ratio (descending)
    sorted_results = sorted(results, key=lambda r: r.sharpe_ratio, reverse=True)

    # Header
    print(f"{'Rank':<6} {'Config':<20} {'Sharpe':<8} {'Win%':<8} {'Avg P&L':<10} {'Total P&L':<12} {'Trades':<8} {'Max DD':<10}")
    print("-"*100)

    # Rows
    for rank, result in enumerate(sorted_results, 1):
        print(f"{rank:<6} "
              f"{result.config_name:<20} "
              f"{result.sharpe_ratio:<8.2f} "
              f"{result.win_rate:<8.1f} "
              f"{result.avg_pnl_per_trade:<10.2f} "
              f"{result.total_pnl:<12.2f} "
              f"{result.selected_trades:<8} "
              f"{result.max_drawdown:<10.2f}")

    print("="*100)

    # Highlight top 3
    print("\n🏆 TOP 3 CONFIGURATIONS:")
    for i, result in enumerate(sorted_results[:3], 1):
        print(f"\n{i}. {result.config_name}")
        print(f"   Sharpe: {result.sharpe_ratio:.2f}, Win Rate: {result.win_rate:.1f}%, P&L: {result.total_pnl:.2f}%")
        print(f"   Description: {result.config_description}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Run backtests for scoring weight optimization"
    )

    parser.add_argument(
        "--config",
        type=str,
        help="Specific configuration to test (default: all)"
    )

    parser.add_argument(
        "--start-date",
        type=str,
        required=True,
        help="Start date (YYYY-MM-DD)"
    )

    parser.add_argument(
        "--end-date",
        type=str,
        required=True,
        help="End date (YYYY-MM-DD)"
    )

    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("2.0/data/ivcrush.db"),
        help="Database path (default: 2.0/data/ivcrush.db)"
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("2.0/results/backtest_results.json"),
        help="Output file path"
    )

    parser.add_argument(
        "--position-sizing",
        action="store_true",
        help="Enable position sizing (Kelly criterion)"
    )

    parser.add_argument(
        "--total-capital",
        type=float,
        default=40000.0,
        help="Total capital for position sizing (default: 40000)"
    )

    parser.add_argument(
        "--walk-forward",
        action="store_true",
        help="Run walk-forward validation"
    )

    parser.add_argument(
        "--train-days",
        type=int,
        default=180,
        help="Training window days for walk-forward (default: 180)"
    )

    parser.add_argument(
        "--test-days",
        type=int,
        default=90,
        help="Test window days for walk-forward (default: 90)"
    )

    parser.add_argument(
        "--step-days",
        type=int,
        default=90,
        help="Step size for walk-forward (default: 90)"
    )

    parser.add_argument(
        "--list-configs",
        action="store_true",
        help="List available configurations"
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # List configs
    if args.list_configs:
        configs = list_configs()
        print("\nAvailable scoring configurations:")
        for config in configs:
            print(f"  - {config}")
        return

    # Parse dates
    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)

    # Walk-forward mode
    if args.walk_forward:
        results_dict = run_walk_forward(
            start_date=start_date,
            end_date=end_date,
            db_path=args.db_path,
            train_days=args.train_days,
            test_days=args.test_days,
            step_days=args.step_days,
        )

        # Save walk-forward results
        output_path = args.output.parent / "walk_forward_results.json"
        with open(output_path, 'w') as f:
            # Convert test_results to serializable format
            serializable = {
                'summary': results_dict['summary'],
                'best_configs': results_dict['best_configs'],
                'test_results': [
                    {
                        'config_name': r.config_name,
                        'sharpe_ratio': r.sharpe_ratio,
                        'win_rate': r.win_rate,
                        'total_pnl': r.total_pnl,
                        'selected_trades': r.selected_trades,
                    }
                    for r in results_dict['test_results']
                ]
            }
            json.dump(serializable, f, indent=2)

        logger.info(f"Walk-forward results saved to: {output_path}")
        return

    # Single config mode
    if args.config:
        result = run_single_config(
            config_name=args.config,
            start_date=start_date,
            end_date=end_date,
            db_path=args.db_path,
            position_sizing=args.position_sizing,
            total_capital=args.total_capital,
        )

        print(format_result_summary(result))
        save_results([result], args.output)
        return

    # All configs mode
    results = run_all_configs(
        start_date=start_date,
        end_date=end_date,
        db_path=args.db_path,
        position_sizing=args.position_sizing,
        total_capital=args.total_capital,
    )

    # Print comparison
    print_comparison_table(results)

    # Save results
    save_results(results, args.output)

    print(f"\n✅ Complete! Results saved to: {args.output}")


if __name__ == "__main__":
    main()
