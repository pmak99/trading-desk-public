#!/usr/bin/env python3
"""
Analyze complete Q1-Q3 2025 trading history by ticker.
Processes actual Fidelity account data across all quarters.
"""

import pandas as pd
from collections import defaultdict
from pathlib import Path
import re

def extract_ticker_from_description(description):
    """Extract ticker symbol from option or stock description."""
    if pd.isna(description):
        return None

    # Option format: "PUT (TICKER) ..." or "CALL (TICKER) ..."
    match = re.search(r'(?:PUT|CALL)\s+\(([A-Z]+)\)', description)
    if match:
        return match.group(1)

    # Stock format: Direct symbol
    return None

def parse_trade_data(csv_files):
    """Parse all CSV files and extract trade P&L by ticker."""

    ticker_data = defaultdict(lambda: {
        'trades': [],
        'total_pnl': 0,
        'num_trades': 0,
        'wins': 0,
        'losses': 0
    })

    all_trades = []

    for csv_file in csv_files:
        print(f"Processing {csv_file.name}...")

        try:
            # Use error_bad_lines=False to skip problematic lines
            df = pd.read_csv(csv_file, skiprows=1, on_bad_lines='skip', engine='python')

            # Filter for actual trades (exclude dividends, transfers, etc.)
            trade_actions = [
                'YOU SOLD CLOSING',
                'YOU BOUGHT CLOSING',
                'YOU SOLD OPENING',
                'YOU BOUGHT OPENING',
                'EXPIRED'
            ]

            df = df[df['Action'].str.contains('|'.join(trade_actions), na=False)]

            # Process each trade
            for idx, row in df.iterrows():
                description = row['Description']
                symbol = row.get('Symbol', '')
                action = row['Action']
                amount = row.get('Amount', 0)

                # Skip if no amount
                if pd.isna(amount) or amount == 0:
                    continue

                # Extract ticker
                ticker = extract_ticker_from_description(description)
                if not ticker:
                    # Try symbol column for stocks
                    if symbol and symbol not in ['SPAXX', 'VOO', 'SPY', 'FXAIX']:
                        ticker = symbol

                if not ticker or ticker in ['SPAXX']:  # Skip money market
                    continue

                # Store trade
                all_trades.append({
                    'date': row['Run Date'],
                    'ticker': ticker,
                    'action': action,
                    'description': description,
                    'amount': amount,
                    'symbol': symbol
                })

            print(f"  ✓ Found {len(df)} trade actions")

        except Exception as e:
            print(f"  ✗ Error: {e}")
            continue

    # Group trades by ticker and position
    # Need to match opening and closing transactions
    positions = defaultdict(list)

    for trade in all_trades:
        ticker = trade['ticker']
        positions[ticker].append(trade)

    # Calculate P&L for each ticker
    for ticker, trades in positions.items():
        # Sum all amounts for this ticker (positive = profit, negative = loss)
        total = sum(t['amount'] for t in trades)

        ticker_data[ticker]['total_pnl'] = total
        ticker_data[ticker]['num_trades'] = len(trades)
        ticker_data[ticker]['trades'] = trades

        if total > 0:
            ticker_data[ticker]['wins'] = 1
        elif total < 0:
            ticker_data[ticker]['losses'] = 1

    return ticker_data

def main():
    # CSV file paths
    csv_files = [
        Path('/Users/prashant/Desktop/Q1.csv'),
        Path('/Users/prashant/Desktop/Q2.csv'),
        Path('/Users/prashant/Desktop/Q3.csv'),
        Path('/Users/prashant/Desktop/90_days.csv')
    ]

    # Check which files exist
    existing_files = [f for f in csv_files if f.exists()]
    print(f"Found {len(existing_files)} CSV files to process\n")

    if not existing_files:
        print("ERROR: No CSV files found!")
        return

    # Parse data
    ticker_data = parse_trade_data(existing_files)

    # Convert to list and sort by total P&L
    results = []
    for ticker, data in ticker_data.items():
        results.append({
            'ticker': ticker,
            'total_pnl': data['total_pnl'],
            'num_trades': data['num_trades'],
            'wins': data['wins'],
            'losses': data['losses']
        })

    results.sort(key=lambda x: x['total_pnl'], reverse=True)

    # Print results
    print("\n" + "=" * 90)
    print("FULL YEAR 2025 TRADING PERFORMANCE BY TICKER (Q1-Q3 + Recent)")
    print("=" * 90)
    print(f"{'Rank':<6} {'Ticker':<8} {'Total P&L':>15} {'Trades':>8} {'W':>4} {'L':>4}")
    print("-" * 90)

    for i, result in enumerate(results, 1):
        pnl_str = f"${result['total_pnl']:,.2f}"
        print(f"{i:<6} {result['ticker']:<8} {pnl_str:>15} {result['num_trades']:>8} "
              f"{result['wins']:>4} {result['losses']:>4}")

    print("-" * 90)

    # Summary
    total_pnl = sum(r['total_pnl'] for r in results)
    total_trades = sum(r['num_trades'] for r in results)
    profitable_tickers = sum(1 for r in results if r['total_pnl'] > 0)
    losing_tickers = sum(1 for r in results if r['total_pnl'] < 0)

    print(f"\nSUMMARY:")
    print(f"  Total P&L: ${total_pnl:,.2f}")
    print(f"  Total Trade Actions: {total_trades}")
    print(f"  Unique Tickers: {len(results)}")
    print(f"  Profitable Tickers: {profitable_tickers}")
    print(f"  Losing Tickers: {losing_tickers}")
    print(f"  Avg P&L per Ticker: ${total_pnl/len(results):,.2f}" if results else "  N/A")

    # Top/Bottom 10
    print(f"\n{'='*90}")
    print("TOP 10 MOST PROFITABLE TICKERS:")
    print(f"{'='*90}")
    for i, r in enumerate(results[:10], 1):
        print(f"  {i:2}. {r['ticker']:<8} ${r['total_pnl']:>12,.2f}  ({r['num_trades']} trades)")

    print(f"\n{'='*90}")
    print("TOP 10 WORST PERFORMING TICKERS:")
    print(f"{'='*90}")
    for i, r in enumerate(results[-10:][::-1], 1):
        print(f"  {i:2}. {r['ticker']:<8} ${r['total_pnl']:>12,.2f}  ({r['num_trades']} trades)")

    print("=" * 90)

if __name__ == "__main__":
    main()
