#!/usr/bin/env python3
"""
Test position sizing optimization on 2024 data.

Compares baseline (equal weight) vs optimized (Hybrid Kelly + VRP) position sizing.

Usage:
    python scripts/test_position_sizing.py --start-date 2024-04-01 --end-date 2024-12-31
"""

import sys
import argparse
import logging
from datetime import date
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.logging import setup_logging
from src.config.scoring_config import get_config
from src.application.services.backtest_engine import BacktestEngine

logger = logging.getLogger(__name__)


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description='Test position sizing optimization')
    parser.add_argument('--start-date', type=date.fromisoformat, default=date(2024, 4, 1),
                      help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end-date', type=date.fromisoformat, default=date(2024, 12, 31),
                      help='End date (YYYY-MM-DD)')
    parser.add_argument('--config', default='consistency_heavy',
                      help='Scoring config to test')
    parser.add_argument('--capital', type=float, default=40000.0,
                      help='Total capital for position sizing')
    parser.add_argument('--db', type=Path, default=Path('data/ivcrush.db'),
                      help='Path to database')

    args = parser.parse_args()

    # Setup logging
    setup_logging()

    print("\n" + "="*100)
    print("POSITION SIZING OPTIMIZATION TEST")
    print("="*100)
    print(f"\nPeriod: {args.start_date} to {args.end_date}")
    print(f"Config: {args.config}")
    print(f"Capital: ${args.capital:,.0f}")

    # Get config
    config = get_config(args.config)

    # Initialize backtest engine
    engine = BacktestEngine(args.db)

    # Run baseline (no position sizing)
    print("\n" + "-"*100)
    print("BASELINE: Equal Weight (No Position Sizing)")
    print("-"*100)

    baseline = engine.run_backtest(
        config=config,
        start_date=args.start_date,
        end_date=args.end_date,
        position_sizing=False,
    )

    print(f"\nSelected Trades: {baseline.selected_trades}")
    print(f"Win Rate: {baseline.win_rate:.1f}%")
    print(f"Total P&L: {baseline.total_pnl:.2f}%")
    print(f"Avg P&L/Trade: {baseline.avg_pnl_per_trade:.2f}%")
    print(f"Sharpe Ratio: {baseline.sharpe_ratio:.2f}")
    print(f"Max Drawdown: {baseline.max_drawdown:.2f}%")

    # Run optimized (with position sizing)
    print("\n" + "-"*100)
    print("OPTIMIZED: Hybrid Position Sizing (Kelly + VRP)")
    print("-"*100)

    optimized = engine.run_backtest(
        config=config,
        start_date=args.start_date,
        end_date=args.end_date,
        position_sizing=True,
        total_capital=args.capital,
    )

    print(f"\nKelly Fraction: {optimized.kelly_fraction:.2%}")
    print(f"Total Capital: ${optimized.total_capital:,.0f}")
    print(f"Selected Trades: {optimized.selected_trades}")
    print(f"Win Rate: {optimized.win_rate:.1f}%")
    print(f"Total P&L: ${optimized.total_pnl:,.2f}")
    print(f"Avg P&L/Trade: ${optimized.avg_pnl_per_trade:,.2f}")
    print(f"Sharpe Ratio: {optimized.sharpe_ratio:.2f}")
    print(f"Max Drawdown: ${optimized.max_drawdown:,.2f}")

    # Comparison
    print("\n" + "="*100)
    print("COMPARISON")
    print("="*100)

    # Convert baseline to dollars for comparison
    baseline_capital = 20000.0  # Equal weight baseline
    baseline_position_size = baseline_capital / baseline.selected_trades if baseline.selected_trades > 0 else 0
    baseline_total_dollars = baseline.total_pnl / 100.0 * baseline_capital
    baseline_avg_dollars = baseline_total_dollars / baseline.selected_trades if baseline.selected_trades > 0 else 0
    baseline_dd_dollars = baseline.max_drawdown / 100.0 * baseline_capital

    print(f"\n{'Metric':<25} {'Baseline':<20} {'Optimized':<20} {'Improvement'}")
    print("-"*100)
    print(f"{'Capital Deployed':<25} ${baseline_capital:>18,.0f} ${optimized.total_capital:>18,.0f} {(optimized.total_capital/baseline_capital-1)*100:>+8.1f}%")
    print(f"{'Total P&L':<25} ${baseline_total_dollars:>18,.2f} ${optimized.total_pnl:>18,.2f} {(optimized.total_pnl/baseline_total_dollars-1)*100 if baseline_total_dollars != 0 else 0:>+8.1f}%")
    print(f"{'Avg P&L/Trade':<25} ${baseline_avg_dollars:>18,.2f} ${optimized.avg_pnl_per_trade:>18,.2f} {(optimized.avg_pnl_per_trade/baseline_avg_dollars-1)*100 if baseline_avg_dollars != 0 else 0:>+8.1f}%")
    print(f"{'Sharpe Ratio':<25} {baseline.sharpe_ratio:>20.2f} {optimized.sharpe_ratio:>20.2f} {(optimized.sharpe_ratio/baseline.sharpe_ratio-1)*100 if baseline.sharpe_ratio != 0 else 0:>+8.1f}%")
    print(f"{'Max Drawdown':<25} ${baseline_dd_dollars:>18,.2f} ${optimized.max_drawdown:>18,.2f} {(optimized.max_drawdown/baseline_dd_dollars-1)*100 if baseline_dd_dollars != 0 else 0:>+8.1f}%")
    print(f"{'Win Rate':<25} {baseline.win_rate:>19.1f}% {optimized.win_rate:>19.1f}% {optimized.win_rate-baseline.win_rate:>+8.1f}%")

    print("\n" + "="*100)
    print("TRADE-BY-TRADE BREAKDOWN (Optimized)")
    print("="*100)

    print(f"\n{'Ticker':<8} {'Date':<12} {'Score':<8} {'Actual':<10} {'P&L %':<10} {'Position':<12} {'P&L $'}")
    print("-"*100)

    selected = [t for t in optimized.trades if t.selected]
    for trade in selected:
        pnl_pct = (trade.simulated_pnl / (args.capital * optimized.kelly_fraction * (trade.composite_score / statistics.mean(t.composite_score for t in selected)))) * 100
        position_size = args.capital * optimized.kelly_fraction * (trade.composite_score / statistics.mean(t.composite_score for t in selected))

        print(f"{trade.ticker:<8} {str(trade.earnings_date):<12} {trade.composite_score:>6.1f}  {trade.actual_move:>8.2f}%  {pnl_pct:>+8.2f}%  ${position_size:>10,.0f}  ${trade.simulated_pnl:>+10,.2f}")

    print("\n✓ Position sizing test complete!")

    return 0


if __name__ == '__main__':
    import statistics
    sys.exit(main())
