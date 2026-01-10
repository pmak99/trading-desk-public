#!/usr/bin/env python3
"""
Sync December 2025 trades from PDF-parsed CSV to database.
Estimates acquisition dates for earnings plays (typically 1-2 day holds).
"""
import csv
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# Paths
PROJECT_ROOT = Path(__file__).parent.parent
CSV_PATH = PROJECT_ROOT / "docs/2025 Trades/trading_journal_2025_v3.csv"
DB_PATH = PROJECT_ROOT / "2.0/data/ivcrush.db"

def parse_option_description(desc: str) -> dict:
    """Parse option description like 'PUT CRDO $145.0 exp:2025-12-05'"""
    result = {'option_type': None, 'strike': None, 'expiration': None}

    # Option type
    if 'PUT' in desc.upper():
        result['option_type'] = 'PUT'
    elif 'CALL' in desc.upper():
        result['option_type'] = 'CALL'

    # Strike price
    strike_match = re.search(r'\$(\d+\.?\d*)', desc)
    if strike_match:
        result['strike'] = float(strike_match.group(1))

    # Expiration
    exp_match = re.search(r'exp:(\d{4}-\d{2}-\d{2})', desc)
    if exp_match:
        result['expiration'] = exp_match.group(1)

    return result

def parse_currency(value: str) -> float:
    """Parse currency string like '$1,234.56' or '-$500.00'"""
    if not value or value == '':
        return 0.0
    clean = value.replace('$', '').replace(',', '').replace('"', '')
    try:
        return float(clean)
    except ValueError:
        return 0.0

def estimate_acquired_date(sale_date: str, holding_period: str) -> str:
    """Estimate acquisition date based on sale date and holding period."""
    sale_dt = datetime.strptime(sale_date, '%Y-%m-%d')

    # Default 1-day hold for earnings plays
    days_back = 1

    if holding_period == 'LONG':
        days_back = 366  # Over a year

    acquired_dt = sale_dt - timedelta(days=days_back)
    return acquired_dt.strftime('%Y-%m-%d')

def main():
    print(f"Reading CSV: {CSV_PATH}")
    print(f"Database: {DB_PATH}")

    # Check file exists
    if not CSV_PATH.exists():
        print(f"Error: CSV file not found: {CSV_PATH}")
        return

    if not DB_PATH.exists():
        print(f"Error: Database not found: {DB_PATH}")
        return

    # Read existing December trades from DB
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT symbol, sale_date, option_type, strike, cost_basis
        FROM trade_journal
        WHERE sale_date >= '2025-12-01' AND sale_date <= '2025-12-31'
    """)
    existing = set()
    for row in cursor.fetchall():
        # Create a key for matching
        key = (row[0], row[1], row[2], row[3], round(row[4], 2))
        existing.add(key)

    print(f"Existing December trades in DB: {len(existing)}")

    # Read CSV and find new December trades
    new_trades = []
    with open(CSV_PATH, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            sale_date = row['Date Close']
            if not sale_date.startswith('2025-12'):
                continue

            symbol = row['Symbol']
            desc = row['Description']
            option_info = parse_option_description(desc)
            cost_basis = parse_currency(row['Cost Basis'])

            # Create matching key
            key = (symbol, sale_date, option_info['option_type'],
                   option_info['strike'], round(cost_basis, 2))

            if key in existing:
                continue

            # Parse other fields
            proceeds = parse_currency(row['Proceeds'])
            st_gain = parse_currency(row.get('ST Gain/Loss', ''))
            lt_gain = parse_currency(row.get('LT Gain/Loss', ''))
            total_pnl = st_gain + lt_gain if st_gain or lt_gain else parse_currency(row.get('Total P&L', ''))

            holding_period = row.get('Holding Period', 'SHORT')
            acquired_date = estimate_acquired_date(sale_date, holding_period)

            # Calculate days held
            try:
                sale_dt = datetime.strptime(sale_date, '%Y-%m-%d')
                acq_dt = datetime.strptime(acquired_date, '%Y-%m-%d')
                days_held = (sale_dt - acq_dt).days
            except (ValueError, TypeError):
                days_held = 1

            quantity = int(row.get('Quantity', '1').replace(',', ''))
            is_winner = 1 if total_pnl > 0 else 0
            term = 'LONG' if holding_period == 'LONG' else 'SHORT'

            new_trades.append({
                'symbol': symbol,
                'acquired_date': acquired_date,
                'sale_date': sale_date,
                'days_held': days_held,
                'option_type': option_info['option_type'],
                'strike': option_info['strike'],
                'expiration': option_info['expiration'],
                'quantity': quantity,
                'cost_basis': cost_basis,
                'proceeds': proceeds,
                'gain_loss': total_pnl,
                'is_winner': is_winner,
                'term': term,
                'wash_sale_amount': 0
            })

    print(f"New December trades to insert: {len(new_trades)}")

    if not new_trades:
        print("No new trades to insert.")
        conn.close()
        return

    # Show what we're about to insert
    print("\nTrades to insert:")
    total_pnl = 0
    for t in new_trades:
        print(f"  {t['sale_date']} {t['symbol']:6} {t['option_type'] or 'STOCK':4} ${t['strike'] or 0:>7.1f} P&L: ${t['gain_loss']:>10,.2f}")
        total_pnl += t['gain_loss']
    print(f"\nTotal P&L of new trades: ${total_pnl:,.2f}")

    # Insert trades
    insert_sql = """
        INSERT INTO trade_journal
        (symbol, acquired_date, sale_date, days_held, option_type, strike,
         expiration, quantity, cost_basis, proceeds, gain_loss, is_winner,
         term, wash_sale_amount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    inserted = 0
    try:
        for t in new_trades:
            try:
                cursor.execute(insert_sql, (
                    t['symbol'], t['acquired_date'], t['sale_date'], t['days_held'],
                    t['option_type'], t['strike'], t['expiration'], t['quantity'],
                    t['cost_basis'], t['proceeds'], t['gain_loss'], t['is_winner'],
                    t['term'], t['wash_sale_amount']
                ))
                inserted += 1
            except sqlite3.IntegrityError as e:
                print(f"  Skip duplicate: {t['symbol']} {t['sale_date']} - {e}")

        conn.commit()
        print(f"\nInserted {inserted} new December trades")
    except Exception as e:
        conn.rollback()
        print(f"\nError during insert, rolled back: {e}")
        conn.close()
        raise

    # Verify
    cursor.execute("""
        SELECT COUNT(*), ROUND(SUM(gain_loss), 2)
        FROM trade_journal
        WHERE sale_date >= '2025-12-01' AND sale_date <= '2025-12-31'
    """)
    count, pnl = cursor.fetchone()
    print(f"December totals now: {count} trades, ${pnl:,.2f} P&L")

    conn.close()

if __name__ == '__main__':
    main()
