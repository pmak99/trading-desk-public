"""
Parse Fidelity monthly statements to extract options trading data for journal.
Handles naked puts/calls, credit spreads, and iron condors.
V2: More robust parsing using exact format matching.
"""

import pdfplumber
import re
import os
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from collections import defaultdict
import json
import csv


@dataclass
class OptionTransaction:
    """Represents a single option transaction"""
    settlement_date: str
    symbol: str  # Underlying symbol
    option_type: str  # PUT or CALL
    expiry: str  # Expiry date YYYY-MM-DD
    strike: float
    quantity: int  # Positive for buy, negative for sell
    price: float  # Per share (contract is 100 shares)
    action: str  # OPENING or CLOSING
    transaction_amount: float  # Total cash flow
    cost_basis: Optional[float] = None
    realized_gain: Optional[float] = None
    transaction_cost: float = 0.0
    cusip: str = ""
    option_symbol: str = ""
    month: str = ""
    raw_text: str = ""


@dataclass
class Trade:
    """Represents a complete trade (matched open and close)"""
    ticker: str
    strategy: str  # naked_put, naked_call, credit_spread, iron_condor, long_call, long_put
    date_open: str
    date_close: str
    expiry: str
    strikes: list
    lot_size: int
    premium_received: float
    premium_paid: float
    cost_basis: float
    proceeds: float
    profit_loss: float
    transaction_costs: float
    legs: list = field(default_factory=list)
    notes: str = ""


def parse_month_year(text: str, default_year: str = "2025") -> str:
    """Parse month abbreviation and day to YYYY-MM-DD format"""
    month_map = {
        'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04',
        'MAY': '05', 'JUN': '06', 'JUL': '07', 'AUG': '08',
        'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12'
    }

    match = re.search(r'(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s+(\d{1,2})\s+(\d{2})', text)
    if match:
        month = month_map.get(match.group(1), '01')
        day = match.group(2).zfill(2)
        year = '20' + match.group(3)
        return f"{year}-{month}-{day}"
    return ""


def extract_transactions_from_page(text: str, statement_month: str) -> List[OptionTransaction]:
    """Extract all option transactions from a page of text"""

    transactions = []

    # Extract year from statement month (format: 2025-01)
    year = statement_month.split('-')[0]

    # Pattern to match a complete transaction block
    # Format: MM/DD OPTION_TYPE (SYMBOL) COMPANY NAME CUSIP You Bought/Sold QTY PRICE COST_BASIS TRANS_COST AMOUNT
    #         EXPIRY_DATE (100 SHS) OPENING/CLOSING
    #         TRANSACTION Short-term gain: $XXX.XX

    # Split into lines and process
    lines = text.split('\n')

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Look for lines starting with date pattern MM/DD
        date_match = re.match(r'^(\d{2}/\d{2})\s+', line)
        if not date_match:
            i += 1
            continue

        settlement_date = date_match.group(1)

        # Check if this is an option transaction (PUT or CALL in the line or next few lines)
        combined_text = ' '.join(lines[i:min(i+4, len(lines))])

        if ('PUT (' not in combined_text and 'CALL (' not in combined_text and
            'MPUT (' not in combined_text and 'MCALL (' not in combined_text):
            i += 1
            continue

        if 'You Bought' not in combined_text and 'You Sold' not in combined_text:
            i += 1
            continue

        # This is an option transaction - extract details
        try:
            txn = parse_option_transaction(combined_text, settlement_date, year, statement_month)
            if txn:
                txn.raw_text = combined_text[:200]
                transactions.append(txn)
        except Exception as e:
            print(f"  Warning: Failed to parse transaction: {e}")
            print(f"    Text: {combined_text[:100]}")

        # Skip lines that were part of this transaction
        i += 1
        while i < len(lines):
            if re.match(r'^\d{2}/\d{2}\s', lines[i].strip()):
                break
            if 'TRANSACTION' in lines[i]:
                i += 1
                break
            i += 1

    return transactions


def parse_option_transaction(text: str, settlement_date: str, year: str, month: str) -> Optional[OptionTransaction]:
    """Parse a single option transaction from combined text"""

    # Determine option type
    if 'PUT (' in text or 'MPUT (' in text:
        option_type = 'PUT'
    elif 'CALL (' in text or 'MCALL (' in text:
        option_type = 'CALL'
    else:
        return None

    # Extract underlying symbol
    symbol_match = re.search(r'(?:PUT|CALL|MPUT|MCALL)\s*\(([A-Z]{1,5})\)', text)
    if not symbol_match:
        return None
    symbol = symbol_match.group(1)

    # Extract expiry date
    expiry = parse_month_year(text)
    if not expiry:
        return None

    # Extract strike price - look for pattern like "$16.5 (100 SHS)" or "$600 (100 SHS)"
    strike_match = re.search(r'\$(\d+(?:\.\d+)?)\s*\(100 SHS\)', text)
    if not strike_match:
        return None
    strike = float(strike_match.group(1))

    # Determine action (OPENING or CLOSING)
    action = 'CLOSING' if 'CLOSING' in text else 'OPENING'

    # Determine if buy or sell
    is_buy = 'You Bought' in text
    is_sell = 'You Sold' in text

    # Extract quantity and price
    # Pattern: "You Bought 8.000 $1.00000" or "You Sold -90.000 0.29000"
    if is_buy:
        qty_price_match = re.search(r'You Bought\s+([\d.]+)\s+\$?([\d.]+)', text)
    else:
        qty_price_match = re.search(r'You Sold\s+(-?[\d.]+)\s+([\d.]+)', text)

    if not qty_price_match:
        return None

    quantity = int(float(qty_price_match.group(1)))
    price = float(qty_price_match.group(2))

    # For sells, quantity is negative (contracts sold)
    if is_sell:
        quantity = -abs(quantity)

    # Extract transaction amount (last dollar amount in the line, after transaction cost)
    # Pattern: "-$0.99 -$74.99" where last is transaction amount
    amount_matches = re.findall(r'(-?\$?[\d,]+\.?\d*)\s*$', text.split('\n')[0] if '\n' in text else text)

    # Try to find the transaction amount - it's typically the last significant number
    transaction_amount = 0.0
    amount_pattern = re.findall(r'(-?)\$?([\d,]+\.\d{2})(?:\s|$)', text)
    if amount_pattern:
        # Get last meaningful amount (transaction amount)
        for sign, amt in reversed(amount_pattern):
            val = float(amt.replace(',', ''))
            if val > 0.5:  # Skip tiny transaction costs
                transaction_amount = -val if sign == '-' else val
                break

    # For buys, transaction amount should be negative (cash outflow)
    # For sells, transaction amount should be positive (cash inflow)
    if is_buy and transaction_amount > 0:
        transaction_amount = -abs(transaction_amount)
    elif is_sell and transaction_amount < 0:
        transaction_amount = abs(transaction_amount)

    # Extract cost basis for closing transactions
    cost_basis = None
    cost_match = re.search(r'-?\$?([\d,]+\.?\d+)f', text)
    if cost_match and action == 'CLOSING':
        cost_basis = float(cost_match.group(1).replace(',', ''))

    # Extract realized gain
    realized_gain = None
    gain_match = re.search(r'Short-term (?:gain|loss):\s*\$?([\d,]+\.?\d*)', text)
    if gain_match:
        realized_gain = float(gain_match.group(1).replace(',', ''))
        if 'loss' in text.lower() and 'Short-term loss' in text:
            realized_gain = -realized_gain

    # Also check for long-term gains
    lt_gain_match = re.search(r'Long-term (?:gain|loss):\s*\$?([\d,]+\.?\d*)', text)
    if lt_gain_match:
        lt_gain = float(lt_gain_match.group(1).replace(',', ''))
        if 'Long-term loss' in text:
            lt_gain = -lt_gain
        if realized_gain:
            realized_gain += lt_gain
        else:
            realized_gain = lt_gain

    # Extract CUSIP
    cusip_match = re.search(r'\b([A-Z0-9]{9})\b', text)
    cusip = cusip_match.group(1) if cusip_match else ""

    # Convert settlement date to full format
    full_date = f"{year}-{settlement_date.replace('/', '-')}"

    return OptionTransaction(
        settlement_date=full_date,
        symbol=symbol,
        option_type=option_type,
        expiry=expiry,
        strike=strike,
        quantity=quantity,
        price=price,
        action=action,
        transaction_amount=transaction_amount,
        cost_basis=cost_basis,
        realized_gain=realized_gain,
        cusip=cusip,
        month=month
    )


def parse_statement(pdf_path: str, month: str) -> Tuple[List[OptionTransaction], Dict]:
    """Parse a single monthly statement"""

    all_transactions = []
    realized_gains = {'short_term': 0.0, 'long_term': 0.0}
    in_individual_account = False

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            text = page.extract_text()
            if not text:
                continue

            # Track if we're in the individual account section
            if 'X96-783860' in text:
                in_individual_account = True

            # Check for start of next account section (stop parsing)
            if in_individual_account and 'Z32-346871' in text:
                in_individual_account = False

            if not in_individual_account:
                continue

            # Extract realized gains summary
            if 'Realized Gains and Losses' in text:
                st_match = re.search(r'Net Short-term Gain/Loss\s+([-\d,]+\.?\d*)', text)
                if st_match:
                    val = st_match.group(1).replace(',', '')
                    if val and val != '-' and val != '':
                        try:
                            realized_gains['short_term'] = float(val)
                        except ValueError:
                            pass

                lt_match = re.search(r'Net Long-term Gain/Loss\s+([-\d,]+\.?\d*)', text)
                if lt_match:
                    val = lt_match.group(1).replace(',', '')
                    if val and val != '-' and val != '':
                        try:
                            realized_gains['long_term'] = float(val)
                        except ValueError:
                            pass

            # Extract transactions from this page
            if 'Securities Bought & Sold' in text or 'Trades Pending Settlement' in text:
                txns = extract_transactions_from_page(text, month)
                all_transactions.extend(txns)

    return all_transactions, realized_gains


def parse_all_statements(directory: str) -> Tuple[List[OptionTransaction], Dict]:
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
            print(f"\nProcessing {filename}...")
            txns, realized = parse_statement(filepath, month)
            all_transactions.extend(txns)
            monthly_realized[month] = realized
            print(f"  Found {len(txns)} option transactions")
            print(f"  Statement Realized P&L: ST=${realized['short_term']:,.2f}, LT=${realized['long_term']:,.2f}")

    return all_transactions, monthly_realized


def generate_option_key(txn: OptionTransaction) -> str:
    """Generate a unique key for matching option legs"""
    return f"{txn.symbol}_{txn.option_type}_{txn.expiry}_{txn.strike}"


def match_trades(transactions: List[OptionTransaction]) -> Tuple[List[Trade], List[dict]]:
    """Match opening and closing transactions into complete trades using FIFO"""

    # Group transactions by option key
    option_groups = defaultdict(list)
    for txn in transactions:
        key = generate_option_key(txn)
        option_groups[key].append(txn)

    trades = []
    unmatched_opens = []

    for key, txns in option_groups.items():
        # Separate opens and closes
        opens = sorted([t for t in txns if t.action == 'OPENING'], key=lambda x: x.settlement_date)
        closes = sorted([t for t in txns if t.action == 'CLOSING'], key=lambda x: x.settlement_date)

        # Track remaining quantities for each open
        open_remaining = []
        for o in opens:
            open_remaining.append({
                'txn': o,
                'remaining_qty': abs(o.quantity),
                'price_per_contract': o.price * 100  # Price per share * 100 shares
            })

        # Match closes to opens using FIFO
        for close in closes:
            close_qty = abs(close.quantity)
            matched_opens = []
            total_open_premium = 0.0

            for entry in open_remaining:
                if close_qty <= 0:
                    break
                if entry['remaining_qty'] <= 0:
                    continue

                match_qty = min(entry['remaining_qty'], close_qty)
                proportion = match_qty / abs(entry['txn'].quantity)

                matched_opens.append({
                    'txn': entry['txn'],
                    'qty': match_qty,
                    'premium': abs(entry['txn'].transaction_amount) * proportion
                })

                total_open_premium += abs(entry['txn'].transaction_amount) * proportion
                entry['remaining_qty'] -= match_qty
                close_qty -= match_qty

            if matched_opens:
                first_open = matched_opens[0]['txn']
                total_qty = sum(m['qty'] for m in matched_opens)

                # Determine if this was selling or buying premium
                is_short_position = first_open.quantity < 0  # Sold to open

                if is_short_position:
                    # Sold to open, bought to close
                    premium_received = total_open_premium
                    premium_paid = abs(close.transaction_amount)
                    profit_loss = close.realized_gain if close.realized_gain is not None else (premium_received - premium_paid)
                    cost_basis = premium_paid
                    proceeds = premium_received
                    strategy = f"short_{first_open.option_type.lower()}"
                else:
                    # Bought to open, sold to close
                    premium_paid = total_open_premium
                    premium_received = abs(close.transaction_amount)
                    profit_loss = close.realized_gain if close.realized_gain is not None else (premium_received - premium_paid)
                    cost_basis = premium_paid
                    proceeds = premium_received
                    strategy = f"long_{first_open.option_type.lower()}"

                trade = Trade(
                    ticker=first_open.symbol,
                    strategy=strategy,
                    date_open=first_open.settlement_date,
                    date_close=close.settlement_date,
                    expiry=first_open.expiry,
                    strikes=[first_open.strike],
                    lot_size=total_qty,
                    premium_received=premium_received,
                    premium_paid=premium_paid,
                    cost_basis=cost_basis,
                    proceeds=proceeds,
                    profit_loss=profit_loss,
                    transaction_costs=0.0,
                    legs=[first_open, close]
                )
                trades.append(trade)

        # Track unmatched opens
        for entry in open_remaining:
            if entry['remaining_qty'] > 0:
                unmatched_opens.append({
                    'txn': entry['txn'],
                    'remaining_qty': entry['remaining_qty']
                })

    return trades, unmatched_opens


def identify_spreads(trades: List[Trade]) -> List[Trade]:
    """Identify multi-leg strategies (spreads, iron condors) from matched trades"""

    # Group trades by ticker, open date, and expiry
    trade_groups = defaultdict(list)
    for trade in trades:
        # Use a key that groups potential spread legs
        key = (trade.ticker, trade.date_open, trade.expiry)
        trade_groups[key].append(trade)

    enhanced_trades = []

    for key, group in trade_groups.items():
        if len(group) == 1:
            # Single leg trade
            enhanced_trades.append(group[0])
        elif len(group) == 2:
            # Could be a vertical spread
            legs = sorted(group, key=lambda x: x.strikes[0])

            if legs[0].strategy.startswith('short') and legs[1].strategy.startswith('long'):
                # Credit spread (sold lower, bought higher for puts; sold higher, bought lower for calls)
                combined = Trade(
                    ticker=legs[0].ticker,
                    strategy='credit_spread',
                    date_open=legs[0].date_open,
                    date_close=legs[0].date_close,
                    expiry=legs[0].expiry,
                    strikes=[legs[0].strikes[0], legs[1].strikes[0]],
                    lot_size=min(legs[0].lot_size, legs[1].lot_size),
                    premium_received=legs[0].premium_received,
                    premium_paid=legs[0].premium_paid + legs[1].premium_paid,
                    cost_basis=legs[0].cost_basis + legs[1].cost_basis,
                    proceeds=legs[0].proceeds + legs[1].proceeds,
                    profit_loss=legs[0].profit_loss + legs[1].profit_loss,
                    transaction_costs=legs[0].transaction_costs + legs[1].transaction_costs,
                    legs=legs[0].legs + legs[1].legs,
                    notes="Credit Spread"
                )
                enhanced_trades.append(combined)
            elif legs[0].strategy.startswith('long') and legs[1].strategy.startswith('short'):
                # Debit spread
                combined = Trade(
                    ticker=legs[0].ticker,
                    strategy='debit_spread',
                    date_open=legs[0].date_open,
                    date_close=legs[0].date_close,
                    expiry=legs[0].expiry,
                    strikes=[legs[0].strikes[0], legs[1].strikes[0]],
                    lot_size=min(legs[0].lot_size, legs[1].lot_size),
                    premium_received=legs[0].premium_received + legs[1].premium_received,
                    premium_paid=legs[0].premium_paid,
                    cost_basis=legs[0].cost_basis + legs[1].cost_basis,
                    proceeds=legs[0].proceeds + legs[1].proceeds,
                    profit_loss=legs[0].profit_loss + legs[1].profit_loss,
                    transaction_costs=legs[0].transaction_costs + legs[1].transaction_costs,
                    legs=legs[0].legs + legs[1].legs,
                    notes="Debit Spread"
                )
                enhanced_trades.append(combined)
            else:
                # Same direction legs - just list separately
                enhanced_trades.extend(group)
        elif len(group) == 4:
            # Could be an iron condor (2 puts + 2 calls at different strikes)
            puts = [t for t in group if 'put' in t.strategy]
            calls = [t for t in group if 'call' in t.strategy]

            if len(puts) == 2 and len(calls) == 2:
                combined = Trade(
                    ticker=group[0].ticker,
                    strategy='iron_condor',
                    date_open=group[0].date_open,
                    date_close=group[0].date_close,
                    expiry=group[0].expiry,
                    strikes=sorted([t.strikes[0] for t in group]),
                    lot_size=min(t.lot_size for t in group),
                    premium_received=sum(t.premium_received for t in group),
                    premium_paid=sum(t.premium_paid for t in group),
                    cost_basis=sum(t.cost_basis for t in group),
                    proceeds=sum(t.proceeds for t in group),
                    profit_loss=sum(t.profit_loss for t in group),
                    transaction_costs=sum(t.transaction_costs for t in group),
                    legs=[leg for t in group for leg in t.legs],
                    notes="Iron Condor"
                )
                enhanced_trades.append(combined)
            else:
                enhanced_trades.extend(group)
        else:
            # Multiple legs - keep as individual trades for now
            enhanced_trades.extend(group)

    return enhanced_trades


def export_to_csv(trades: List[Trade], filepath: str):
    """Export trades to CSV for trading journal"""

    fieldnames = [
        'Date Open', 'Date Close', 'Days Held', 'Ticker', 'Strategy', 'Expiry',
        'Strike(s)', 'Lot Size', 'Premium Received', 'Premium Paid',
        'Cost Basis', 'Proceeds', 'Profit/Loss', 'Return %', 'Notes'
    ]

    with open(filepath, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for trade in sorted(trades, key=lambda x: x.date_open):
            # Calculate days held
            try:
                open_date = datetime.strptime(trade.date_open, '%Y-%m-%d')
                close_date = datetime.strptime(trade.date_close, '%Y-%m-%d')
                days_held = (close_date - open_date).days
            except:
                days_held = 0

            # Calculate return percentage
            if trade.cost_basis > 0:
                return_pct = (trade.profit_loss / trade.cost_basis) * 100
            elif trade.premium_received > 0:
                return_pct = (trade.profit_loss / trade.premium_received) * 100
            else:
                return_pct = 0.0

            writer.writerow({
                'Date Open': trade.date_open,
                'Date Close': trade.date_close,
                'Days Held': days_held,
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
                'Return %': f"{return_pct:.1f}%",
                'Notes': trade.notes
            })

    print(f"\nJournal exported to: {filepath}")


def export_to_json(trades: List[Trade], transactions: List[OptionTransaction],
                   monthly_realized: Dict, filepath: str):
    """Export detailed data to JSON for analysis"""

    data = {
        'summary': {
            'total_trades': len(trades),
            'total_transactions': len(transactions),
            'total_realized_pl': sum(m['short_term'] + m['long_term'] for m in monthly_realized.values()),
            'calculated_pl': sum(t.profit_loss for t in trades),
        },
        'monthly_summary': {
            month: {
                'statement_short_term': realized['short_term'],
                'statement_long_term': realized['long_term'],
                'statement_total': realized['short_term'] + realized['long_term'],
                'calculated_pl': sum(t.profit_loss for t in trades if t.date_close.startswith(month))
            }
            for month, realized in monthly_realized.items()
        },
        'trades': [
            {
                'ticker': t.ticker,
                'strategy': t.strategy,
                'date_open': t.date_open,
                'date_close': t.date_close,
                'expiry': t.expiry,
                'strikes': t.strikes,
                'lot_size': t.lot_size,
                'premium_received': t.premium_received,
                'premium_paid': t.premium_paid,
                'profit_loss': t.profit_loss,
                'notes': t.notes
            }
            for t in sorted(trades, key=lambda x: x.date_open)
        ]
    }

    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"Detailed data exported to: {filepath}")


def print_monthly_reconciliation(trades: List[Trade], monthly_realized: Dict):
    """Print monthly reconciliation report"""

    print(f"\n{'='*90}")
    print("MONTHLY RECONCILIATION")
    print("="*90)
    print(f"{'Month':<10} {'Statement ST':>14} {'Statement LT':>14} {'Calculated':>14} {'Difference':>14}")
    print("-"*90)

    total_statement = 0.0
    total_calculated = 0.0

    for month in sorted(monthly_realized.keys()):
        realized = monthly_realized[month]
        month_trades = [t for t in trades if t.date_close.startswith(month)]
        calc_pl = sum(t.profit_loss for t in month_trades)
        statement_total = realized['short_term'] + realized['long_term']
        diff = statement_total - calc_pl

        total_statement += statement_total
        total_calculated += calc_pl

        print(f"{month:<10} ${realized['short_term']:>12,.2f} ${realized['long_term']:>12,.2f} ${calc_pl:>12,.2f} ${diff:>12,.2f}")

    print("-"*90)
    print(f"{'TOTAL':<10} ${total_statement:>26,.2f} ${total_calculated:>12,.2f} ${total_statement-total_calculated:>12,.2f}")
    print("="*90)


def main():
    """Main entry point"""

    directory = "docs/2025 Trades"

    print("="*90)
    print("FIDELITY STATEMENT PARSER - INDIVIDUAL ACCOUNT X96-783860")
    print("="*90)

    # Parse all statements
    transactions, monthly_realized = parse_all_statements(directory)

    print(f"\n{'='*90}")
    print(f"EXTRACTION SUMMARY")
    print("="*90)
    print(f"Total option transactions extracted: {len(transactions)}")

    # Match trades
    trades, unmatched = match_trades(transactions)

    print(f"Matched trades: {len(trades)}")
    print(f"Unmatched open positions: {len(unmatched)}")

    # Try to identify spreads
    # trades = identify_spreads(trades)  # Disabled for now to avoid complexity

    # Print monthly reconciliation
    print_monthly_reconciliation(trades, monthly_realized)

    # Print trade summary by ticker
    print(f"\n{'='*90}")
    print("TRADE SUMMARY BY TICKER")
    print("="*90)
    ticker_summary = defaultdict(lambda: {'count': 0, 'pl': 0.0})
    for t in trades:
        ticker_summary[t.ticker]['count'] += 1
        ticker_summary[t.ticker]['pl'] += t.profit_loss

    for ticker in sorted(ticker_summary.keys(), key=lambda x: ticker_summary[x]['pl'], reverse=True):
        data = ticker_summary[ticker]
        print(f"  {ticker:<8} {data['count']:>5} trades  P&L: ${data['pl']:>12,.2f}")

    # Export to CSV
    csv_path = os.path.join(directory, "trading_journal_2025.csv")
    export_to_csv(trades, csv_path)

    # Export to JSON
    json_path = os.path.join(directory, "trading_data_2025.json")
    export_to_json(trades, transactions, monthly_realized, json_path)

    # Print unmatched positions
    if unmatched:
        print(f"\n{'='*90}")
        print("OPEN POSITIONS (Not closed by end of November)")
        print("="*90)
        for u in sorted(unmatched, key=lambda x: x['txn'].symbol):
            t = u['txn']
            print(f"  {t.symbol:<6} {t.option_type:<4} ${t.strike:<8.2f} exp:{t.expiry}  qty:{u['remaining_qty']:>4}")

    return transactions, trades, monthly_realized


if __name__ == "__main__":
    main()
