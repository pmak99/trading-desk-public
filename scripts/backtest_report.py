#!/usr/bin/env python3
"""
Generate comprehensive backtest report for IV Crush strategy.
Analyzes historical moves database and compares to actual trading results.
"""

import sys
import sqlite3
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime
import statistics


def get_db_path() -> Path:
    """Get path to ivcrush database."""
    return Path(__file__).parent.parent / "2.0" / "data" / "ivcrush.db"


def get_journal_path() -> Path:
    """Get path to trading journal."""
    return Path(__file__).parent.parent / "docs" / "2025 Trades" / "trading_data_2025_v3.json"


def load_historical_data(ticker: str = None) -> list[dict]:
    """Load historical moves from database."""
    db_path = get_db_path()

    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return []

    base_query = """
        SELECT ticker, earnings_date,
               close_move_pct as actual_move_pct,
               CASE WHEN earnings_close > prev_close THEN 'UP' ELSE 'DOWN' END as direction,
               prev_close as close_before, earnings_close as close_after
        FROM historical_moves
        {where_clause}
        ORDER BY earnings_date DESC
    """

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if ticker:
            query = base_query.format(where_clause="WHERE ticker = ?")
            cursor = conn.execute(query, (ticker.upper(),))
        else:
            query = base_query.format(where_clause="")
            cursor = conn.execute(query)
        results = [dict(row) for row in cursor.fetchall()]

    return results


def load_trading_journal() -> dict:
    """Load actual trading results."""
    journal_path = get_journal_path()

    if not journal_path.exists():
        return {}

    try:
        with open(journal_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"Warning: Could not load trading journal: {e}")
        return {}


def calculate_backtest_metrics(moves: list[dict], implied_move_target: float = 8.0) -> dict:
    """Calculate backtest performance metrics."""
    if not moves:
        return {}

    move_pcts = [m['actual_move_pct'] for m in moves]

    # Basic stats
    mean_move = statistics.mean(move_pcts)
    median_move = statistics.median(move_pcts)
    stdev = statistics.stdev(move_pcts) if len(move_pcts) > 1 else 0

    # Win rate at different thresholds
    def win_rate_at(threshold):
        wins = len([m for m in move_pcts if m < threshold])
        return wins / len(move_pcts) * 100 if move_pcts else 0

    # Tail risk
    moves_over_20 = len([m for m in move_pcts if m > 20])
    moves_over_15 = len([m for m in move_pcts if m > 15])
    moves_over_10 = len([m for m in move_pcts if m > 10])

    # By ticker stats
    ticker_stats = defaultdict(lambda: {'moves': [], 'count': 0})
    for m in moves:
        ticker_stats[m['ticker']]['moves'].append(m['actual_move_pct'])
        ticker_stats[m['ticker']]['count'] += 1

    # Calculate per-ticker metrics
    ticker_metrics = {}
    for ticker, data in ticker_stats.items():
        if data['count'] >= 4:  # Minimum samples
            ticker_metrics[ticker] = {
                'count': data['count'],
                'mean': statistics.mean(data['moves']),
                'median': statistics.median(data['moves']),
                'stdev': statistics.stdev(data['moves']) if len(data['moves']) > 1 else 0,
                'max': max(data['moves']),
                'win_rate_at_8': sum(1 for m in data['moves'] if m < 8) / len(data['moves']) * 100
            }

    # Best tickers (lowest mean move)
    best_tickers = sorted(ticker_metrics.items(), key=lambda x: x[1]['mean'])[:10]
    worst_tickers = sorted(ticker_metrics.items(), key=lambda x: x[1]['mean'], reverse=True)[:10]

    # Monthly/seasonal analysis
    monthly_stats = defaultdict(list)
    for m in moves:
        month = m['earnings_date'][5:7]  # Extract MM from YYYY-MM-DD
        monthly_stats[month].append(m['actual_move_pct'])

    monthly_metrics = {
        month: {
            'count': len(moves_list),
            'mean': statistics.mean(moves_list),
            'win_rate_at_8': sum(1 for m in moves_list if m < 8) / len(moves_list) * 100
        }
        for month, moves_list in monthly_stats.items()
    }

    return {
        'total_events': len(moves),
        'mean_move': mean_move,
        'median_move': median_move,
        'stdev': stdev,
        'min_move': min(move_pcts),
        'max_move': max(move_pcts),
        'win_rate_at_5': win_rate_at(5),
        'win_rate_at_8': win_rate_at(8),
        'win_rate_at_10': win_rate_at(10),
        'win_rate_at_12': win_rate_at(12),
        'win_rate_at_15': win_rate_at(15),
        'tail_risk_10pct': moves_over_10 / len(moves) * 100,
        'tail_risk_15pct': moves_over_15 / len(moves) * 100,
        'tail_risk_20pct': moves_over_20 / len(moves) * 100,
        'best_tickers': best_tickers,
        'worst_tickers': worst_tickers,
        'monthly_metrics': monthly_metrics,
        'unique_tickers': len(ticker_stats)
    }


def print_report(metrics: dict, journal: dict = None):
    """Print formatted backtest report."""
    print("\n" + "="*70)
    print("IV CRUSH STRATEGY - BACKTEST REPORT")
    print("="*70)

    print(f"\nDATASET OVERVIEW")
    print(f"  Total earnings events: {metrics['total_events']:,}")
    print(f"  Unique tickers: {metrics['unique_tickers']}")

    print(f"\nMOVE STATISTICS")
    print(f"  Mean move:   {metrics['mean_move']:.2f}%")
    print(f"  Median move: {metrics['median_move']:.2f}%")
    print(f"  Std Dev:     {metrics['stdev']:.2f}%")
    print(f"  Range:       {metrics['min_move']:.2f}% - {metrics['max_move']:.2f}%")

    print(f"\nTHEORETICAL WIN RATES")
    print(f"  At 5% implied:  {metrics['win_rate_at_5']:.1f}%")
    print(f"  At 8% implied:  {metrics['win_rate_at_8']:.1f}%")
    print(f"  At 10% implied: {metrics['win_rate_at_10']:.1f}%")
    print(f"  At 12% implied: {metrics['win_rate_at_12']:.1f}%")
    print(f"  At 15% implied: {metrics['win_rate_at_15']:.1f}%")

    print(f"\nTAIL RISK (large moves)")
    print(f"  Moves > 10%: {metrics['tail_risk_10pct']:.1f}%")
    print(f"  Moves > 15%: {metrics['tail_risk_15pct']:.1f}%")
    print(f"  Moves > 20%: {metrics['tail_risk_20pct']:.1f}%")

    print(f"\nBEST TICKERS FOR IV CRUSH (lowest mean move)")
    print(f"  {'Ticker':<8} {'Events':>8} {'Mean':>8} {'StdDev':>8} {'WinRate@8%':>12}")
    print(f"  {'-'*48}")
    for ticker, data in metrics['best_tickers']:
        print(f"  {ticker:<8} {data['count']:>8} {data['mean']:>7.2f}% {data['stdev']:>7.2f}% {data['win_rate_at_8']:>11.1f}%")

    print(f"\nTICKERS TO AVOID (highest mean move)")
    print(f"  {'Ticker':<8} {'Events':>8} {'Mean':>8} {'Max':>8} {'WinRate@8%':>12}")
    print(f"  {'-'*48}")
    for ticker, data in metrics['worst_tickers']:
        print(f"  {ticker:<8} {data['count']:>8} {data['mean']:>7.2f}% {data['max']:>7.2f}% {data['win_rate_at_8']:>11.1f}%")

    print(f"\nSEASONAL ANALYSIS (by month)")
    print(f"  {'Month':<8} {'Events':>8} {'Mean':>8} {'WinRate@8%':>12}")
    print(f"  {'-'*40}")
    for month in sorted(metrics['monthly_metrics'].keys()):
        data = metrics['monthly_metrics'][month]
        month_name = datetime.strptime(month, '%m').strftime('%b')
        print(f"  {month_name:<8} {data['count']:>8} {data['mean']:>7.2f}% {data['win_rate_at_8']:>11.1f}%")

    # Compare to actual trading if journal available
    if journal and 'summary' in journal:
        actual = journal['summary']
        print(f"\n{'='*70}")
        print("ACTUAL VS BACKTEST COMPARISON (2025 YTD)")
        print("="*70)

        total_trades = actual.get('total_trades', 0)
        if total_trades == 0:
            print("\n  No trades found in journal")
            return

        print(f"\nActual Trading Results:")
        print(f"  Total trades: {total_trades}")
        print(f"  Winners: {actual['winners']} ({actual['winners']/total_trades*100:.1f}%)")
        print(f"  Losers: {actual['losers']} ({actual['losers']/total_trades*100:.1f}%)")
        print(f"  Total P&L: ${actual.get('total_pl', 0):,.2f}")

        actual_win_rate = actual['winners'] / total_trades * 100
        expected_win_rate = metrics['win_rate_at_8']

        print(f"\nWin Rate Analysis:")
        print(f"  Expected (backtest): {expected_win_rate:.1f}%")
        print(f"  Actual (2025):       {actual_win_rate:.1f}%")
        print(f"  Variance:            {actual_win_rate - expected_win_rate:+.1f}%")

    print("\n" + "="*70)


def main():
    """Main entry point."""
    ticker = sys.argv[1].upper() if len(sys.argv) > 1 else None

    if ticker and ticker not in ['ALL', 'FULL', 'REPORT']:
        print(f"Running backtest for: {ticker}")
        moves = load_historical_data(ticker)
    else:
        print("Running full backtest report...")
        moves = load_historical_data()

    if not moves:
        print("No historical data found")
        sys.exit(1)

    metrics = calculate_backtest_metrics(moves)
    journal = load_trading_journal()

    print_report(metrics, journal)


if __name__ == "__main__":
    main()
