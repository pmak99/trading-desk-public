#!/usr/bin/env python3
"""
Create a validated and augmented trading journal from the transaction log.
This script parses the transaction log, groups trades into spreads,
and creates a comprehensive trading journal with all relevant fields.
"""

import csv
import json
from datetime import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
import re

@dataclass
class Trade:
    """Represents a single trade from the transaction log"""
    date: str
    ticker: str
    type: str  # Put, Call, Stock, ETF
    quantity: int
    opening_price: float
    closing_price: float
    total_pnl: float
    notes: str  # Close Short, Close Long, *Wash, [Spread Leg]

    @property
    def is_short(self) -> bool:
        return "Close Short" in self.notes

    @property
    def is_long(self) -> bool:
        return "Close Long" in self.notes

    @property
    def is_wash_sale(self) -> bool:
        return "*Wash" in self.notes

    @property
    def is_spread_leg(self) -> bool:
        return "[Spread Leg]" in self.notes

    @property
    def strategy(self) -> str:
        """Determine strategy based on direction and type"""
        if self.type in ["Stock", "ETF"]:
            return "long_stock" if self.is_long else "short_stock"

        direction = "short" if self.is_short else "long"
        option_type = self.type.lower()
        return f"{direction}_{option_type}"


def parse_transaction_log(filepath: str) -> List[Trade]:
    """Parse the transaction log file and return list of Trade objects"""
    trades = []

    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Parse numeric values
            quantity = int(row['Quantity'])
            opening_price = float(row['Opening Price'])
            closing_price = float(row['Closing Price'])
            total_pnl = float(row['Total P&L'].replace(',', ''))

            trade = Trade(
                date=row['Date'],
                ticker=row['Ticker'],
                type=row['Type'],
                quantity=quantity,
                opening_price=opening_price,
                closing_price=closing_price,
                total_pnl=total_pnl,
                notes=row['Notes']
            )
            trades.append(trade)

    return trades


def group_trades_into_spreads(trades: List[Trade]) -> List[Dict]:
    """
    Group trades into spreads based on same date, ticker, type (or complementary types).
    Returns a list of trade entries for the journal.
    """
    journal_entries = []

    # Group by date and ticker first
    grouped = defaultdict(list)
    for trade in trades:
        key = (trade.date, trade.ticker, trade.type)
        grouped[key].append(trade)

    # Also track which trades we've processed
    processed_indices = set()

    for i, trade in enumerate(trades):
        if i in processed_indices:
            continue

        # Check if this is part of a spread (same date, same ticker, different direction legs)
        date = trade.date
        ticker = trade.ticker
        opt_type = trade.type

        # Find matching trades on same date for potential spread
        matching_trades = []
        for j, other in enumerate(trades):
            if j == i or j in processed_indices:
                continue
            if other.date == date and other.ticker == ticker:
                # Same type with opposite direction = spread
                if other.type == opt_type and other.is_short != trade.is_short:
                    matching_trades.append((j, other))

        # If we have matching trades, it's a spread
        if matching_trades:
            # Combine all legs
            all_legs = [(i, trade)] + matching_trades

            # Calculate combined P&L
            combined_pnl = sum(t.total_pnl for _, t in all_legs)

            # Determine overall strategy
            short_legs = [t for _, t in all_legs if t.is_short]
            long_legs = [t for _, t in all_legs if t.is_long]

            # Check for wash sales in any leg
            has_wash = any(t.is_wash_sale for _, t in all_legs)

            # Determine spread type
            if short_legs and long_legs:
                # It's a vertical spread
                if short_legs[0].type in ['Put', 'PUT']:
                    strategy = "put_spread"
                else:
                    strategy = "call_spread"

                # Determine if it's a credit or debit spread based on net premium
                # Credit spread = short strike closer to money (higher premium)
                # For credit spreads: opened for credit, closed for debit
                total_short_pnl = sum(t.total_pnl for t in short_legs)
                if total_short_pnl > 0:
                    strategy = f"credit_{strategy}"
                else:
                    strategy = f"debit_{strategy}"
            else:
                # Just multiple lots of same direction
                strategy = trade.strategy

            # Create a combined journal entry
            total_qty = sum(t.quantity for _, t in all_legs)

            entry = {
                'date_close': datetime.strptime(date, '%m/%d/%Y').strftime('%Y-%m-%d'),
                'ticker': ticker,
                'type': opt_type,
                'quantity': total_qty,
                'strategy': strategy,
                'net_pnl': round(combined_pnl, 2),
                'is_winner': combined_pnl > 0,
                'is_wash_sale': has_wash,
                'is_spread': True,
                'num_legs': len(all_legs),
                'legs': [{
                    'direction': 'SHORT' if t.is_short else 'LONG',
                    'quantity': t.quantity,
                    'open_price': t.opening_price,
                    'close_price': t.closing_price,
                    'pnl': t.total_pnl
                } for _, t in all_legs]
            }
            journal_entries.append(entry)

            # Mark all legs as processed
            for idx, _ in all_legs:
                processed_indices.add(idx)
        else:
            # Single trade, not part of a spread
            entry = {
                'date_close': datetime.strptime(date, '%m/%d/%Y').strftime('%Y-%m-%d'),
                'ticker': ticker,
                'type': trade.type,
                'quantity': trade.quantity,
                'strategy': trade.strategy,
                'open_price': trade.opening_price,
                'close_price': trade.closing_price,
                'net_pnl': round(trade.total_pnl, 2),
                'is_winner': trade.total_pnl > 0,
                'is_wash_sale': trade.is_wash_sale,
                'is_spread': False,
                'num_legs': 1
            }
            journal_entries.append(entry)
            processed_indices.add(i)

    return journal_entries


def calculate_statistics(journal_entries: List[Dict]) -> Dict:
    """Calculate comprehensive trading statistics"""

    total_trades = len(journal_entries)
    winners = [e for e in journal_entries if e['is_winner']]
    losers = [e for e in journal_entries if not e['is_winner']]

    total_pnl = sum(e['net_pnl'] for e in journal_entries)
    total_from_winners = sum(e['net_pnl'] for e in winners)
    total_from_losers = sum(e['net_pnl'] for e in losers)

    # By strategy
    by_strategy = defaultdict(lambda: {'trades': 0, 'wins': 0, 'losses': 0, 'pnl': 0})
    for entry in journal_entries:
        strat = entry['strategy']
        by_strategy[strat]['trades'] += 1
        by_strategy[strat]['pnl'] += entry['net_pnl']
        if entry['is_winner']:
            by_strategy[strat]['wins'] += 1
        else:
            by_strategy[strat]['losses'] += 1

    # Calculate win rates
    for strat in by_strategy:
        trades = by_strategy[strat]['trades']
        wins = by_strategy[strat]['wins']
        by_strategy[strat]['win_pct'] = round(100 * wins / trades, 1) if trades > 0 else 0
        by_strategy[strat]['pnl'] = round(by_strategy[strat]['pnl'], 2)

    # By month
    by_month = defaultdict(lambda: {'trades': 0, 'pnl': 0, 'wins': 0, 'losses': 0})
    for entry in journal_entries:
        month = entry['date_close'][:7]  # YYYY-MM
        by_month[month]['trades'] += 1
        by_month[month]['pnl'] += entry['net_pnl']
        if entry['is_winner']:
            by_month[month]['wins'] += 1
        else:
            by_month[month]['losses'] += 1

    # By ticker
    by_ticker = defaultdict(lambda: {'trades': 0, 'pnl': 0, 'wins': 0})
    for entry in journal_entries:
        ticker = entry['ticker']
        by_ticker[ticker]['trades'] += 1
        by_ticker[ticker]['pnl'] += entry['net_pnl']
        if entry['is_winner']:
            by_ticker[ticker]['wins'] += 1

    # Top winners and losers by ticker
    ticker_stats = []
    for ticker, stats in by_ticker.items():
        ticker_stats.append({
            'ticker': ticker,
            'trades': stats['trades'],
            'pnl': round(stats['pnl'], 2),
            'win_rate': round(100 * stats['wins'] / stats['trades'], 1) if stats['trades'] > 0 else 0
        })

    top_winners = sorted(ticker_stats, key=lambda x: x['pnl'], reverse=True)[:10]
    top_losers = sorted(ticker_stats, key=lambda x: x['pnl'])[:10]

    # Wash sales
    wash_sales = [e for e in journal_entries if e['is_wash_sale']]
    wash_sale_pnl = sum(e['net_pnl'] for e in wash_sales)

    # Spreads vs single legs
    spreads = [e for e in journal_entries if e['is_spread']]
    singles = [e for e in journal_entries if not e['is_spread']]

    return {
        'total_trades': total_trades,
        'winners': len(winners),
        'losers': len(losers),
        'win_rate_pct': round(100 * len(winners) / total_trades, 1) if total_trades > 0 else 0,
        'total_pnl': round(total_pnl, 2),
        'total_from_winners': round(total_from_winners, 2),
        'total_from_losers': round(total_from_losers, 2),
        'avg_win': round(total_from_winners / len(winners), 2) if winners else 0,
        'avg_loss': round(total_from_losers / len(losers), 2) if losers else 0,
        'profit_factor': round(abs(total_from_winners / total_from_losers), 2) if total_from_losers else 0,
        'by_strategy': dict(by_strategy),
        'by_month': {k: {'trades': v['trades'], 'pnl': round(v['pnl'], 2),
                        'wins': v['wins'], 'losses': v['losses']}
                    for k, v in sorted(by_month.items())},
        'top_tickers_profit': top_winners,
        'top_tickers_loss': top_losers,
        'wash_sales': {
            'count': len(wash_sales),
            'total_pnl': round(wash_sale_pnl, 2)
        },
        'spreads': {
            'count': len(spreads),
            'pnl': round(sum(e['net_pnl'] for e in spreads), 2)
        },
        'single_legs': {
            'count': len(singles),
            'pnl': round(sum(e['net_pnl'] for e in singles), 2)
        }
    }


def write_journal_csv(entries: List[Dict], filepath: str):
    """Write journal entries to CSV file"""

    # Flatten entries for CSV
    rows = []
    for entry in entries:
        row = {
            'Date Close': entry['date_close'],
            'Ticker': entry['ticker'],
            'Type': entry['type'],
            'Quantity': entry['quantity'],
            'Strategy': entry['strategy'],
            'Net P&L': f"${entry['net_pnl']:,.2f}",
            'Winner': 'YES' if entry['is_winner'] else 'NO',
            'Wash Sale': 'YES' if entry['is_wash_sale'] else 'NO',
            'Is Spread': 'YES' if entry['is_spread'] else 'NO',
            'Num Legs': entry['num_legs']
        }

        # Add price info for single trades
        if not entry['is_spread']:
            row['Open Price'] = entry.get('open_price', '')
            row['Close Price'] = entry.get('close_price', '')
        else:
            row['Open Price'] = ''
            row['Close Price'] = ''

        rows.append(row)

    # Write CSV
    fieldnames = ['Date Close', 'Ticker', 'Type', 'Quantity', 'Strategy',
                  'Open Price', 'Close Price', 'Net P&L', 'Winner',
                  'Wash Sale', 'Is Spread', 'Num Legs']

    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    # Paths
    input_file = "/path/to/transaction_log.txt"
    output_dir = "$PROJECT_ROOT/docs/2025 Trades"

    print("=" * 60)
    print("TRADING JOURNAL VALIDATION & AUGMENTATION")
    print("=" * 60)

    # Parse transaction log
    print("\n📖 Parsing transaction log...")
    trades = parse_transaction_log(input_file)
    print(f"   Found {len(trades)} individual trade records")

    # Group into spreads
    print("\n🔗 Grouping trades into spreads...")
    journal_entries = group_trades_into_spreads(trades)
    print(f"   Created {len(journal_entries)} journal entries")

    # Calculate statistics
    print("\n📊 Calculating statistics...")
    stats = calculate_statistics(journal_entries)

    # Print summary
    print("\n" + "=" * 60)
    print("📊 OVERALL STATISTICS")
    print("=" * 60)
    print(f"Total Trades:          {stats['total_trades']}")
    print(f"Winners:               {stats['winners']} ({stats['win_rate_pct']}%)")
    print(f"Losers:                {stats['losers']}")
    print(f"\n💰 PROFIT & LOSS")
    print(f"Total from Winners:    ${stats['total_from_winners']:,.2f}")
    print(f"Total from Losers:     ${stats['total_from_losers']:,.2f}")
    print(f"NET PROFIT:            ${stats['total_pnl']:,.2f}")
    print(f"Profit Factor:         {stats['profit_factor']}")
    print(f"Average Win:           ${stats['avg_win']:,.2f}")
    print(f"Average Loss:          ${stats['avg_loss']:,.2f}")

    print(f"\n📈 BY STRATEGY")
    for strat, data in sorted(stats['by_strategy'].items(), key=lambda x: x[1]['pnl'], reverse=True):
        print(f"  {strat:25} {data['trades']:3} trades  {data['win_pct']:5.1f}% win  ${data['pnl']:>12,.2f}")

    print(f"\n📅 BY MONTH")
    running_total = 0
    for month, data in sorted(stats['by_month'].items()):
        running_total += data['pnl']
        print(f"  {month}  {data['trades']:3} trades  {data['wins']:3}W/{data['losses']:3}L  ${data['pnl']:>10,.2f}  (YTD: ${running_total:>12,.2f})")

    print(f"\n🏆 TOP 5 MOST PROFITABLE TICKERS")
    for item in stats['top_tickers_profit'][:5]:
        print(f"  {item['ticker']:8} {item['trades']:3} trades  {item['win_rate']:5.1f}% win  ${item['pnl']:>12,.2f}")

    print(f"\n❌ TOP 5 LOSING TICKERS")
    for item in stats['top_tickers_loss'][:5]:
        print(f"  {item['ticker']:8} {item['trades']:3} trades  {item['win_rate']:5.1f}% win  ${item['pnl']:>12,.2f}")

    print(f"\n⚠️  WASH SALES")
    print(f"  Count:    {stats['wash_sales']['count']}")
    print(f"  P&L:      ${stats['wash_sales']['total_pnl']:,.2f}")

    print(f"\n📊 SPREADS vs SINGLE LEGS")
    print(f"  Spreads:      {stats['spreads']['count']} trades, ${stats['spreads']['pnl']:,.2f}")
    print(f"  Single Legs:  {stats['single_legs']['count']} trades, ${stats['single_legs']['pnl']:,.2f}")

    # Write outputs
    print("\n" + "=" * 60)
    print("💾 SAVING FILES...")
    print("=" * 60)

    # CSV Journal
    csv_path = f"{output_dir}/trading_journal_validated_2025.csv"
    write_journal_csv(journal_entries, csv_path)
    print(f"✅ CSV:  {csv_path}")

    # JSON with full details
    json_path = f"{output_dir}/trading_journal_validated_2025.json"
    with open(json_path, 'w') as f:
        json.dump({
            'generated_at': datetime.now().isoformat(),
            'source_file': input_file,
            'statistics': stats,
            'entries': journal_entries
        }, f, indent=2)
    print(f"✅ JSON: {json_path}")

    # Summary stats
    summary_path = f"{output_dir}/trading_summary_validated_2025.json"
    with open(summary_path, 'w') as f:
        json.dump(stats, f, indent=2)
    print(f"✅ Summary: {summary_path}")

    print("\n✅ DONE!")
    return stats


if __name__ == "__main__":
    main()
