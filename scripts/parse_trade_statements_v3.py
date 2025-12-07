"""
Parse Fidelity monthly statements to extract trading data for journal.
V3: Extract all trades (options + stocks) with realized gains/losses.
Focus on accuracy by using the gain/loss data reported in statements.
"""

import pdfplumber
import re
import os
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from collections import defaultdict
import csv
import json


@dataclass
class Transaction:
    """Represents a single transaction (open or close)"""
    settlement_date: str
    security_type: str  # OPTION, STOCK, ETF
    symbol: str
    description: str  # Full description line
    action: str  # BOUGHT, SOLD
    transaction_type: str  # OPENING, CLOSING, ASSIGNED, etc.
    quantity: int
    price: float
    transaction_amount: float
    cost_basis: Optional[float] = None
    short_term_gain: Optional[float] = None
    short_term_loss: Optional[float] = None
    long_term_gain: Optional[float] = None
    long_term_loss: Optional[float] = None
    month: str = ""
    page_num: int = 0

    @property
    def net_gain_loss(self) -> float:
        """Calculate net gain/loss"""
        gain = (self.short_term_gain or 0) + (self.long_term_gain or 0)
        loss = (self.short_term_loss or 0) + (self.long_term_loss or 0)
        return gain - loss

    @property
    def is_closing(self) -> bool:
        return self.transaction_type == 'CLOSING' or self.net_gain_loss != 0


@dataclass
class TradeEntry:
    """A complete trade entry for the journal"""
    date_close: str
    date_open: str  # May be approximate or unknown
    symbol: str
    security_type: str
    description: str
    quantity: int
    cost_basis: float
    proceeds: float
    short_term_gain_loss: float
    long_term_gain_loss: float
    total_gain_loss: float
    holding_period: str  # SHORT or LONG
    notes: str = ""


def parse_date(date_str: str, year: str) -> str:
    """Convert MM/DD to YYYY-MM-DD"""
    if '/' in date_str:
        parts = date_str.split('/')
        return f"{year}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
    return date_str


def extract_option_details(text: str) -> dict:
    """Extract option details from description text"""
    result = {
        'option_type': None,
        'underlying': None,
        'strike': None,
        'expiry': None
    }

    # Option type
    if 'PUT (' in text or 'MPUT (' in text:
        result['option_type'] = 'PUT'
    elif 'CALL (' in text or 'MCALL (' in text:
        result['option_type'] = 'CALL'

    # Underlying symbol
    match = re.search(r'(?:PUT|CALL|MPUT|MCALL)\s*\(([A-Z]{1,5})\)', text)
    if match:
        result['underlying'] = match.group(1)

    # Strike
    match = re.search(r'\$(\d+(?:\.\d+)?)\s*\(100 SHS\)', text)
    if match:
        result['strike'] = float(match.group(1))

    # Expiry
    month_map = {'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04',
                 'MAY': '05', 'JUN': '06', 'JUL': '07', 'AUG': '08',
                 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'}
    match = re.search(r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{1,2})\s+(\d{2})', text)
    if match:
        result['expiry'] = f"20{match.group(3)}-{month_map[match.group(1)]}-{match.group(2).zfill(2)}"

    return result


def parse_transaction_block(text: str, settlement_date: str, year: str, month: str, page_num: int) -> Optional[Transaction]:
    """Parse a transaction block into a Transaction object"""

    # Determine if buy or sell
    is_buy = 'You Bought' in text
    is_sell = 'You Sold' in text

    if not is_buy and not is_sell:
        return None

    action = 'BOUGHT' if is_buy else 'SOLD'

    # Determine security type
    if 'PUT (' in text or 'CALL (' in text or 'MPUT (' in text or 'MCALL (' in text:
        security_type = 'OPTION'
    elif 'ETF' in text or 'SPDR' in text or 'TRUST' in text:
        security_type = 'ETF'
    elif 'COM' in text or 'INC' in text or 'CORP' in text:
        security_type = 'STOCK'
    else:
        security_type = 'OTHER'

    # Extract symbol
    symbol_match = re.search(r'(?:PUT|CALL|MPUT|MCALL)\s*\(([A-Z]{1,5})\)', text)
    if not symbol_match:
        # Try to get stock symbol from CUSIP area
        symbol_match = re.search(r'\b([A-Z]{1,5})\b\s+[A-Z0-9]{9}\s+You', text)
    symbol = symbol_match.group(1) if symbol_match else 'UNKNOWN'

    # Determine transaction type
    if 'OPENING' in text:
        txn_type = 'OPENING'
    elif 'CLOSING' in text:
        txn_type = 'CLOSING'
    elif 'ASSIGNED' in text:
        txn_type = 'ASSIGNED'
    else:
        txn_type = 'TRADE'

    # Extract quantity and price
    qty_price_pattern = r'You (?:Bought|Sold)\s+([-\d,.]+)\s+\$?([\d,.]+)'
    match = re.search(qty_price_pattern, text)
    if not match:
        return None

    quantity = int(float(match.group(1).replace(',', '')))
    price = float(match.group(2).replace(',', ''))

    # Adjust quantity sign
    if is_sell:
        quantity = -abs(quantity)

    # Extract transaction amount (last dollar value in the line typically)
    amounts = re.findall(r'(-?)\$?([\d,]+\.\d{2})(?:\s|$)', text)
    transaction_amount = 0.0
    if amounts:
        sign, amt = amounts[-1]
        transaction_amount = float(amt.replace(',', ''))
        if sign == '-':
            transaction_amount = -transaction_amount

    # Extract cost basis (marked with 'f')
    cost_basis = None
    cost_match = re.search(r'-?\$?([\d,]+\.?\d*)f', text)
    if cost_match:
        cost_basis = float(cost_match.group(1).replace(',', ''))

    # Extract gains/losses
    st_gain = None
    st_loss = None
    lt_gain = None
    lt_loss = None

    gain_match = re.search(r'Short-term gain:\s*\$?([\d,]+\.?\d*)', text)
    if gain_match:
        st_gain = float(gain_match.group(1).replace(',', ''))

    loss_match = re.search(r'Short-term loss:\s*\$?([\d,]+\.?\d*)', text)
    if loss_match:
        st_loss = float(loss_match.group(1).replace(',', ''))

    lt_gain_match = re.search(r'Long-term gain:\s*\$?([\d,]+\.?\d*)', text)
    if lt_gain_match:
        lt_gain = float(lt_gain_match.group(1).replace(',', ''))

    lt_loss_match = re.search(r'Long-term loss:\s*\$?([\d,]+\.?\d*)', text)
    if lt_loss_match:
        lt_loss = float(lt_loss_match.group(1).replace(',', ''))

    # Build description
    if security_type == 'OPTION':
        opt = extract_option_details(text)
        description = f"{opt['option_type']} {symbol} ${opt['strike']} exp:{opt['expiry']}"
    else:
        description = f"{security_type} {symbol}"

    return Transaction(
        settlement_date=parse_date(settlement_date, year),
        security_type=security_type,
        symbol=symbol,
        description=description,
        action=action,
        transaction_type=txn_type,
        quantity=quantity,
        price=price,
        transaction_amount=transaction_amount,
        cost_basis=cost_basis,
        short_term_gain=st_gain,
        short_term_loss=st_loss,
        long_term_gain=lt_gain,
        long_term_loss=lt_loss,
        month=month,
        page_num=page_num
    )


def parse_statement(pdf_path: str, month: str) -> Tuple[List[Transaction], Dict]:
    """Parse a single monthly statement"""

    transactions = []
    realized_gains = {'short_term': 0.0, 'long_term': 0.0, 'ytd_st': 0.0, 'ytd_lt': 0.0}
    year = month.split('-')[0]

    in_individual_account = False

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text()
            if not text:
                continue

            # Track account sections
            if 'X96-783860' in text:
                in_individual_account = True
            if 'Z32-346871' in text:
                in_individual_account = False

            if not in_individual_account:
                continue

            # Extract realized gains summary
            if 'Realized Gains and Losses' in text:
                st_match = re.search(r'Net Short-term Gain/Loss\s+([-\d,]+\.?\d*)\s+([-\d,]+\.?\d*)', text)
                if st_match:
                    try:
                        val = st_match.group(1).replace(',', '')
                        if val and val != '-':
                            realized_gains['short_term'] = float(val)
                        val = st_match.group(2).replace(',', '')
                        if val and val != '-':
                            realized_gains['ytd_st'] = float(val)
                    except:
                        pass

                lt_match = re.search(r'Net Long-term Gain/Loss\s+([-\d,]+\.?\d*)\s+([-\d,]+\.?\d*)', text)
                if lt_match:
                    try:
                        val = lt_match.group(1).replace(',', '')
                        if val and val != '-':
                            realized_gains['long_term'] = float(val)
                        val = lt_match.group(2).replace(',', '')
                        if val and val != '-':
                            realized_gains['ytd_lt'] = float(val)
                    except:
                        pass

            # Extract transactions
            if 'Securities Bought & Sold' in text or 'Trades Pending Settlement' in text:
                lines = text.split('\n')

                i = 0
                while i < len(lines):
                    line = lines[i].strip()

                    # Look for date pattern
                    date_match = re.match(r'^(\d{2}/\d{2})\s', line)
                    if date_match:
                        settlement_date = date_match.group(1)

                        # Collect lines for this transaction
                        txn_text = line
                        j = i + 1
                        while j < len(lines):
                            next_line = lines[j].strip()
                            # Stop at next transaction or section
                            if re.match(r'^\d{2}/\d{2}\s', next_line):
                                break
                            if 'Total Securities' in next_line or 'Dividends' in next_line:
                                break
                            if next_line.startswith('Core Fund'):
                                break
                            txn_text += ' ' + next_line
                            if 'TRANSACTION' in next_line:
                                j += 1
                                break
                            j += 1

                        # Parse the transaction
                        if 'You Bought' in txn_text or 'You Sold' in txn_text:
                            txn = parse_transaction_block(txn_text, settlement_date, year, month, page_num)
                            if txn:
                                transactions.append(txn)

                        i = j
                        continue

                    i += 1

    return transactions, realized_gains


def parse_all_statements(directory: str) -> Tuple[List[Transaction], Dict]:
    """Parse all statements"""

    statements = [
        ('Statement1312025.pdf', '2025-01'),
        ('Statement2282025.pdf', '2025-02'),
        ('Statement3312025.pdf', '2025-03'),
        ('Statement4302025.pdf', '2025-04'),
        ('Statement5312025.pdf', '2025-05'),
        ('Statement6302025.pdf', '2025-06'),
        ('Statement7312025.pdf', '2025-07'),
        ('Statement8312025.pdf', '2025-08'),
        ('Statement9302025.pdf', '2025-09'),
        ('Statement10312025.pdf', '2025-10'),
        ('Statement11302025.pdf', '2025-11'),
    ]

    all_transactions = []
    monthly_summary = {}

    for filename, month in statements:
        filepath = os.path.join(directory, filename)
        if os.path.exists(filepath):
            print(f"\nProcessing {filename}...")
            txns, realized = parse_statement(filepath, month)

            # Filter to only closing transactions (those with realized gains/losses)
            closing_txns = [t for t in txns if t.is_closing]

            all_transactions.extend(closing_txns)
            monthly_summary[month] = realized

            calc_st = sum((t.short_term_gain or 0) - (t.short_term_loss or 0) for t in closing_txns)
            calc_lt = sum((t.long_term_gain or 0) - (t.long_term_loss or 0) for t in closing_txns)

            print(f"  Closing transactions: {len(closing_txns)}")
            print(f"  Statement P&L: ST=${realized['short_term']:,.2f}, LT=${realized['long_term']:,.2f}")
            print(f"  Calculated:    ST=${calc_st:,.2f}, LT=${calc_lt:,.2f}")
            print(f"  YTD:           ST=${realized['ytd_st']:,.2f}, LT=${realized['ytd_lt']:,.2f}")

    return all_transactions, monthly_summary


def create_trade_journal(transactions: List[Transaction]) -> List[TradeEntry]:
    """Convert transactions to trade journal entries"""

    entries = []

    for txn in transactions:
        if not txn.is_closing:
            continue

        st_pl = (txn.short_term_gain or 0) - (txn.short_term_loss or 0)
        lt_pl = (txn.long_term_gain or 0) - (txn.long_term_loss or 0)
        total_pl = st_pl + lt_pl

        holding_period = 'LONG' if lt_pl != 0 else 'SHORT'

        entry = TradeEntry(
            date_close=txn.settlement_date,
            date_open='',  # Would need to track from opening transactions
            symbol=txn.symbol,
            security_type=txn.security_type,
            description=txn.description,
            quantity=abs(txn.quantity),
            cost_basis=txn.cost_basis or 0,
            proceeds=abs(txn.transaction_amount),
            short_term_gain_loss=st_pl,
            long_term_gain_loss=lt_pl,
            total_gain_loss=total_pl,
            holding_period=holding_period
        )
        entries.append(entry)

    return entries


def export_journal_csv(entries: List[TradeEntry], filepath: str):
    """Export journal to CSV"""

    fieldnames = [
        'Date Close', 'Symbol', 'Security Type', 'Description', 'Quantity',
        'Cost Basis', 'Proceeds', 'ST Gain/Loss', 'LT Gain/Loss', 'Total P&L',
        'Holding Period'
    ]

    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for e in sorted(entries, key=lambda x: x.date_close):
            writer.writerow({
                'Date Close': e.date_close,
                'Symbol': e.symbol,
                'Security Type': e.security_type,
                'Description': e.description,
                'Quantity': e.quantity,
                'Cost Basis': f"${e.cost_basis:,.2f}" if e.cost_basis else '',
                'Proceeds': f"${e.proceeds:,.2f}",
                'ST Gain/Loss': f"${e.short_term_gain_loss:,.2f}",
                'LT Gain/Loss': f"${e.long_term_gain_loss:,.2f}" if e.long_term_gain_loss else '',
                'Total P&L': f"${e.total_gain_loss:,.2f}",
                'Holding Period': e.holding_period
            })

    print(f"\nJournal exported to: {filepath}")


def print_summary(entries: List[TradeEntry], monthly_summary: Dict):
    """Print comprehensive summary"""

    print(f"\n{'='*90}")
    print("TRADING JOURNAL SUMMARY - Individual Account X96-783860 (Jan-Nov 2025)")
    print("="*90)

    # Overall stats
    total_st = sum(e.short_term_gain_loss for e in entries)
    total_lt = sum(e.long_term_gain_loss for e in entries)
    total_pl = total_st + total_lt

    winners = [e for e in entries if e.total_gain_loss > 0]
    losers = [e for e in entries if e.total_gain_loss < 0]

    print(f"\nTotal Closed Trades: {len(entries)}")
    print(f"Winners: {len(winners)} ({len(winners)/len(entries)*100:.1f}%)")
    print(f"Losers: {len(losers)} ({len(losers)/len(entries)*100:.1f}%)")
    print(f"\nTotal Short-term P&L: ${total_st:,.2f}")
    print(f"Total Long-term P&L:  ${total_lt:,.2f}")
    print(f"TOTAL P&L:            ${total_pl:,.2f}")

    # YTD from statements
    final_month = max(monthly_summary.keys())
    ytd_st = monthly_summary[final_month]['ytd_st']
    ytd_lt = monthly_summary[final_month]['ytd_lt']
    print(f"\nStatement YTD (Nov 2025):")
    print(f"  Short-term: ${ytd_st:,.2f}")
    print(f"  Long-term:  ${ytd_lt:,.2f}")
    print(f"  TOTAL:      ${ytd_st + ytd_lt:,.2f}")

    # By ticker
    print(f"\n{'='*90}")
    print("P&L BY TICKER")
    print("="*90)

    ticker_summary = defaultdict(lambda: {'count': 0, 'pl': 0.0, 'wins': 0, 'losses': 0})
    for e in entries:
        ticker_summary[e.symbol]['count'] += 1
        ticker_summary[e.symbol]['pl'] += e.total_gain_loss
        if e.total_gain_loss > 0:
            ticker_summary[e.symbol]['wins'] += 1
        elif e.total_gain_loss < 0:
            ticker_summary[e.symbol]['losses'] += 1

    sorted_tickers = sorted(ticker_summary.items(), key=lambda x: x[1]['pl'], reverse=True)

    print(f"{'Ticker':<8} {'Trades':>8} {'Win':>5} {'Loss':>5} {'Win%':>8} {'P&L':>14}")
    print("-" * 55)

    for ticker, data in sorted_tickers[:20]:
        win_rate = data['wins'] / data['count'] * 100 if data['count'] > 0 else 0
        print(f"{ticker:<8} {data['count']:>8} {data['wins']:>5} {data['losses']:>5} {win_rate:>7.1f}% ${data['pl']:>12,.2f}")

    if len(sorted_tickers) > 20:
        print(f"... and {len(sorted_tickers) - 20} more tickers")

    # Monthly summary
    print(f"\n{'='*90}")
    print("MONTHLY P&L")
    print("="*90)
    print(f"{'Month':<10} {'Statement ST':>14} {'Statement LT':>14} {'Statement Total':>16}")
    print("-" * 60)

    for month in sorted(monthly_summary.keys()):
        data = monthly_summary[month]
        total = data['short_term'] + data['long_term']
        print(f"{month:<10} ${data['short_term']:>12,.2f} ${data['long_term']:>12,.2f} ${total:>14,.2f}")

    # Grand totals
    total_stmt_st = sum(m['short_term'] for m in monthly_summary.values())
    total_stmt_lt = sum(m['long_term'] for m in monthly_summary.values())
    print("-" * 60)
    print(f"{'TOTAL':<10} ${total_stmt_st:>12,.2f} ${total_stmt_lt:>12,.2f} ${total_stmt_st + total_stmt_lt:>14,.2f}")


def main():
    directory = "docs/2025 Trades"

    print("="*90)
    print("FIDELITY STATEMENT PARSER V3 - COMPLETE TRADE JOURNAL")
    print("="*90)

    # Parse statements
    transactions, monthly_summary = parse_all_statements(directory)

    # Create journal entries
    entries = create_trade_journal(transactions)

    # Print summary
    print_summary(entries, monthly_summary)

    # Export CSV
    csv_path = os.path.join(directory, "trading_journal_2025_v3.csv")
    export_journal_csv(entries, csv_path)

    # Export detailed JSON
    json_path = os.path.join(directory, "trading_data_2025_v3.json")

    data = {
        'summary': {
            'total_trades': len(entries),
            'winners': len([e for e in entries if e.total_gain_loss > 0]),
            'losers': len([e for e in entries if e.total_gain_loss < 0]),
            'total_short_term_pl': sum(e.short_term_gain_loss for e in entries),
            'total_long_term_pl': sum(e.long_term_gain_loss for e in entries),
            'total_pl': sum(e.total_gain_loss for e in entries),
            'statement_ytd_st': monthly_summary[max(monthly_summary.keys())]['ytd_st'],
            'statement_ytd_lt': monthly_summary[max(monthly_summary.keys())]['ytd_lt']
        },
        'monthly': {
            month: {
                'statement_short_term': data['short_term'],
                'statement_long_term': data['long_term'],
                'ytd_short_term': data['ytd_st'],
                'ytd_long_term': data['ytd_lt']
            }
            for month, data in monthly_summary.items()
        },
        'trades': [
            {
                'date_close': e.date_close,
                'symbol': e.symbol,
                'security_type': e.security_type,
                'description': e.description,
                'quantity': e.quantity,
                'cost_basis': e.cost_basis,
                'proceeds': e.proceeds,
                'short_term_gain_loss': e.short_term_gain_loss,
                'long_term_gain_loss': e.long_term_gain_loss,
                'total_gain_loss': e.total_gain_loss
            }
            for e in sorted(entries, key=lambda x: x.date_close)
        ]
    }

    with open(json_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"Detailed data exported to: {json_path}")

    return entries, monthly_summary


if __name__ == "__main__":
    main()
