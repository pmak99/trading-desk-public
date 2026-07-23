#!/usr/bin/env python3
"""
Parse Fidelity CSV exports (Realized Gains/Losses or Transaction History)
and correlate with VRP data from the ivcrush database.

Supports flexible column matching for different Fidelity export formats.
"""

import csv
import json
import sqlite3
import os
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from collections import defaultdict
from pathlib import Path

from journal.models import Trade, Strategy
from journal.parsing import (
    COLUMN_MAPPINGS,
    find_column,
    parse_money,
    parse_date,
    parse_option_description,
    parse_occ_symbol,
)

from journal.csv_reader import parse_fidelity_csv


from journal.vrp_correlation import correlate_with_vrp
from journal.statistics import group_trades_into_strategies, calculate_strategy_statistics, calculate_statistics
from journal.reporting import export_journal_csv, print_summary

def main():
    import argparse

    # Get project root for portable paths
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    home = Path.home()

    parser = argparse.ArgumentParser(description='Parse Fidelity CSV exports')
    parser.add_argument('csv_file', nargs='?', help='Path to Fidelity CSV export')
    parser.add_argument('--db', default=str(project_root / '2.0' / 'data' / 'ivcrush.db'),
                       help='Path to ivcrush database for VRP correlation')
    parser.add_argument('--output', default=str(project_root / 'docs' / '2025 Trades'),
                       help='Output directory')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Show skipped rows details')

    args = parser.parse_args()

    # Find CSV file
    if args.csv_file:
        csv_path = args.csv_file
    else:
        # Look for CSV in common locations, prefer most recent
        search_paths = [
            home / 'Downloads',
            home / 'Desktop',
            Path(args.output),
        ]
        csv_candidates = []

        for search_dir in search_paths:
            if search_dir.exists():
                for f in search_dir.iterdir():
                    if not f.suffix.lower() == '.csv':
                        continue
                    fname_lower = f.name.lower()
                    if any(term in fname_lower for term in ['fidelity', 'gain', 'realized', 'portfolio', 'closed']):
                        mtime = f.stat().st_mtime
                        csv_candidates.append((mtime, str(f)))

        # Pick most recent
        csv_path = max(csv_candidates, key=lambda x: x[0])[1] if csv_candidates else None

    if not csv_path or not os.path.exists(csv_path):
        print("No Fidelity CSV file found.")
        print("\nTo use this parser:")
        print("1. Log into Fidelity.com")
        print("2. Go to Accounts & Trade -> Tax Information")
        print("3. Select 'Realized Gain/Loss' for your account")
        print("4. Click 'Download' or 'CSV' to export")
        print("5. Run: python parse_fidelity_csv.py /path/to/downloaded.csv")
        return

    print("=" * 70)
    print("FIDELITY CSV JOURNAL PARSER")
    print("=" * 70)
    print(f"\nInput:  {csv_path}")

    # Parse CSV
    print("\n[1/4] Parsing Fidelity CSV...")
    trades, skipped_rows, unable_rows = parse_fidelity_csv(csv_path)
    print(f"      Found {len(trades)} trades")
    if unable_rows:
        print(f"      WARNING: {len(unable_rows)} positions missing G/L data (Fidelity export error)")

    if skipped_rows and args.verbose:
        print(f"      Skipped {len(skipped_rows)} rows:")
        for num, reason, data in skipped_rows[:10]:
            print(f"        Row {num}: {reason}")
        if len(skipped_rows) > 10:
            print(f"        ... and {len(skipped_rows) - 10} more")

    # Correlate with VRP
    print("\n[2/4] Correlating with earnings data...")
    trades, correlated = correlate_with_vrp(trades, args.db)
    print(f"      Matched {correlated} trades to earnings events")

    # Calculate stats
    print("\n[3/4] Calculating statistics...")
    strategies = group_trades_into_strategies(trades)
    singles = sum(1 for s in strategies if s.strategy_type == 'SINGLE')
    spreads = sum(1 for s in strategies if s.strategy_type == 'SPREAD')
    condors = sum(1 for s in strategies if s.strategy_type == 'IRON_CONDOR')
    print(f"      Grouped {len(trades)} legs → {len(strategies)} strategies"
          f"  ({singles} single, {spreads} spread"
          + (f", {condors} condor" if condors else "") + ")")
    stats = calculate_strategy_statistics(strategies)

    # Print summary
    print_summary(stats, len(skipped_rows), unable_rows)

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

    print("\nDone!")


if __name__ == "__main__":
    main()
