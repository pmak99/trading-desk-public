#!/usr/bin/env python3
"""
Import enhanced CSV trade journal into ivcrush.db trade_journal table.

Reads trading_journal_enhanced.csv (output of parse_fidelity_csv.py) and inserts
rows into trade_journal using INSERT OR IGNORE to skip duplicates.

Aggregates multiple fills that share the same UNIQUE key
(symbol, acquired_date, sale_date, option_type, strike, cost_basis)
before inserting, so Fidelity's multi-fill lots don't get dropped.
"""

import csv
import sqlite3
from collections import defaultdict
from pathlib import Path


def parse_dollar(val: str) -> float:
    """Parse dollar string like '$1,234.56' or '$-123.45' to float."""
    if not val or not val.strip():
        return 0.0
    val = val.strip().replace('$', '').replace(',', '')
    return float(val)


def aggregate_fills(rows: list[dict]) -> list[dict]:
    """
    Aggregate CSV rows that share the same UNIQUE key.

    Fidelity produces multiple fills at the same strike/date/cost_basis=0
    for credit trades. These collide on the UNIQUE constraint.
    We aggregate them by summing quantity, proceeds, gain_loss, cost_basis.
    """
    groups = defaultdict(list)
    for row in rows:
        symbol = row['Symbol'].strip()
        if not symbol:
            continue
        # Key must match DB UNIQUE constraint types (floats, not strings)
        # to catch fills like "440.0" vs "440" or "$54.14" vs "$54.1400"
        strike_str = row.get('Strike', '').strip()
        key = (
            symbol,
            row.get('Acquired Date', '').strip(),
            row['Sale Date'].strip(),
            row.get('Option Type', '').strip(),
            float(strike_str) if strike_str else None,
            parse_dollar(row['Cost Basis']),
        )
        groups[key].append(row)

    aggregated = []
    for key, fill_rows in groups.items():
        if len(fill_rows) == 1:
            aggregated.append(fill_rows[0])
            continue

        # Aggregate: sum quantity, proceeds, gain_loss, cost_basis
        base = dict(fill_rows[0])  # copy first row as template
        total_qty = sum(int(r['Quantity']) for r in fill_rows if r.get('Quantity', '').strip())
        total_proceeds = sum(parse_dollar(r['Proceeds']) for r in fill_rows)
        total_cost = sum(parse_dollar(r['Cost Basis']) for r in fill_rows)
        total_gl = sum(parse_dollar(r['Gain/Loss']) for r in fill_rows)
        total_wash = sum(parse_dollar(r.get('Wash Sale', '')) for r in fill_rows)

        base['Quantity'] = str(total_qty)
        base['Proceeds'] = f"${total_proceeds:.2f}"
        base['Cost Basis'] = f"${total_cost:.2f}"
        base['Gain/Loss'] = f"${total_gl:.2f}"
        base['Wash Sale'] = f"${total_wash:.2f}" if total_wash else ''

        # Winner is determined by aggregate P&L
        base['Winner'] = 'YES' if total_gl > 0 else 'NO'

        aggregated.append(base)

    return aggregated


def import_csv_to_db(csv_path: str, db_path: str, dry_run: bool = False) -> dict:
    """Import enhanced CSV into trade_journal table."""
    inserted = 0
    skipped = 0
    errors = []

    with open(csv_path, newline='') as f:
        reader = csv.DictReader(f)
        raw_rows = list(reader)

    print(f"CSV rows: {len(raw_rows)}")
    rows = aggregate_fills(raw_rows)
    print(f"After aggregating fills: {len(rows)} unique records")

    if dry_run:
        conn = None
    else:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

    try:
        for i, row in enumerate(rows, 1):
            symbol = row['Symbol'].strip()
            if not symbol:
                skipped += 1
                continue

            option_type = row.get('Option Type', '').strip() or None

            # Parse fields
            sale_date = row['Sale Date'].strip()
            acquired_date = row['Acquired Date'].strip() or None
            days_held = int(row['Days Held']) if row.get('Days Held', '').strip() else None
            strike = float(row['Strike']) if row.get('Strike', '').strip() else None
            expiration = row.get('Expiration', '').strip() or None
            quantity = int(row['Quantity']) if row.get('Quantity', '').strip() else None
            cost_basis = parse_dollar(row['Cost Basis'])
            proceeds = parse_dollar(row['Proceeds'])
            gain_loss = parse_dollar(row['Gain/Loss'])
            is_winner = 1 if row.get('Winner', '').strip().upper() == 'YES' else 0
            term = row.get('Term', '').strip() or None
            wash_sale = parse_dollar(row.get('Wash Sale', ''))
            earnings_date = row.get('Earnings Date', '').strip() or None
            actual_move_str = row.get('Actual Move %', '').strip()
            actual_move = float(actual_move_str.replace('%', '')) if actual_move_str else None

            if dry_run:
                inserted += 1  # counts records to attempt, not guaranteed inserts
                continue

            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO trade_journal
                    (symbol, acquired_date, sale_date, days_held, option_type, strike,
                     expiration, quantity, cost_basis, proceeds, gain_loss, is_winner,
                     term, wash_sale_amount, earnings_date, actual_move)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol, acquired_date, sale_date, days_held, option_type, strike,
                    expiration, quantity, cost_basis, proceeds, gain_loss, is_winner,
                    term, wash_sale, earnings_date, actual_move,
                ))
                if cursor.rowcount > 0:
                    inserted += 1
                else:
                    skipped += 1
            except Exception as e:
                errors.append(f"Row {i} ({symbol}): {e}")
                skipped += 1

        if conn and not dry_run:
            conn.commit()
            print(f"Committed to database")

    except Exception as e:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            conn.close()

    return {'inserted': inserted, 'skipped': skipped, 'errors': errors}


def main():
    import argparse

    project_root = Path(__file__).parent.parent
    default_csv = project_root / "docs" / "2025 Trades" / "trading_journal_enhanced.csv"
    default_db = project_root / "2.0" / "data" / "ivcrush.db"

    parser = argparse.ArgumentParser(description='Import enhanced CSV into trade_journal')
    parser.add_argument('--csv', default=str(default_csv), help='Path to enhanced CSV')
    parser.add_argument('--db', default=str(default_db), help='Database path')
    parser.add_argument('--dry-run', action='store_true', help='Preview without inserting')

    args = parser.parse_args()

    print(f"CSV: {args.csv}")
    print(f"Database: {args.db}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print()

    result = import_csv_to_db(args.csv, args.db, dry_run=args.dry_run)

    prefix = "[DRY RUN] " if args.dry_run else ""
    print(f"\n{prefix}Results:")
    label = "Would attempt" if args.dry_run else "Inserted"
    print(f"  {label}: {result['inserted']}")
    print(f"  Skipped (duplicates): {result['skipped']}")
    if result['errors']:
        print(f"  Errors: {len(result['errors'])}")
        for err in result['errors']:
            print(f"    {err}")


if __name__ == "__main__":
    main()
