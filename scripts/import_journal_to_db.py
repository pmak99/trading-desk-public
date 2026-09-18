#!/usr/bin/env python3
"""
Import the enhanced CSV trade journal into Postgres trade_journal.

Reads trading_journal_enhanced.csv (output of parse_fidelity_csv.py) and inserts
rows with ON CONFLICT ON CONSTRAINT trade_journal_dedup DO NOTHING, so a
re-import skips rows already present. Each row runs in its own savepoint: a
row the database rejects is reported and skipped, and the rest still commit.

Aggregates multiple fills that share the same UNIQUE key
(symbol, acquired_date, sale_date, option_type, strike, cost_basis)
before inserting, so Fidelity's multi-fill lots don't get dropped.
"""

import csv
from collections import defaultdict
from pathlib import Path

from common.db import connect, target  # noqa: E402


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
    for fill_rows in groups.values():
        if len(fill_rows) == 1:
            aggregated.append(fill_rows[0])
            continue

        # Aggregate: sum quantity, proceeds, gain_loss, cost_basis
        base = dict(fill_rows[0])  # copy first row as template
        # float: fractional share lots exist, and int() truncated them.
        total_qty = sum(float(r['Quantity']) for r in fill_rows if r.get('Quantity', '').strip())
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


def import_csv_to_db(csv_path: str, dry_run: bool = False, account_type: str = 'TAXABLE') -> dict:
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
        conn = connect()
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
            qty_str = row.get('Quantity', '').strip()
            # float, not int(float(...)): trade_journal.quantity is numeric
            # because Fidelity reports fractional share lots (9 rows hold 0.4,
            # 2.4, 0.444 ...). int() truncated a 0.4 lot to 0.
            quantity = float(qty_str) if qty_str else None
            cost_basis = parse_dollar(row['Cost Basis'])
            proceeds = parse_dollar(row['Proceeds'])
            gain_loss = parse_dollar(row['Gain/Loss'])
            # bool: is_winner is a boolean column, and Postgres rejects 1/0.
            is_winner = row.get('Winner', '').strip().upper() == 'YES'
            term = row.get('Term', '').strip() or None
            wash_sale = parse_dollar(row.get('Wash Sale', ''))
            earnings_date = row.get('Earnings Date', '').strip() or None
            actual_move_str = row.get('Actual Move %', '').strip()
            actual_move = float(actual_move_str.replace('%', '')) if actual_move_str else None

            if dry_run:
                inserted += 1  # counts records to attempt, not guaranteed inserts
                continue

            try:
                # One SAVEPOINT per row. Without it, the first failing row
                # aborts the whole transaction, every later execute raises
                # "current transaction is aborted", each is counted as skipped,
                # and the final commit silently rolls back rows that looked
                # inserted -- one bad CSV line cost the entire import.
                with conn.transaction():
                    cursor.execute("""
                        INSERT INTO trade_journal
                        (symbol, acquired_date, sale_date, days_held, option_type, strike,
                         expiration, quantity, cost_basis, proceeds, gain_loss, is_winner,
                         term, wash_sale_amount, earnings_date, actual_move, account_type)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        -- ⚠️ This named idx_trade_journal_dedup, a unique INDEX, from
                        -- the 2026-09-12 cutover until migration 2. ON CONFLICT ON
                        -- CONSTRAINT accepts only a constraint, so every row raised
                        -- "constraint ... does not exist", the except below
                        -- swallowed it, and an import reported zero inserts.
                        -- trade_journal_dedup is a real UNIQUE NULLS NOT DISTINCT
                        -- constraint: NULL option_type/strike (stock legs) compare
                        -- equal, which is what the old COALESCE index did.
                        ON CONFLICT ON CONSTRAINT trade_journal_dedup DO NOTHING
                    """, (
                        symbol, acquired_date, sale_date, days_held, option_type, strike,
                        expiration, quantity, cost_basis, proceeds, gain_loss, is_winner,
                        term, wash_sale, earnings_date, actual_move, account_type,
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
            print("Committed to database")

    except Exception:
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

    parser = argparse.ArgumentParser(description='Import enhanced CSV into trade_journal')
    parser.add_argument('--csv', default=str(default_csv), help='Path to enhanced CSV')
    parser.add_argument('--execute', action='store_true',
                        help='Insert into trade_journal (default: report only)')
    parser.add_argument('--account-type', default='TAXABLE', choices=['TAXABLE', 'IRA'],
                        help='Account type tag for imported rows (default: TAXABLE)')

    args = parser.parse_args()
    args.dry_run = not args.execute

    print(f"CSV: {args.csv}")
    print(f"Database: {target()}")
    print(f"Account type: {args.account_type}")
    print(f"Mode: {'DRY RUN (pass --execute to write)' if args.dry_run else 'LIVE'}")
    print()

    result = import_csv_to_db(args.csv, dry_run=args.dry_run, account_type=args.account_type)

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
