"""
Parse Fidelity monthly statements to extract options trading data for journal.
Handles naked puts/calls, credit spreads, and iron condors.
"""

import pdfplumber
import re
import os
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict
import json
import csv


@dataclass
class OptionTransaction:
    """Represents a single option transaction"""
    settlement_date: str
    trade_date: Optional[str]
    symbol: str  # Underlying symbol
    option_type: str  # PUT or CALL
    expiry: str  # Expiry date
    strike: float
    quantity: int  # Positive for buy, negative for sell
    price: float  # Per share (contract is 100 shares)
    transaction_type: str  # OPENING or CLOSING
    transaction_amount: float  # Total cash flow
    cost_basis: Optional[float] = None
    realized_gain: Optional[float] = None
    transaction_cost: float = 0.0
    cusip: str = ""
    option_symbol: str = ""  # Full option symbol like INTC250207P16.5
    month: str = ""  # Source month for tracking
    pending: bool = False  # If from "Trades Pending Settlement"


@dataclass
class Trade:
    """Represents a complete trade (matched open and close)"""
    ticker: str
    strategy: str  # naked_put, naked_call, credit_spread, iron_condor
    date_open: str
    date_close: str
    expiry: str
    strikes: list  # List of strikes involved
    lot_size: int  # Number of contracts
    premium_received: float  # Credit received
    premium_paid: float  # Debit paid (to close)
    cost_basis: float
    proceeds: float
    profit_loss: float
    transaction_costs: float
    legs: list = field(default_factory=list)  # List of option transactions
    notes: str = ""


def parse_option_symbol(text: str) -> dict:
    """Parse option details from text like 'PUT (INTC) INTEL CORP COM FEB 07 25 $16.5 (100 SHS)'"""
    result = {
        'option_type': None,
        'symbol': None,
        'company_name': None,
        'expiry': None,
        'strike': None,
        'option_symbol': None
    }

    # Extract option type
    if text.startswith('PUT') or 'PUT (' in text:
        result['option_type'] = 'PUT'
    elif text.startswith('CALL') or 'CALL (' in text:
        result['option_type'] = 'CALL'
    elif text.startswith('MPUT'):
        result['option_type'] = 'PUT'
    elif text.startswith('MCALL'):
        result['option_type'] = 'CALL'

    # Extract underlying symbol
    symbol_match = re.search(r'\(([A-Z]{1,5})\)', text)
    if symbol_match:
        result['symbol'] = symbol_match.group(1)

    # Extract expiry date - formats: "FEB 07 25", "JAN 17 25", etc.
    expiry_match = re.search(r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{1,2})\s+(\d{2})', text)
    if expiry_match:
        month_map = {'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04', 'MAY': '05', 'JUN': '06',
                     'JUL': '07', 'AUG': '08', 'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'}
        month = month_map.get(expiry_match.group(1), '01')
        day = expiry_match.group(2).zfill(2)
        year = '20' + expiry_match.group(3)
        result['expiry'] = f"{year}-{month}-{day}"

    # Extract strike price
    strike_match = re.search(r'\$(\d+(?:\.\d+)?)', text)
    if strike_match:
        result['strike'] = float(strike_match.group(1))

    # Extract option symbol like (INTC250207P16.5)
    opt_symbol_match = re.search(r'\(([A-Z]+\d+[PC][\d.]+)\)', text)
    if opt_symbol_match:
        result['option_symbol'] = opt_symbol_match.group(1)

    return result


def parse_transaction_line(lines: list, start_idx: int, month: str) -> tuple:
    """Parse a transaction starting at given index, return (transaction, next_index)"""

    # First line typically has: date, security name start
    # Need to combine multiple lines for full transaction

    combined_text = ""
    current_idx = start_idx

    # Collect lines until we hit the next transaction (starts with MM/DD pattern) or section header
    while current_idx < len(lines):
        line = lines[current_idx].strip()

        # Check if this is a new date (next transaction)
        if current_idx > start_idx and re.match(r'^\d{2}/\d{2}\s', line):
            break

        # Check for section headers
        if line.startswith('Total Securities') or line.startswith('Dividends') or line.startswith('Core Fund'):
            break

        combined_text += " " + line
        current_idx += 1

    combined_text = combined_text.strip()

    # Parse the combined text
    # Format: MM/DD OPTION_INFO CUSIP DESCRIPTION QTY PRICE COST_BASIS TRANS_COST AMOUNT

    # Extract settlement date
    date_match = re.match(r'^(\d{2}/\d{2})', combined_text)
    if not date_match:
        return None, current_idx

    settlement_date = date_match.group(1)
    # Convert to full date format
    year = month.split('-')[0] if '-' in month else '2025'
    settlement_date = f"{year}-{settlement_date.replace('/', '-')}"

    # Check if PUT or CALL
    if 'PUT' not in combined_text and 'CALL' not in combined_text:
        return None, current_idx  # Not an option transaction

    # Parse option details
    opt_details = parse_option_symbol(combined_text)

    if not opt_details['symbol']:
        return None, current_idx

    # Determine if OPENING or CLOSING
    is_opening = 'OPENING' in combined_text
    is_closing = 'CLOSING' in combined_text

    if not is_opening and not is_closing:
        return None, current_idx

    # Determine if buy or sell
    is_buy = 'You Bought' in combined_text
    is_sell = 'You Sold' in combined_text

    # Extract quantity - look for pattern like "180.000" or "-113.000"
    qty_match = re.search(r'(?:Bought|Sold)\s+(-?\d+\.?\d*)', combined_text)
    quantity = 0
    if qty_match:
        quantity = int(float(qty_match.group(1)))
        if is_sell:
            quantity = -abs(quantity)
        else:
            quantity = abs(quantity)

    # Extract price - appears after quantity
    price_pattern = r'(?:Bought|Sold)\s+[\d.]+\s+([\d.]+)'
    price_match = re.search(price_pattern, combined_text)
    price = 0.0
    if price_match:
        price = float(price_match.group(1))

    # Extract realized gain for closing transactions
    realized_gain = None
    gain_match = re.search(r'Short-term gain:\s*\$?([\d,]+\.?\d*)', combined_text)
    if gain_match:
        realized_gain = float(gain_match.group(1).replace(',', ''))

    # Extract transaction amount (last dollar amount usually)
    amounts = re.findall(r'-?\$?([\d,]+\.?\d+)$', combined_text)
    transaction_amount = 0.0
    if amounts:
        transaction_amount = float(amounts[-1].replace(',', ''))
        # Negative for buys, positive for sells
        if is_buy:
            transaction_amount = -abs(transaction_amount)
        else:
            transaction_amount = abs(transaction_amount)

    # Extract cost basis for closing transactions
    cost_basis = None
    cost_match = re.search(r'-?\$?([\d,]+\.?\d+)f', combined_text)
    if cost_match and is_closing:
        cost_basis = float(cost_match.group(1).replace(',', ''))

    txn = OptionTransaction(
        settlement_date=settlement_date,
        trade_date=None,
        symbol=opt_details['symbol'],
        option_type=opt_details['option_type'],
        expiry=opt_details['expiry'] or '',
        strike=opt_details['strike'] or 0.0,
        quantity=quantity,
        price=price,
        transaction_type='OPENING' if is_opening else 'CLOSING',
        transaction_amount=transaction_amount,
        cost_basis=cost_basis,
        realized_gain=realized_gain,
        option_symbol=opt_details['option_symbol'] or '',
        month=month
    )

    return txn, current_idx


def extract_transactions_from_text(text: str, month: str, pending: bool = False) -> list:
    """Extract all option transactions from page text"""
    transactions = []
    lines = text.split('\n')

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Look for transaction lines that start with date pattern
        if re.match(r'^\d{2}/\d{2}\s', line):
            # Check if this line contains option keywords
            combined = ' '.join(lines[i:min(i+5, len(lines))])
            if 'PUT' in combined or 'CALL' in combined:
                if 'You Bought' in combined or 'You Sold' in combined:
                    txn, next_i = parse_transaction_line(lines, i, month)
                    if txn:
                        txn.pending = pending
                        transactions.append(txn)
                    i = next_i
                    continue
        i += 1

    return transactions


def parse_statement(pdf_path: str, month: str) -> tuple:
    """Parse a single monthly statement and return transactions and summary"""

    all_transactions = []
    realized_gains = {'short_term': 0.0, 'long_term': 0.0}

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text()
            if not text:
                continue

            # Check if this is the Individual account section (X96-783860)
            if 'X96-783860' not in text and 'INDIVIDUAL' not in text:
                continue

            # Extract realized gains summary
            if 'Realized Gains and Losses' in text:
                # Format is: "Net Short-term Gain/Loss 8,453.40 159,140.71" (this period, YTD)
                st_match = re.search(r'Net Short-term Gain/Loss\s+([-\d,]+\.?\d*)', text)
                if st_match:
                    val = st_match.group(1).replace(',', '')
                    if val and val != '-':
                        realized_gains['short_term'] = float(val)
                lt_match = re.search(r'Net Long-term Gain/Loss\s+([-\d,]+\.?\d*)', text)
                if lt_match:
                    val = lt_match.group(1).replace(',', '')
                    if val and val != '-':
                        realized_gains['long_term'] = float(val)

            # Check for Securities Bought & Sold section
            if 'Securities Bought & Sold' in text:
                txns = extract_transactions_from_text(text, month, pending=False)
                all_transactions.extend(txns)

            # Check for Trades Pending Settlement
            if 'Trades Pending Settlement' in text:
                txns = extract_transactions_from_text(text, month, pending=True)
                all_transactions.extend(txns)

    return all_transactions, realized_gains


def parse_all_statements(directory: str) -> tuple:
    """Parse all statements in directory"""

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
    monthly_realized = {}

    for filename, month in statements:
        filepath = os.path.join(directory, filename)
        if os.path.exists(filepath):
            print(f"Processing {filename}...")
            txns, realized = parse_statement(filepath, month)
            all_transactions.extend(txns)
            monthly_realized[month] = realized
            print(f"  Found {len(txns)} transactions, Realized: ${realized['short_term']:,.2f}")

    return all_transactions, monthly_realized


def generate_option_key(txn: OptionTransaction) -> str:
    """Generate a unique key for matching option legs"""
    return f"{txn.symbol}_{txn.option_type}_{txn.expiry}_{txn.strike}"


def match_trades(transactions: list) -> list:
    """Match opening and closing transactions into complete trades"""

    # Group transactions by option key
    option_groups = defaultdict(list)
    for txn in transactions:
        key = generate_option_key(txn)
        option_groups[key].append(txn)

    trades = []
    unmatched_opens = []

    for key, txns in option_groups.items():
        # Separate opens and closes
        opens = [t for t in txns if t.transaction_type == 'OPENING']
        closes = [t for t in txns if t.transaction_type == 'CLOSING']

        # Sort by date
        opens.sort(key=lambda x: x.settlement_date)
        closes.sort(key=lambda x: x.settlement_date)

        # Match opens with closes using FIFO
        remaining_open_qty = []
        for o in opens:
            remaining_open_qty.append({
                'txn': o,
                'qty': abs(o.quantity),
                'matched': 0
            })

        for c in closes:
            close_qty = abs(c.quantity)
            matched_opens = []

            for open_entry in remaining_open_qty:
                if close_qty <= 0:
                    break
                if open_entry['qty'] - open_entry['matched'] <= 0:
                    continue

                available = open_entry['qty'] - open_entry['matched']
                to_match = min(available, close_qty)

                if to_match > 0:
                    matched_opens.append({
                        'open_txn': open_entry['txn'],
                        'qty_matched': to_match
                    })
                    open_entry['matched'] += to_match
                    close_qty -= to_match

            if matched_opens:
                # Calculate trade details
                open_txn = matched_opens[0]['open_txn']
                qty = sum(m['qty_matched'] for m in matched_opens)

                # Premium received = sum of opening sells
                # Premium paid = sum of closing buys
                is_short = open_txn.quantity < 0

                if is_short:
                    # Sold to open, bought to close (typical for selling premium)
                    premium_received = abs(sum(m['open_txn'].transaction_amount * (m['qty_matched'] / abs(m['open_txn'].quantity))
                                               for m in matched_opens))
                    premium_paid = abs(c.transaction_amount)
                else:
                    # Bought to open, sold to close (long option)
                    premium_received = abs(c.transaction_amount)
                    premium_paid = abs(sum(m['open_txn'].transaction_amount * (m['qty_matched'] / abs(m['open_txn'].quantity))
                                          for m in matched_opens))

                profit_loss = c.realized_gain if c.realized_gain else (premium_received - premium_paid)

                trade = Trade(
                    ticker=open_txn.symbol,
                    strategy=f"naked_{open_txn.option_type.lower()}",
                    date_open=open_txn.settlement_date,
                    date_close=c.settlement_date,
                    expiry=open_txn.expiry,
                    strikes=[open_txn.strike],
                    lot_size=qty,
                    premium_received=premium_received,
                    premium_paid=premium_paid,
                    cost_basis=c.cost_basis or premium_paid,
                    proceeds=premium_received,
                    profit_loss=profit_loss,
                    transaction_costs=0.0,  # TODO: Extract from transaction
                    legs=[open_txn, c]
                )
                trades.append(trade)

        # Track unmatched opens (still open positions)
        for open_entry in remaining_open_qty:
            if open_entry['qty'] - open_entry['matched'] > 0:
                unmatched_opens.append({
                    'txn': open_entry['txn'],
                    'remaining_qty': open_entry['qty'] - open_entry['matched']
                })

    return trades, unmatched_opens


def export_to_csv(trades: list, filepath: str):
    """Export trades to CSV for journal"""

    fieldnames = [
        'Date Open', 'Date Close', 'Ticker', 'Strategy', 'Expiry',
        'Strike(s)', 'Lot Size', 'Premium Received', 'Premium Paid',
        'Cost Basis', 'Proceeds', 'Profit/Loss', 'Transaction Costs', 'Notes'
    ]

    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for trade in sorted(trades, key=lambda x: x.date_open):
            writer.writerow({
                'Date Open': trade.date_open,
                'Date Close': trade.date_close,
                'Ticker': trade.ticker,
                'Strategy': trade.strategy,
                'Expiry': trade.expiry,
                'Strike(s)': '/'.join(str(s) for s in trade.strikes),
                'Lot Size': trade.lot_size,
                'Premium Received': f"${trade.premium_received:,.2f}",
                'Premium Paid': f"${trade.premium_paid:,.2f}",
                'Cost Basis': f"${trade.cost_basis:,.2f}",
                'Proceeds': f"${trade.proceeds:,.2f}",
                'Profit/Loss': f"${trade.profit_loss:,.2f}",
                'Transaction Costs': f"${trade.transaction_costs:,.2f}",
                'Notes': trade.notes
            })


def main():
    """Main entry point"""

    directory = "docs/2025 Trades"

    print("="*80)
    print("PARSING FIDELITY STATEMENTS - INDIVIDUAL ACCOUNT X96-783860")
    print("="*80)

    # Parse all statements
    transactions, monthly_realized = parse_all_statements(directory)

    print(f"\nTotal transactions extracted: {len(transactions)}")

    # Match trades
    trades, unmatched = match_trades(transactions)

    print(f"Matched trades: {len(trades)}")
    print(f"Unmatched open positions: {len(unmatched)}")

    # Calculate totals
    total_pl = sum(t.profit_loss for t in trades)
    monthly_statement_total = sum(m['short_term'] for m in monthly_realized.values())

    print(f"\n{'='*80}")
    print("MONTHLY RECONCILIATION")
    print("="*80)

    for month in sorted(monthly_realized.keys()):
        realized = monthly_realized[month]
        month_trades = [t for t in trades if t.date_close.startswith(month)]
        calc_pl = sum(t.profit_loss for t in month_trades)
        print(f"{month}: Statement=${realized['short_term']:>12,.2f}  Calculated=${calc_pl:>12,.2f}  Diff=${realized['short_term']-calc_pl:>10,.2f}")

    print(f"\n{'='*80}")
    print(f"TOTAL Statement P&L: ${monthly_statement_total:,.2f}")
    print(f"TOTAL Calculated P&L: ${total_pl:,.2f}")
    print(f"DIFFERENCE: ${monthly_statement_total - total_pl:,.2f}")
    print("="*80)

    # Export to CSV
    output_path = "docs/2025 Trades/trading_journal_2025.csv"
    export_to_csv(trades, output_path)
    print(f"\nJournal exported to: {output_path}")

    # Print unmatched positions
    if unmatched:
        print(f"\n{'='*80}")
        print("UNMATCHED OPEN POSITIONS (Still open at end of period)")
        print("="*80)
        for u in unmatched:
            t = u['txn']
            print(f"  {t.symbol} {t.option_type} {t.strike} exp:{t.expiry} qty:{u['remaining_qty']}")

    return transactions, trades, monthly_realized


if __name__ == "__main__":
    main()
