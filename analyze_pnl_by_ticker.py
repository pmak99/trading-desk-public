#!/usr/bin/env python3
"""
Analyze P&L by ticker from backtest results.
Aggregates across all configurations and ranks tickers by profitability.
"""

import json
from collections import defaultdict
from pathlib import Path

def analyze_pnl_by_ticker():
    """Analyze and rank tickers by total P&L."""

    # Load backtest results
    results_path = Path("2.0/data/backtest_results.json")
    with open(results_path) as f:
        results = json.load(f)

    # Aggregate P&L by ticker across all configurations
    ticker_stats = defaultdict(lambda: {
        'total_pnl': 0.0,
        'num_trades': 0,
        'wins': 0,
        'losses': 0,
        'pnl_list': []
    })

    # Process all trades from all configurations
    for config in results:
        for trade in config['trades']:
            if trade['selected']:  # Only count selected trades
                ticker = trade['ticker']
                pnl = trade['pnl']

                ticker_stats[ticker]['total_pnl'] += pnl
                ticker_stats[ticker]['num_trades'] += 1
                ticker_stats[ticker]['pnl_list'].append(pnl)

                if pnl > 0:
                    ticker_stats[ticker]['wins'] += 1
                else:
                    ticker_stats[ticker]['losses'] += 1

    # Convert to list and calculate additional metrics
    ticker_results = []
    for ticker, stats in ticker_stats.items():
        win_rate = (stats['wins'] / stats['num_trades'] * 100) if stats['num_trades'] > 0 else 0
        avg_pnl = stats['total_pnl'] / stats['num_trades'] if stats['num_trades'] > 0 else 0

        ticker_results.append({
            'ticker': ticker,
            'total_pnl': stats['total_pnl'],
            'num_trades': stats['num_trades'],
            'wins': stats['wins'],
            'losses': stats['losses'],
            'win_rate': win_rate,
            'avg_pnl': avg_pnl
        })

    # Sort by total P&L (descending)
    ticker_results.sort(key=lambda x: x['total_pnl'], reverse=True)

    # Print results
    print("=" * 90)
    print("PROFIT/LOSS BY TICKER - RANKED MOST PROFITABLE TO LEAST")
    print("=" * 90)
    print(f"{'Ticker':<8} {'Total P&L':>12} {'Trades':>8} {'Wins':>6} {'Losses':>7} {'Win Rate':>10} {'Avg P&L':>12}")
    print("-" * 90)

    for result in ticker_results:
        print(f"{result['ticker']:<8} "
              f"${result['total_pnl']:>11.2f} "
              f"{result['num_trades']:>8} "
              f"{result['wins']:>6} "
              f"{result['losses']:>7} "
              f"{result['win_rate']:>9.1f}% "
              f"${result['avg_pnl']:>11.2f}")

    print("-" * 90)

    # Summary statistics
    total_pnl = sum(r['total_pnl'] for r in ticker_results)
    total_trades = sum(r['num_trades'] for r in ticker_results)
    profitable_tickers = sum(1 for r in ticker_results if r['total_pnl'] > 0)
    losing_tickers = sum(1 for r in ticker_results if r['total_pnl'] < 0)

    print(f"\nSUMMARY:")
    print(f"  Total P&L: ${total_pnl:.2f}")
    print(f"  Total Trades: {total_trades}")
    print(f"  Unique Tickers: {len(ticker_results)}")
    print(f"  Profitable Tickers: {profitable_tickers}")
    print(f"  Losing Tickers: {losing_tickers}")
    print(f"  Overall Avg P&L per Trade: ${total_pnl/total_trades:.2f}")
    print()

    # Top 5 winners and losers
    print("\nTOP 5 MOST PROFITABLE:")
    for i, result in enumerate(ticker_results[:5], 1):
        print(f"  {i}. {result['ticker']}: ${result['total_pnl']:.2f} ({result['num_trades']} trades, {result['win_rate']:.1f}% win rate)")

    print("\nTOP 5 LEAST PROFITABLE:")
    for i, result in enumerate(ticker_results[-5:][::-1], 1):
        print(f"  {i}. {result['ticker']}: ${result['total_pnl']:.2f} ({result['num_trades']} trades, {result['win_rate']:.1f}% win rate)")

    print("=" * 90)

if __name__ == "__main__":
    analyze_pnl_by_ticker()
