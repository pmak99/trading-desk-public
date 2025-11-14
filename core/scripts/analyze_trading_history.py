#!/usr/bin/env python3
"""
Analyze actual trading history from Accounts_History.csv.

Parses real trades including:
- Credit spreads (bull put, bear call)
- Iron condors
- Multi-leg positions

Calculates real P&L and generates actionable recommendations.
"""

import csv
import sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Tuple
import json
import re

def parse_symbol(description: str) -> Tuple[str, str, str, float]:
    """
    Parse option description to extract ticker, type, date, strike.

    Example: "CALL (AMAT) APPLIED MATERIALS NOV 14 25 $245 (100 SHS)"
    Returns: ('AMAT', 'CALL', '2025-11-14', 245.0)
    """
    # Extract ticker
    ticker_match = re.search(r'\(([A-Z]+)\)', description)
    ticker = ticker_match.group(1) if ticker_match else "UNKNOWN"

    # Extract type
    option_type = "CALL" if "CALL" in description else "PUT" if "PUT" in description else "UNKNOWN"

    # Extract strike
    strike_match = re.search(r'\$(\d+(?:\.\d+)?)', description)
    strike = float(strike_match.group(1)) if strike_match else 0.0

    # Extract date (e.g., "NOV 14 25")
    date_match = re.search(r'([A-Z]{3}) (\d+) (\d{2})', description)
    if date_match:
        month_str = date_match.group(1)
        day = date_match.group(2)
        year = "20" + date_match.group(3)

        months = {
            'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04',
            'MAY': '05', 'JUN': '06', 'JUL': '07', 'AUG': '08',
            'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
        }
        month = months.get(month_str, '01')
        exp_date = f"{year}-{month}-{day.zfill(2)}"
    else:
        exp_date = "UNKNOWN"

    return (ticker, option_type, exp_date, strike)

def parse_csv(csv_path: Path) -> List[Dict]:
    """Parse CSV and return list of trades."""
    trades = []

    with open(csv_path, 'r', encoding='utf-8-sig') as f:
        # Skip empty lines
        lines = [line for line in f if line.strip()]
        reader = csv.DictReader(lines)
        for row in reader:
            if not row.get('Description'):
                continue

            # Skip non-option trades
            if 'CALL' not in row['Description'] and 'PUT' not in row['Description']:
                continue

            try:
                quantity = int(row.get('Quantity', '0')) if row.get('Quantity') and row.get('Quantity').strip() not in ['', 'USD'] else 0
                price = float(row.get('Price', '0')) if row.get('Price') and row.get('Price').strip() else 0.0
                amount = float(row.get('Amount', '0').replace(',', '')) if row.get('Amount') and row.get('Amount').strip() else 0.0
                commission = float(row.get('Commission', '0')) if row.get('Commission') and row.get('Commission').strip() else 0.0
                fees = float(row.get('Fees', '0')) if row.get('Fees') and row.get('Fees').strip() else 0.0
            except (ValueError, AttributeError):
                continue

            trade = {
                'run_date': row['Run Date'],
                'action': row['Action'],
                'symbol': row['Symbol'],
                'description': row['Description'],
                'quantity': quantity,
                'price': price,
                'amount': amount,
                'commission': commission,
                'fees': fees,
            }

            # Parse symbol details
            ticker, opt_type, exp_date, strike = parse_symbol(row['Description'])
            trade['ticker'] = ticker
            trade['option_type'] = opt_type
            trade['exp_date'] = exp_date
            trade['strike'] = strike

            # Determine if opening or closing
            trade['is_opening'] = 'OPENING' in row['Action']
            trade['is_closing'] = 'CLOSING' in row['Action']
            trade['is_buy'] = 'BOUGHT' in row['Action']
            trade['is_sell'] = 'SOLD' in row['Action']

            trades.append(trade)

    return trades

def group_positions(trades: List[Dict]) -> Dict[str, List[Dict]]:
    """Group trades by position (ticker + expiration)."""
    positions = defaultdict(list)

    for trade in trades:
        key = f"{trade['ticker']}_{trade['exp_date']}"
        positions[key].append(trade)

    return dict(positions)

def calculate_position_pnl(position_trades: List[Dict]) -> Dict:
    """Calculate P&L for a complete position."""
    ticker = position_trades[0]['ticker']
    exp_date = position_trades[0]['exp_date']

    # Sum all amounts (credits positive, debits negative)
    total_pnl = sum(t['amount'] for t in position_trades)
    total_fees = sum(abs(t.get('fees', 0)) for t in position_trades)
    total_commission = sum(abs(t.get('commission', 0)) for t in position_trades)

    # Count legs
    opening_legs = [t for t in position_trades if t['is_opening']]
    closing_legs = [t for t in position_trades if t['is_closing']]

    # Determine position type
    calls = [t for t in opening_legs if t['option_type'] == 'CALL']
    puts = [t for t in opening_legs if t['option_type'] == 'PUT']

    if len(calls) >= 2 and len(puts) >= 2:
        position_type = "Iron Condor"
    elif len(calls) == 2:
        position_type = "Call Spread"
    elif len(puts) == 2:
        position_type = "Put Spread"
    elif len(calls) == 1 and len(puts) == 1:
        position_type = "Straddle/Strangle"
    else:
        position_type = "Other"

    # Determine if closed
    is_closed = len(closing_legs) > 0

    return {
        'ticker': ticker,
        'exp_date': exp_date,
        'position_type': position_type,
        'total_pnl': total_pnl,
        'net_pnl': total_pnl - total_fees - total_commission,
        'total_fees': total_fees,
        'total_commission': total_commission,
        'num_legs': len(opening_legs),
        'is_closed': is_closed,
        'num_trades': len(position_trades),
        'trades': position_trades,
    }

def analyze_performance(positions: List[Dict]) -> Dict:
    """Analyze overall trading performance."""
    closed_positions = [p for p in positions if p['is_closed']]

    if not closed_positions:
        return {
            'total_positions': len(positions),
            'closed_positions': 0,
            'open_positions': len(positions),
            'message': 'No closed positions to analyze'
        }

    total_pnl = sum(p['net_pnl'] for p in closed_positions)
    winners = [p for p in closed_positions if p['net_pnl'] > 0]
    losers = [p for p in closed_positions if p['net_pnl'] <= 0]

    win_rate = len(winners) / len(closed_positions) * 100 if closed_positions else 0
    avg_win = sum(p['net_pnl'] for p in winners) / len(winners) if winners else 0
    avg_loss = sum(p['net_pnl'] for p in losers) / len(losers) if losers else 0

    # Analyze by ticker
    by_ticker = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0})
    for p in closed_positions:
        by_ticker[p['ticker']]['count'] += 1
        by_ticker[p['ticker']]['pnl'] += p['net_pnl']
        if p['net_pnl'] > 0:
            by_ticker[p['ticker']]['wins'] += 1

    # Analyze by position type
    by_type = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0})
    for p in closed_positions:
        by_type[p['position_type']]['count'] += 1
        by_type[p['position_type']]['pnl'] += p['net_pnl']
        if p['net_pnl'] > 0:
            by_type[p['position_type']]['wins'] += 1

    return {
        'total_positions': len(positions),
        'closed_positions': len(closed_positions),
        'open_positions': len(positions) - len(closed_positions),
        'total_pnl': total_pnl,
        'win_rate': win_rate,
        'winners': len(winners),
        'losers': len(losers),
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'by_ticker': dict(by_ticker),
        'by_type': dict(by_type),
        'best_trades': sorted(closed_positions, key=lambda x: x['net_pnl'], reverse=True)[:5],
        'worst_trades': sorted(closed_positions, key=lambda x: x['net_pnl'])[:5],
    }

def print_summary(analysis: Dict, positions: List[Dict]):
    """Print analysis summary."""
    print("=" * 80)
    print("ACTUAL TRADING HISTORY ANALYSIS - Last 90 Days")
    print("=" * 80)
    print()

    if analysis.get('message'):
        print(analysis['message'])
        return

    print("OVERALL PERFORMANCE")
    print("=" * 80)
    print(f"Total Positions: {analysis['total_positions']}")
    print(f"Closed Positions: {analysis['closed_positions']}")
    print(f"Open Positions: {analysis['open_positions']}")
    print()
    print(f"Total P&L: ${analysis['total_pnl']:,.2f}")
    print(f"Win Rate: {analysis['win_rate']:.1f}%")
    print(f"Winners: {analysis['winners']}")
    print(f"Losers: {analysis['losers']}")
    print(f"Avg Win: ${analysis['avg_win']:,.2f}")
    print(f"Avg Loss: ${analysis['avg_loss']:,.2f}")
    print()

    # By position type
    print("=" * 80)
    print("PERFORMANCE BY POSITION TYPE")
    print("=" * 80)
    for ptype, stats in analysis['by_type'].items():
        wr = stats['wins'] / stats['count'] * 100 if stats['count'] > 0 else 0
        print(f"{ptype:20s}: {stats['count']:3d} positions, ${stats['pnl']:8,.2f} P&L, {wr:5.1f}% WR")
    print()

    # By ticker
    print("=" * 80)
    print("PERFORMANCE BY TICKER (Top 10)")
    print("=" * 80)
    sorted_tickers = sorted(analysis['by_ticker'].items(),
                           key=lambda x: x[1]['pnl'], reverse=True)[:10]
    for ticker, stats in sorted_tickers:
        wr = stats['wins'] / stats['count'] * 100 if stats['count'] > 0 else 0
        print(f"{ticker:6s}: {stats['count']:3d} positions, ${stats['pnl']:8,.2f} P&L, {wr:5.1f}% WR")
    print()

    # Best trades
    print("=" * 80)
    print("BEST 5 TRADES")
    print("=" * 80)
    for trade in analysis['best_trades']:
        print(f"{trade['ticker']:6s} {trade['exp_date']:12s} {trade['position_type']:20s} ${trade['net_pnl']:8,.2f}")
    print()

    # Worst trades
    print("=" * 80)
    print("WORST 5 TRADES")
    print("=" * 80)
    for trade in analysis['worst_trades']:
        print(f"{trade['ticker']:6s} {trade['exp_date']:12s} {trade['position_type']:20s} ${trade['net_pnl']:8,.2f}")
    print()

def main():
    """Main entry point."""
    csv_path = Path("/Users/prashant/Desktop/Accounts_History.csv")

    if not csv_path.exists():
        print(f"Error: CSV file not found at {csv_path}")
        return 1

    print("Reading trading history...")
    trades = parse_csv(csv_path)
    print(f"✓ Loaded {len(trades)} option trades")

    print("\nGrouping into positions...")
    grouped = group_positions(trades)
    print(f"✓ Identified {len(grouped)} unique positions")

    print("\nCalculating P&L...")
    positions = [calculate_position_pnl(pos_trades) for pos_trades in grouped.values()]
    print(f"✓ Calculated P&L for all positions")

    print("\nAnalyzing performance...\n")
    analysis = analyze_performance(positions)

    # Print summary
    print_summary(analysis, positions)

    # Save results
    output = {
        'analysis_date': datetime.now().isoformat(),
        'source': str(csv_path),
        'summary': {
            k: v for k, v in analysis.items()
            if k not in ['best_trades', 'worst_trades']
        },
        'positions': [
            {k: v for k, v in p.items() if k != 'trades'}
            for p in positions
        ],
    }

    output_path = Path("results/actual_trading_analysis.json")
    output_path.parent.mkdir(exist_ok=True)

    with open(output_path, 'w') as f:
        json.dump(output, f, indent=2, default=str)

    print(f"✓ Results saved to: {output_path}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
