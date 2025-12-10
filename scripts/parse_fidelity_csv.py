#!/usr/bin/env python3
"""
Parse Fidelity CSV exports (Realized Gains/Losses or Transaction History)
and correlate with VRP data from the ivcrush database.

Supports flexible column matching for different Fidelity export formats.
"""

import csv
import json
import sqlite3
import re
import os
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
from pathlib import Path


# Flexible column name mappings (Fidelity uses different names across exports)
COLUMN_MAPPINGS = {
    'symbol': ['Symbol', 'Symbol(CUSIP)', 'Security', 'Ticker', 'SYMBOL'],
    'description': ['Description', 'Security Description', 'DESCRIPTION'],
    'quantity': ['Quantity', 'Shares', 'Shares Sold', 'Qty', 'QUANTITY'],
    'acquired_date': ['Date Acquired', 'Acquired Date', 'Acquisition Date', 'Open Date', 'ACQUIRED DATE'],
    'sale_date': ['Date Sold', 'Sale Date', 'Close Date', 'Settlement Date', 'SALE DATE'],
    'cost_basis': ['Cost Basis', 'Cost', 'Adjusted Cost Basis', 'COST BASIS'],
    'proceeds': ['Proceeds', 'Sale Proceeds', 'Gross Proceeds', 'PROCEEDS'],
    'short_term_gl': ['Short Term Gain/Loss', 'Short-Term Gain/Loss', 'ST Gain/Loss'],
    'long_term_gl': ['Long Term Gain/Loss', 'Long-Term Gain/Loss', 'LT Gain/Loss'],
    'gain_loss': ['Gain/Loss', 'Realized Gain/Loss', 'Gain (Loss)', 'GAIN/LOSS', 'Realized G/L'],
    'term': ['Term', 'Holding Period', 'Short/Long', 'TERM'],
    'wash_sale': ['Wash Sale Disallowed', 'Disallowed Loss', 'Wash Sale Adjustment', 'WASH SALE'],
    'account': ['Account Number', 'Account', 'Acct', 'ACCOUNT'],
}


@dataclass
class Trade:
    """Represents a single closed trade"""
    symbol: str
    description: str
    quantity: int
    acquired_date: Optional[str]
    sale_date: str
    cost_basis: float
    proceeds: float
    gain_loss: float
    term: str  # SHORT or LONG
    wash_sale_amount: float = 0.0

    # Parsed option details
    option_type: Optional[str] = None  # PUT or CALL
    strike: Optional[float] = None
    expiration: Optional[str] = None
    underlying: Optional[str] = None

    # VRP correlation (populated later)
    earnings_date: Optional[str] = None
    vrp_ratio: Optional[float] = None
    implied_move: Optional[float] = None
    actual_move: Optional[float] = None
    beat_implied: Optional[bool] = None

    @property
    def is_option(self) -> bool:
        return self.option_type is not None

    @property
    def is_winner(self) -> bool:
        return self.gain_loss > 0

    @property
    def days_held(self) -> Optional[int]:
        if not self.acquired_date or not self.sale_date:
            return None
        try:
            acq = datetime.strptime(self.acquired_date, '%Y-%m-%d')
            sale = datetime.strptime(self.sale_date, '%Y-%m-%d')
            return (sale - acq).days
        except:
            return None


def find_column(headers: List[str], field_name: str) -> Optional[int]:
    """Find column index for a field using flexible matching"""
    possible_names = COLUMN_MAPPINGS.get(field_name, [field_name])

    for i, header in enumerate(headers):
        header_clean = header.strip()
        for name in possible_names:
            if header_clean.lower() == name.lower():
                return i
    return None


def parse_money(value: str) -> float:
    """Parse money string like '$1,234.56' or '(1,234.56)' to float"""
    if not value or value == '-' or value == '':
        return 0.0

    # Remove currency symbols and whitespace
    clean = value.strip().replace('$', '').replace(',', '')

    # Handle parentheses for negative
    if clean.startswith('(') and clean.endswith(')'):
        clean = '-' + clean[1:-1]

    try:
        return float(clean)
    except ValueError:
        return 0.0


def parse_date(value: str) -> Optional[str]:
    """Parse date string to YYYY-MM-DD format"""
    if not value or value == '-' or value == '':
        return None

    # Try common formats
    formats = [
        '%m/%d/%Y',
        '%Y-%m-%d',
        '%m-%d-%Y',
        '%m/%d/%y',
        '%Y/%m/%d',
    ]

    for fmt in formats:
        try:
            return datetime.strptime(value.strip(), fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue

    return None


def parse_option_description(description: str) -> Dict:
    """Extract option details from Fidelity description"""
    result = {
        'option_type': None,
        'underlying': None,
        'strike': None,
        'expiration': None,
    }

    if not description:
        return result

    desc_upper = description.upper()

    # Detect option type
    if 'PUT' in desc_upper:
        result['option_type'] = 'PUT'
    elif 'CALL' in desc_upper:
        result['option_type'] = 'CALL'
    else:
        return result  # Not an option

    # Extract underlying - usually in parentheses like "PUT (NVDA)"
    match = re.search(r'(?:PUT|CALL)\s*\(([A-Z]{1,5})\)', desc_upper)
    if match:
        result['underlying'] = match.group(1)
    else:
        # Try to find ticker at start
        match = re.search(r'^([A-Z]{1,5})\s', desc_upper)
        if match:
            result['underlying'] = match.group(1)

    # Extract strike price
    match = re.search(r'\$(\d+(?:\.\d+)?)', description)
    if match:
        result['strike'] = float(match.group(1))

    # Extract expiration date
    month_map = {
        'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04',
        'MAY': '05', 'JUN': '06', 'JUL': '07', 'AUG': '08',
        'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
    }

    # Try "JAN 17 25" or "JAN 17 2025" format
    match = re.search(r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{1,2})\s+(\d{2,4})', desc_upper)
    if match:
        month = month_map[match.group(1)]
        day = match.group(2).zfill(2)
        year = match.group(3)
        if len(year) == 2:
            year = '20' + year
        result['expiration'] = f"{year}-{month}-{day}"

    return result


def parse_fidelity_csv(filepath: str) -> List[Trade]:
    """Parse Fidelity CSV export into Trade objects"""
    trades = []

    with open(filepath, 'r', encoding='utf-8-sig') as f:
        # Skip any header lines that aren't the column headers
        lines = f.readlines()

    # Find the header row (first row with recognizable column names)
    header_idx = 0
    for i, line in enumerate(lines):
        if any(col.lower() in line.lower() for col in ['symbol', 'description', 'proceeds']):
            header_idx = i
            break

    # Parse as CSV starting from header
    reader = csv.reader(lines[header_idx:])
    headers = next(reader)

    # Find column indices
    col_idx = {}
    for field in COLUMN_MAPPINGS.keys():
        idx = find_column(headers, field)
        col_idx[field] = idx

    print(f"      Column mapping: {[(k, v) for k, v in col_idx.items() if v is not None]}")

    # Parse rows
    for row in reader:
        if not row or len(row) < 3:
            continue

        # Skip summary/total rows and wash sale adjustment rows
        first_col = row[0].strip() if row else ''
        if not first_col or first_col == '':
            continue
        if any(skip in str(row).lower() for skip in ['total', 'subtotal', 'disclaimer', 'wash sale']):
            continue

        def get_val(field: str) -> str:
            idx = col_idx.get(field)
            if idx is not None and idx < len(row):
                return row[idx].strip()
            return ''

        symbol = get_val('symbol')
        if not symbol:
            continue

        description = get_val('description')

        # Parse option details from description
        opt_details = parse_option_description(description)

        # For options, use underlying as symbol (extract from CUSIP format like "ACN250926P215(8061839XV)")
        if opt_details['underlying']:
            symbol = opt_details['underlying']
        else:
            # Try to extract from symbol field format like "ACN250926P215(cusip)"
            match = re.match(r'^([A-Z]{1,5})\d{6}[PC]', symbol)
            if match:
                symbol = match.group(1)
                # Also try to get option type from symbol
                if 'P' in symbol or re.search(r'\d{6}P', get_val('symbol')):
                    opt_details['option_type'] = 'PUT'
                elif 'C' in symbol or re.search(r'\d{6}C', get_val('symbol')):
                    opt_details['option_type'] = 'CALL'

        # Parse quantity
        qty_str = get_val('quantity')
        try:
            quantity = abs(int(float(qty_str.replace(',', '')))) if qty_str else 0
        except:
            quantity = 0

        # Parse dates
        acquired_date = parse_date(get_val('acquired_date'))
        sale_date = parse_date(get_val('sale_date'))

        if not sale_date:
            continue  # Need at least a sale date

        # Parse money fields
        cost_basis = parse_money(get_val('cost_basis'))
        proceeds = parse_money(get_val('proceeds'))

        # Try short/long term specific columns first, then fall back to combined
        short_term_gl = parse_money(get_val('short_term_gl'))
        long_term_gl = parse_money(get_val('long_term_gl'))

        if short_term_gl != 0 or long_term_gl != 0:
            gain_loss = short_term_gl + long_term_gl
            term = 'LONG' if long_term_gl != 0 else 'SHORT'
        else:
            gain_loss = parse_money(get_val('gain_loss'))
            # Determine term from column or holding period
            term_str = get_val('term').upper()
            if 'LONG' in term_str:
                term = 'LONG'
            else:
                term = 'SHORT'

        wash_sale = parse_money(get_val('wash_sale'))

        trade = Trade(
            symbol=symbol,
            description=description,
            quantity=quantity,
            acquired_date=acquired_date,
            sale_date=sale_date,
            cost_basis=cost_basis,
            proceeds=proceeds,
            gain_loss=gain_loss,
            term=term,
            wash_sale_amount=wash_sale,
            option_type=opt_details['option_type'],
            strike=opt_details['strike'],
            expiration=opt_details['expiration'],
            underlying=opt_details['underlying'],
        )
        trades.append(trade)

    return trades


def load_historical_moves(db_path: str) -> Dict[str, List[Dict]]:
    """Load historical moves from ivcrush database, grouped by ticker"""
    moves_by_ticker = defaultdict(list)

    if not os.path.exists(db_path):
        print(f"Warning: Database not found at {db_path}")
        return moves_by_ticker

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT ticker, earnings_date, gap_move_pct, intraday_move_pct
        FROM historical_moves
        WHERE earnings_date >= '2024-01-01'
        ORDER BY ticker, earnings_date
    """)

    for row in cursor.fetchall():
        ticker, earnings_date, gap_move, intraday_move = row
        moves_by_ticker[ticker].append({
            'earnings_date': earnings_date,
            'gap_move_pct': gap_move,
            'intraday_move_pct': intraday_move,
            'actual_move': max(abs(gap_move), abs(intraday_move)),
        })

    conn.close()
    return moves_by_ticker


def find_nearest_earnings(trade: Trade, moves_by_ticker: Dict) -> Optional[Dict]:
    """Find the earnings event closest to the trade's sale date"""
    ticker = trade.symbol
    if ticker not in moves_by_ticker:
        return None

    sale_date = datetime.strptime(trade.sale_date, '%Y-%m-%d')

    # Look for earnings within 7 days of sale
    best_match = None
    min_diff = 8  # days

    for move in moves_by_ticker[ticker]:
        earnings = datetime.strptime(move['earnings_date'], '%Y-%m-%d')
        diff = abs((sale_date - earnings).days)

        # Trade should close on or shortly after earnings
        if diff < min_diff and (sale_date >= earnings):
            min_diff = diff
            best_match = move

    return best_match


def correlate_with_vrp(trades: List[Trade], db_path: str) -> List[Trade]:
    """Add VRP correlation data to trades"""
    moves_by_ticker = load_historical_moves(db_path)

    for trade in trades:
        if not trade.is_option:
            continue

        match = find_nearest_earnings(trade, moves_by_ticker)
        if match:
            trade.earnings_date = match['earnings_date']
            trade.actual_move = match['actual_move']

            # Calculate if trade beat the implied move
            # (Would need implied move data from sentiment_history or scan logs)

    return trades


def calculate_statistics(trades: List[Trade]) -> Dict:
    """Calculate comprehensive trading statistics"""

    total = len(trades)
    if total == 0:
        return {}

    winners = [t for t in trades if t.is_winner]
    losers = [t for t in trades if not t.is_winner]
    options = [t for t in trades if t.is_option]
    stocks = [t for t in trades if not t.is_option]

    total_pnl = sum(t.gain_loss for t in trades)
    winner_pnl = sum(t.gain_loss for t in winners)
    loser_pnl = sum(t.gain_loss for t in losers)

    # By ticker
    by_ticker = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0})
    for t in trades:
        by_ticker[t.symbol]['count'] += 1
        by_ticker[t.symbol]['pnl'] += t.gain_loss
        if t.is_winner:
            by_ticker[t.symbol]['wins'] += 1

    # By month
    by_month = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0})
    for t in trades:
        month = t.sale_date[:7]
        by_month[month]['count'] += 1
        by_month[month]['pnl'] += t.gain_loss
        if t.is_winner:
            by_month[month]['wins'] += 1

    # By option type
    by_option_type = defaultdict(lambda: {'count': 0, 'pnl': 0, 'wins': 0})
    for t in options:
        otype = t.option_type or 'UNKNOWN'
        by_option_type[otype]['count'] += 1
        by_option_type[otype]['pnl'] += t.gain_loss
        if t.is_winner:
            by_option_type[otype]['wins'] += 1

    # Earnings correlation stats
    earnings_trades = [t for t in trades if t.earnings_date]

    return {
        'total_trades': total,
        'winners': len(winners),
        'losers': len(losers),
        'win_rate': round(100 * len(winners) / total, 1),
        'total_pnl': round(total_pnl, 2),
        'winner_pnl': round(winner_pnl, 2),
        'loser_pnl': round(loser_pnl, 2),
        'avg_win': round(winner_pnl / len(winners), 2) if winners else 0,
        'avg_loss': round(loser_pnl / len(losers), 2) if losers else 0,
        'profit_factor': round(abs(winner_pnl / loser_pnl), 2) if loser_pnl else 0,
        'options_count': len(options),
        'stocks_count': len(stocks),
        'by_ticker': {k: {'count': v['count'], 'pnl': round(v['pnl'], 2),
                         'win_rate': round(100 * v['wins'] / v['count'], 1)}
                     for k, v in sorted(by_ticker.items(), key=lambda x: x[1]['pnl'], reverse=True)},
        'by_month': {k: {'count': v['count'], 'pnl': round(v['pnl'], 2),
                        'win_rate': round(100 * v['wins'] / v['count'], 1)}
                    for k, v in sorted(by_month.items())},
        'by_option_type': dict(by_option_type),
        'earnings_correlated': len(earnings_trades),
        'wash_sales': {
            'count': len([t for t in trades if t.wash_sale_amount != 0]),
            'total': round(sum(t.wash_sale_amount for t in trades), 2),
        }
    }


def export_journal_csv(trades: List[Trade], filepath: str):
    """Export trades to enhanced CSV format"""

    fieldnames = [
        'Sale Date', 'Acquired Date', 'Days Held', 'Symbol', 'Type',
        'Option Type', 'Strike', 'Expiration', 'Quantity',
        'Cost Basis', 'Proceeds', 'Gain/Loss', 'Winner', 'Term',
        'Wash Sale', 'Earnings Date', 'Actual Move %'
    ]

    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for t in sorted(trades, key=lambda x: x.sale_date):
            writer.writerow({
                'Sale Date': t.sale_date,
                'Acquired Date': t.acquired_date or '',
                'Days Held': t.days_held or '',
                'Symbol': t.symbol,
                'Type': 'OPTION' if t.is_option else 'STOCK',
                'Option Type': t.option_type or '',
                'Strike': t.strike or '',
                'Expiration': t.expiration or '',
                'Quantity': t.quantity,
                'Cost Basis': f"${t.cost_basis:,.2f}",
                'Proceeds': f"${t.proceeds:,.2f}",
                'Gain/Loss': f"${t.gain_loss:,.2f}",
                'Winner': 'YES' if t.is_winner else 'NO',
                'Term': t.term,
                'Wash Sale': f"${t.wash_sale_amount:,.2f}" if t.wash_sale_amount else '',
                'Earnings Date': t.earnings_date or '',
                'Actual Move %': f"{t.actual_move:.1f}%" if t.actual_move else '',
            })


def print_summary(stats: Dict):
    """Print formatted summary"""

    print("\n" + "=" * 70)
    print("TRADING JOURNAL SUMMARY")
    print("=" * 70)

    print(f"\n📊 OVERALL PERFORMANCE")
    print(f"   Total Trades:    {stats['total_trades']}")
    print(f"   Win Rate:        {stats['win_rate']}%")
    print(f"   Winners:         {stats['winners']}")
    print(f"   Losers:          {stats['losers']}")

    print(f"\n💰 PROFIT & LOSS")
    print(f"   Total P&L:       ${stats['total_pnl']:,.2f}")
    print(f"   From Winners:    ${stats['winner_pnl']:,.2f}")
    print(f"   From Losers:     ${stats['loser_pnl']:,.2f}")
    print(f"   Avg Win:         ${stats['avg_win']:,.2f}")
    print(f"   Avg Loss:        ${stats['avg_loss']:,.2f}")
    print(f"   Profit Factor:   {stats['profit_factor']}")

    print(f"\n📈 BY INSTRUMENT")
    print(f"   Options:         {stats['options_count']}")
    print(f"   Stocks:          {stats['stocks_count']}")

    if stats['by_option_type']:
        print(f"\n📋 BY OPTION TYPE")
        for otype, data in stats['by_option_type'].items():
            win_rate = 100 * data['wins'] / data['count'] if data['count'] else 0
            print(f"   {otype:8} {data['count']:4} trades  {win_rate:5.1f}% win  ${data['pnl']:>12,.2f}")

    print(f"\n🏆 TOP 5 TICKERS BY P&L")
    for i, (ticker, data) in enumerate(list(stats['by_ticker'].items())[:5]):
        print(f"   {ticker:8} {data['count']:3} trades  {data['win_rate']:5.1f}% win  ${data['pnl']:>12,.2f}")

    print(f"\n📅 MONTHLY P&L")
    ytd = 0
    for month, data in stats['by_month'].items():
        ytd += data['pnl']
        print(f"   {month}  {data['count']:3} trades  {data['win_rate']:5.1f}% win  ${data['pnl']:>10,.2f}  (YTD: ${ytd:>12,.2f})")

    if stats['earnings_correlated'] > 0:
        print(f"\n🎯 EARNINGS CORRELATION")
        print(f"   Trades matched to earnings: {stats['earnings_correlated']}")

    if stats['wash_sales']['count'] > 0:
        print(f"\n⚠️  WASH SALES")
        print(f"   Count:           {stats['wash_sales']['count']}")
        print(f"   Disallowed:      ${stats['wash_sales']['total']:,.2f}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Parse Fidelity CSV exports')
    parser.add_argument('csv_file', nargs='?', help='Path to Fidelity CSV export')
    parser.add_argument('--db', default='$PROJECT_ROOT/2.0/data/ivcrush.db',
                       help='Path to ivcrush database for VRP correlation')
    parser.add_argument('--output', default='$PROJECT_ROOT/docs/2025 Trades',
                       help='Output directory')

    args = parser.parse_args()

    # Find CSV file
    if args.csv_file:
        csv_path = args.csv_file
    else:
        # Look for CSV in common locations
        search_paths = [
            '/Users/prashant/Downloads',
            '/Users/prashant/Desktop',
            args.output,
        ]
        csv_path = None
        for search_dir in search_paths:
            if os.path.exists(search_dir):
                for f in os.listdir(search_dir):
                    if f.lower().endswith('.csv') and 'fidelity' in f.lower():
                        csv_path = os.path.join(search_dir, f)
                        break
                    if f.lower().endswith('.csv') and 'gain' in f.lower():
                        csv_path = os.path.join(search_dir, f)
                        break

    if not csv_path or not os.path.exists(csv_path):
        print("❌ No Fidelity CSV file found.")
        print("\nTo use this parser:")
        print("1. Log into Fidelity.com")
        print("2. Go to Accounts & Trade → Tax Information")
        print("3. Select 'Realized Gain/Loss' for your account")
        print("4. Click 'Download' or 'CSV' to export")
        print("5. Run: python parse_fidelity_csv.py /path/to/downloaded.csv")
        return

    print("=" * 70)
    print("FIDELITY CSV JOURNAL PARSER")
    print("=" * 70)
    print(f"\n📄 Input:  {csv_path}")

    # Parse CSV
    print("\n[1/4] Parsing Fidelity CSV...")
    trades = parse_fidelity_csv(csv_path)
    print(f"      Found {len(trades)} trades")

    # Correlate with VRP
    print("\n[2/4] Correlating with earnings data...")
    trades = correlate_with_vrp(trades, args.db)
    correlated = len([t for t in trades if t.earnings_date])
    print(f"      Matched {correlated} trades to earnings events")

    # Calculate stats
    print("\n[3/4] Calculating statistics...")
    stats = calculate_statistics(trades)

    # Print summary
    print_summary(stats)

    # Export files
    print("\n[4/4] Exporting files...")

    os.makedirs(args.output, exist_ok=True)

    csv_out = os.path.join(args.output, 'trading_journal_enhanced.csv')
    export_journal_csv(trades, csv_out)
    print(f"      CSV:  {csv_out}")

    json_out = os.path.join(args.output, 'trading_journal_enhanced.json')
    with open(json_out, 'w') as f:
        json.dump({
            'generated_at': datetime.now().isoformat(),
            'source_file': csv_path,
            'statistics': stats,
            'trades': [
                {
                    'sale_date': t.sale_date,
                    'acquired_date': t.acquired_date,
                    'days_held': t.days_held,
                    'symbol': t.symbol,
                    'is_option': t.is_option,
                    'option_type': t.option_type,
                    'strike': t.strike,
                    'expiration': t.expiration,
                    'quantity': t.quantity,
                    'cost_basis': t.cost_basis,
                    'proceeds': t.proceeds,
                    'gain_loss': t.gain_loss,
                    'is_winner': t.is_winner,
                    'term': t.term,
                    'wash_sale_amount': t.wash_sale_amount,
                    'earnings_date': t.earnings_date,
                    'actual_move': t.actual_move,
                }
                for t in sorted(trades, key=lambda x: x.sale_date)
            ]
        }, f, indent=2)
    print(f"      JSON: {json_out}")

    print("\n✅ Done!")


if __name__ == "__main__":
    main()
