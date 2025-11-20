#!/usr/bin/env python3
"""
Analyze backtest results and generate insights.

Provides deep analysis of backtest results including:
- Performance comparison across configs
- Trade quality analysis
- Best/worst performers by ticker
- Risk-adjusted metrics
- Recommendations

Usage:
    python analyze_backtest_results.py
    python analyze_backtest_results.py --input results/backtest_results.json
    python analyze_backtest_results.py --export-csv
"""

import argparse
import json
import logging
import sys
from collections import defaultdict
from pathlib import Path
from typing import List, Dict
import statistics

logger = logging.getLogger(__name__)


def load_results(input_path: Path) -> Dict:
    """Load backtest results from JSON."""
    with open(input_path, 'r') as f:
        data = json.load(f)
    return data


def analyze_config_performance(results: List[Dict]) -> None:
    """Analyze and compare configuration performance."""
    print("\n" + "="*100)
    print("CONFIGURATION PERFORMANCE ANALYSIS")
    print("="*100)

    # Sort by Sharpe ratio
    sorted_results = sorted(results, key=lambda r: r['sharpe_ratio'], reverse=True)

    print(f"\n{'Rank':<6} {'Configuration':<22} {'Sharpe':<9} {'Win%':<9} {'Avg P&L':<11} {'Total P&L':<11} {'Trades':<9} {'Max DD':<10}")
    print("-"*100)

    for rank, result in enumerate(sorted_results, 1):
        medal = "🥇" if rank == 1 else "🥈" if rank == 2 else "🥉" if rank == 3 else "  "
        print(f"{medal} {rank:<3} "
              f"{result['config_name']:<22} "
              f"{result['sharpe_ratio']:<9.2f} "
              f"{result['win_rate']:<9.1f} "
              f"{result['avg_pnl_per_trade']:<11.2f} "
              f"{result['total_pnl']:<11.2f} "
              f"{result['selected_trades']:<9} "
              f"{result['max_drawdown']:<10.2f}")

    # Performance categories
    print("\n" + "="*100)
    print("PERFORMANCE RATINGS")
    print("="*100)

    for result in sorted_results[:5]:  # Top 5
        sharpe = result['sharpe_ratio']
        win_rate = result['win_rate']

        sharpe_rating = (
            "⭐⭐⭐ EXCELLENT" if sharpe >= 0.5
            else "⭐⭐ GOOD" if sharpe >= 0.3
            else "⭐ ACCEPTABLE" if sharpe >= 0.1
            else "❌ POOR"
        )

        win_rate_rating = (
            "⭐⭐⭐ EXCELLENT" if win_rate >= 65
            else "⭐⭐ GOOD" if win_rate >= 55
            else "⭐ ACCEPTABLE" if win_rate >= 45
            else "❌ CONCERNING"
        )

        print(f"\n{result['config_name']}:")
        print(f"  Sharpe Ratio: {sharpe:.2f} - {sharpe_rating}")
        print(f"  Win Rate: {win_rate:.1f}% - {win_rate_rating}")
        print(f"  Description: {result['config_description']}")


def analyze_trade_quality(results: List[Dict]) -> None:
    """Analyze trade quality metrics."""
    print("\n" + "="*100)
    print("TRADE QUALITY ANALYSIS")
    print("="*100)

    print(f"\n{'Configuration':<22} {'Avg Score':<12} {'Winner Score':<15} {'Loser Score':<15} {'Score Delta':<12}")
    print("-"*100)

    for result in results:
        avg_score = (result['avg_score_winners'] + result['avg_score_losers']) / 2
        score_delta = result['avg_score_winners'] - result['avg_score_losers']

        delta_indicator = "✅" if score_delta > 5 else "⚠️ " if score_delta > 0 else "❌"

        print(f"{result['config_name']:<22} "
              f"{avg_score:<12.1f} "
              f"{result['avg_score_winners']:<15.1f} "
              f"{result['avg_score_losers']:<15.1f} "
              f"{delta_indicator} {score_delta:<9.1f}")

    print("\n💡 Score Delta Interpretation:")
    print("  ✅ >5 points: Winners clearly differentiated from losers")
    print("  ⚠️  0-5 points: Weak differentiation")
    print("  ❌ <0 points: Scoring not predictive (losers scored higher!)")


def analyze_ticker_performance(results: List[Dict]) -> None:
    """Analyze performance by ticker."""
    print("\n" + "="*100)
    print("TICKER PERFORMANCE ANALYSIS")
    print("="*100)

    # Aggregate trades across all configs
    ticker_stats = defaultdict(lambda: {'trades': [], 'pnls': []})

    for result in results:
        for trade in result['trades']:
            if trade['selected']:
                ticker = trade['ticker']
                ticker_stats[ticker]['trades'].append(trade)
                ticker_stats[ticker]['pnls'].append(trade['simulated_pnl'])

    # Calculate win rates
    ticker_performance = []
    for ticker, stats in ticker_stats.items():
        wins = sum(1 for pnl in stats['pnls'] if pnl > 0)
        total = len(stats['pnls'])
        win_rate = (wins / total * 100) if total > 0 else 0
        avg_pnl = statistics.mean(stats['pnls']) if stats['pnls'] else 0

        ticker_performance.append({
            'ticker': ticker,
            'trades': total,
            'win_rate': win_rate,
            'avg_pnl': avg_pnl,
            'total_pnl': sum(stats['pnls']),
        })

    # Sort by win rate
    ticker_performance.sort(key=lambda x: x['win_rate'], reverse=True)

    # Best performers
    print("\n🏆 TOP 10 BEST PERFORMERS (by Win Rate):")
    print(f"{'Ticker':<10} {'Trades':<10} {'Win Rate':<12} {'Avg P&L':<12} {'Total P&L':<12}")
    print("-"*60)

    for ticker_data in ticker_performance[:10]:
        print(f"{ticker_data['ticker']:<10} "
              f"{ticker_data['trades']:<10} "
              f"{ticker_data['win_rate']:<12.1f} "
              f"{ticker_data['avg_pnl']:<12.2f} "
              f"{ticker_data['total_pnl']:<12.2f}")

    # Worst performers
    print("\n⚠️  TOP 10 WORST PERFORMERS (by Win Rate):")
    print(f"{'Ticker':<10} {'Trades':<10} {'Win Rate':<12} {'Avg P&L':<12} {'Total P&L':<12}")
    print("-"*60)

    for ticker_data in ticker_performance[-10:]:
        print(f"{ticker_data['ticker']:<10} "
              f"{ticker_data['trades']:<10} "
              f"{ticker_data['win_rate']:<12.1f} "
              f"{ticker_data['avg_pnl']:<12.2f} "
              f"{ticker_data['total_pnl']:<12.2f}")


def analyze_risk_metrics(results: List[Dict]) -> None:
    """Analyze risk-adjusted metrics."""
    print("\n" + "="*100)
    print("RISK-ADJUSTED METRICS")
    print("="*100)

    print(f"\n{'Configuration':<22} {'Sharpe':<10} {'Sortino':<10} {'Max DD':<12} {'Recovery':<12}")
    print("-"*100)

    for result in results:
        # Calculate Sortino ratio (simplified)
        trades = result['trades']
        selected_trades = [t for t in trades if t['selected']]

        if selected_trades:
            pnls = [t['simulated_pnl'] for t in selected_trades]
            downside_pnls = [p for p in pnls if p < 0]

            avg_pnl = statistics.mean(pnls)
            downside_std = statistics.stdev(downside_pnls) if len(downside_pnls) > 1 else 0

            sortino = (avg_pnl / downside_std) if downside_std > 0 else 0

            # Recovery factor = Total Return / Max Drawdown
            recovery = abs(result['total_pnl'] / result['max_drawdown']) if result['max_drawdown'] > 0 else 0

            sortino_rating = "⭐⭐⭐" if sortino >= 0.7 else "⭐⭐" if sortino >= 0.4 else "⭐"
            recovery_rating = "⭐⭐⭐" if recovery >= 2.0 else "⭐⭐" if recovery >= 1.0 else "⭐"

            print(f"{result['config_name']:<22} "
                  f"{result['sharpe_ratio']:<10.2f} "
                  f"{sortino:<10.2f} {sortino_rating} "
                  f"{result['max_drawdown']:<12.2f} "
                  f"{recovery:<12.2f} {recovery_rating}")

    print("\n💡 Risk Metrics Interpretation:")
    print("  Sortino Ratio: Like Sharpe but only penalizes downside (>0.7 = excellent)")
    print("  Recovery Factor: Return / Max Drawdown (>2.0 = excellent)")


def generate_recommendations(results: List[Dict]) -> None:
    """Generate trading recommendations based on analysis."""
    print("\n" + "="*100)
    print("RECOMMENDATIONS")
    print("="*100)

    # Find best configs
    best_sharpe = max(results, key=lambda r: r['sharpe_ratio'])
    best_win_rate = max(results, key=lambda r: r['win_rate'])
    best_pnl = max(results, key=lambda r: r['total_pnl'])

    print("\n🎯 RECOMMENDED CONFIGURATIONS:")

    print(f"\n1. Best Risk-Adjusted Return: {best_sharpe['config_name']}")
    print(f"   Sharpe: {best_sharpe['sharpe_ratio']:.2f}, Win Rate: {best_sharpe['win_rate']:.1f}%")
    print(f"   ✅ Use for: Consistent risk-adjusted returns")

    print(f"\n2. Highest Win Rate: {best_win_rate['config_name']}")
    print(f"   Win Rate: {best_win_rate['win_rate']:.1f}%, Sharpe: {best_win_rate['sharpe_ratio']:.2f}")
    print(f"   ✅ Use for: High probability setups")

    print(f"\n3. Maximum Returns: {best_pnl['config_name']}")
    print(f"   Total P&L: {best_pnl['total_pnl']:.2f}%, Sharpe: {best_pnl['sharpe_ratio']:.2f}")
    print(f"   ✅ Use for: Aggressive growth (higher variance)")

    print("\n📋 NEXT STEPS:")
    print("\n1. Paper Trading Validation (4-8 weeks):")
    print(f"   python scripts/paper_trading_backtest.py --config {best_sharpe['config_name']} --weeks 4")

    print("\n2. Compare top 3 configs in paper trading:")
    configs_to_test = [best_sharpe['config_name'], best_win_rate['config_name'], best_pnl['config_name']]
    unique_configs = list(dict.fromkeys(configs_to_test))  # Remove duplicates
    print(f"   python scripts/paper_trading_backtest.py --configs {','.join(unique_configs[:3])} --weeks 4")

    print("\n3. Deploy to live trading:")
    print("   - Start with 1-2 positions")
    print("   - Use conservative position sizing (Half-Kelly)")
    print("   - Track performance vs backtested expectations")


def export_to_csv(results: List[Dict], output_path: Path) -> None:
    """Export results to CSV for external analysis."""
    import csv

    csv_path = output_path.parent / "backtest_summary.csv"

    with open(csv_path, 'w', newline='') as f:
        writer = csv.writer(f)

        # Header
        writer.writerow([
            'Config', 'Sharpe', 'Win Rate', 'Avg P&L', 'Total P&L',
            'Trades', 'Max Drawdown', 'Avg Score Winners', 'Avg Score Losers'
        ])

        # Rows
        for result in sorted(results, key=lambda r: r['sharpe_ratio'], reverse=True):
            writer.writerow([
                result['config_name'],
                f"{result['sharpe_ratio']:.2f}",
                f"{result['win_rate']:.1f}",
                f"{result['avg_pnl_per_trade']:.2f}",
                f"{result['total_pnl']:.2f}",
                result['selected_trades'],
                f"{result['max_drawdown']:.2f}",
                f"{result['avg_score_winners']:.1f}",
                f"{result['avg_score_losers']:.1f}",
            ])

    logger.info(f"CSV exported to: {csv_path}")
    print(f"\n📊 CSV exported to: {csv_path}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Analyze backtest results"
    )

    parser.add_argument(
        "--input",
        type=Path,
        default=Path("2.0/results/backtest_results.json"),
        help="Input results file (default: 2.0/results/backtest_results.json)"
    )

    parser.add_argument(
        "--export-csv",
        action="store_true",
        help="Export summary to CSV"
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Load results
    if not args.input.exists():
        print(f"❌ Results file not found: {args.input}")
        print("\nRun backtests first:")
        print("  python scripts/run_backtests.py --start-date 2024-04-01 --end-date 2024-12-31")
        return

    logger.info(f"Loading results from: {args.input}")
    data = load_results(args.input)

    results = data['results']

    if not results:
        print("❌ No results found in file")
        return

    print(f"\n✅ Loaded {len(results)} backtest results")
    print(f"   Period: {results[0]['start_date']} to {results[0]['end_date']}")

    # Run analyses
    analyze_config_performance(results)
    analyze_trade_quality(results)
    analyze_ticker_performance(results)
    analyze_risk_metrics(results)
    generate_recommendations(results)

    # Export CSV
    if args.export_csv:
        export_to_csv(results, args.input)

    print("\n" + "="*100)
    print("✅ ANALYSIS COMPLETE")
    print("="*100)


if __name__ == "__main__":
    main()
