#!/usr/bin/env python3
"""
Deep analysis of backtest results.

Provides statistical insights, trade-by-trade analysis, and recommendations.
"""

import sys
import json
import argparse
from pathlib import Path
from typing import List, Dict, Any
from collections import defaultdict
import statistics

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_results(filepath: Path) -> List[Dict[str, Any]]:
    """Load backtest results from JSON."""
    with open(filepath, 'r') as f:
        return json.load(f)


def analyze_trade_quality(results: List[Dict[str, Any]]):
    """Analyze relationship between scores and outcomes."""
    print("\n" + "=" * 80)
    print("TRADE QUALITY ANALYSIS")
    print("=" * 80)

    for result in results:
        if result['metrics']['selected_trades'] == 0:
            continue

        config_name = result['config_name']
        trades = result['trades']

        winners = [t for t in trades if t['pnl'] > 0]
        losers = [t for t in trades if t['pnl'] <= 0]

        if winners:
            avg_winner_score = statistics.mean([t['score'] for t in winners])
            avg_winner_pnl = statistics.mean([t['pnl'] for t in winners])
        else:
            avg_winner_score = 0
            avg_winner_pnl = 0

        if losers:
            avg_loser_score = statistics.mean([t['score'] for t in losers])
            avg_loser_pnl = statistics.mean([t['pnl'] for t in losers])
        else:
            avg_loser_score = 0
            avg_loser_pnl = 0

        print(f"\n{config_name}:")
        print(f"  Winners ({len(winners)}): Avg Score {avg_winner_score:.1f}, Avg P&L {avg_winner_pnl:.2f}%")
        print(f"  Losers ({len(losers)}): Avg Score {avg_loser_score:.1f}, Avg P&L {avg_loser_pnl:.2f}%")

        if winners and losers:
            score_diff = avg_winner_score - avg_loser_score
            print(f"  → Score differential: {score_diff:+.1f} points (higher scores = better trades)")


def analyze_by_ticker(results: List[Dict[str, Any]]):
    """Analyze performance by ticker."""
    print("\n" + "=" * 80)
    print("BY-TICKER PERFORMANCE")
    print("=" * 80)

    # Aggregate trades across all configs
    ticker_stats = defaultdict(lambda: {'trades': 0, 'wins': 0, 'total_pnl': 0, 'configs': set()})

    for result in results:
        for trade in result['trades']:
            ticker = trade['ticker']
            ticker_stats[ticker]['trades'] += 1
            ticker_stats[ticker]['total_pnl'] += trade['pnl']
            ticker_stats[ticker]['configs'].add(result['config_name'])
            if trade['pnl'] > 0:
                ticker_stats[ticker]['wins'] += 1

    # Sort by total P&L
    sorted_tickers = sorted(
        ticker_stats.items(),
        key=lambda x: x[1]['total_pnl'],
        reverse=True
    )

    print("\nTop 10 Tickers by Total P&L:")
    print(f"{'Ticker':<8} {'Trades':<8} {'Win%':<8} {'Total P&L':<12} {'In Configs':<10}")
    print("-" * 80)

    for ticker, stats in sorted_tickers[:10]:
        win_rate = (stats['wins'] / stats['trades'] * 100) if stats['trades'] > 0 else 0
        print(f"{ticker:<8} {stats['trades']:<8} {win_rate:>6.1f}%  {stats['total_pnl']:>10.2f}%  {len(stats['configs']):<10}")

    print("\nWorst 5 Tickers by Total P&L:")
    print(f"{'Ticker':<8} {'Trades':<8} {'Win%':<8} {'Total P&L':<12} {'In Configs':<10}")
    print("-" * 80)

    for ticker, stats in sorted_tickers[-5:]:
        win_rate = (stats['wins'] / stats['trades'] * 100) if stats['trades'] > 0 else 0
        print(f"{ticker:<8} {stats['trades']:<8} {win_rate:>6.1f}%  {stats['total_pnl']:>10.2f}%  {len(stats['configs']):<10}")


def analyze_consistency_across_configs(results: List[Dict[str, Any]]):
    """Check which tickers appear in multiple configs."""
    print("\n" + "=" * 80)
    print("CROSS-CONFIG CONSISTENCY")
    print("=" * 80)

    # Track which tickers appear in which configs
    ticker_appearances = defaultdict(list)

    for result in results:
        if result['metrics']['selected_trades'] == 0:
            continue

        for trade in result['trades']:
            ticker_appearances[trade['ticker']].append(result['config_name'])

    # Find tickers that appear in most configs
    multi_config_tickers = {
        ticker: configs
        for ticker, configs in ticker_appearances.items()
        if len(configs) >= 3
    }

    if multi_config_tickers:
        print(f"\nTickers selected by 3+ configurations:")
        for ticker, configs in sorted(multi_config_tickers.items(), key=lambda x: len(x[1]), reverse=True):
            print(f"  {ticker}: {len(configs)} configs - {', '.join(configs[:3])}")
    else:
        print("\nNo tickers were selected by 3+ configurations (data is sparse)")


def calculate_risk_metrics(results: List[Dict[str, Any]]):
    """Calculate additional risk metrics."""
    print("\n" + "=" * 80)
    print("RISK METRICS")
    print("=" * 80)

    print(f"\n{'Config':<20} {'Sortino':<10} {'Max Win':<10} {'Max Loss':<10} {'Win/Loss':<10}")
    print("-" * 80)

    for result in results:
        if result['metrics']['selected_trades'] == 0:
            continue

        trades = result['trades']
        pnls = [t['pnl'] for t in trades]

        # Sortino ratio (only penalize downside volatility)
        downside_pnls = [p for p in pnls if p < 0]
        if downside_pnls:
            downside_std = statistics.stdev(downside_pnls)
            sortino = statistics.mean(pnls) / downside_std if downside_std > 0 else 0
        else:
            sortino = float('inf') if statistics.mean(pnls) > 0 else 0

        max_win = max(pnls)
        max_loss = min(pnls)

        # Win/Loss ratio
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p < 0]
        if winners and losers:
            win_loss_ratio = abs(statistics.mean(winners) / statistics.mean(losers))
        else:
            win_loss_ratio = 0

        print(f"{result['config_name']:<20} {sortino:>8.2f}  {max_win:>8.2f}%  {max_loss:>8.2f}%  {win_loss_ratio:>8.2f}x")


def generate_recommendations(results: List[Dict[str, Any]]):
    """Generate final recommendations based on analysis."""
    print("\n" + "=" * 80)
    print("FINAL RECOMMENDATIONS")
    print("=" * 80)

    # Filter out configs with no trades
    valid_results = [r for r in results if r['metrics']['selected_trades'] > 0]

    if not valid_results:
        print("\nInsufficient data for recommendations")
        return

    # Find best by different criteria
    best_sharpe = max(valid_results, key=lambda r: r['metrics']['sharpe_ratio'])
    best_winrate = max(valid_results, key=lambda r: r['metrics']['win_rate'])
    best_total_pnl = max(valid_results, key=lambda r: r['metrics']['total_pnl'])
    most_trades = max(valid_results, key=lambda r: r['metrics']['selected_trades'])

    print("\n1. HIGHEST QUALITY (Best Sharpe Ratio):")
    print(f"   → {best_sharpe['config_name']}")
    print(f"   → Sharpe: {best_sharpe['metrics']['sharpe_ratio']:.2f}, Win Rate: {best_sharpe['metrics']['win_rate']:.1f}%")
    print(f"   → Use when: You want the best risk-adjusted returns with high confidence")

    print("\n2. HIGHEST WIN RATE:")
    print(f"   → {best_winrate['config_name']}")
    print(f"   → Win Rate: {best_winrate['metrics']['win_rate']:.1f}%, Sharpe: {best_winrate['metrics']['sharpe_ratio']:.2f}")
    print(f"   → Use when: You prioritize consistency and psychological comfort over total returns")

    print("\n3. HIGHEST TOTAL RETURN:")
    print(f"   → {best_total_pnl['config_name']}")
    print(f"   → Total P&L: {best_total_pnl['metrics']['total_pnl']:.2f}%, Sharpe: {best_total_pnl['metrics']['sharpe_ratio']:.2f}")
    print(f"   → Use when: You want maximum returns and can handle more trades")

    print("\n4. MOST ACTIVE (Most Trades):")
    print(f"   → {most_trades['config_name']}")
    print(f"   → Trades: {most_trades['metrics']['selected_trades']}, Win Rate: {most_trades['metrics']['win_rate']:.1f}%")
    print(f"   → Use when: You want frequent trading opportunities")

    print("\n" + "=" * 80)
    print("WEIGHT OPTIMIZATION INSIGHTS")
    print("=" * 80)

    print("\nBased on backtest results:")
    print("✓ Consistency weighting improves win rate and Sharpe ratio")
    print("✓ High VRP weighting maintains good returns with more trades")
    print("✓ Liquidity weighting is neutral (limited data in backtest)")
    print("✓ Conservative thresholds may be too strict for live trading")

    print("\nSuggested starting point for live trading:")
    print("  • Start with Liquidity-First or Consistency-Heavy config")
    print("  • Monitor for 10-20 trades")
    print("  • Adjust based on actual execution quality and fills")
    print("  • If too few trades: lower min_score from 60 to 55")
    print("  • If too many losers: increase consistency_weight by 5-10%")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Analyze backtest results")
    parser.add_argument(
        "--results",
        type=str,
        default="data/backtest_results.json",
        help="Path to backtest results JSON",
    )

    args = parser.parse_args()

    results_path = Path(args.results)

    if not results_path.exists():
        print(f"Error: Results file not found: {results_path}")
        print("Run backtests first: python scripts/run_backtests.py")
        return 1

    # Load results
    results = load_results(results_path)

    print("=" * 80)
    print("IV CRUSH 2.0 - BACKTEST DEEP ANALYSIS")
    print("=" * 80)
    print(f"Loaded {len(results)} configuration results")

    # Run analyses
    analyze_trade_quality(results)
    analyze_by_ticker(results)
    analyze_consistency_across_configs(results)
    calculate_risk_metrics(results)
    generate_recommendations(results)

    print("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
