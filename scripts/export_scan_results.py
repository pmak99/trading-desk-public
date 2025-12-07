#!/usr/bin/env python3
"""
Export scan results to CSV format.
Parses the output of ./trade.sh scan and creates a structured CSV.
"""

import sys
import re
import csv
import json
from datetime import datetime
from pathlib import Path


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    return ansi_escape.sub('', text)


def parse_scan_output(text: str) -> list[dict]:
    """Parse scan output text into structured data."""
    # Strip ANSI codes
    text = strip_ansi(text)

    results = []
    current_ticker = None
    current_data = {}

    lines = text.split('\n')

    for line in lines:
        # New ticker section - look for "Analyzing TICKER" pattern
        ticker_match = re.search(r'Analyzing\s+([A-Z]{1,5})\s*$', line)
        if ticker_match:
            if current_ticker and current_data.get('vrp_ratio'):
                results.append(current_data)
            current_ticker = ticker_match.group(1)
            current_data = {
                'ticker': current_ticker,
                'scan_date': datetime.now().strftime('%Y-%m-%d')
            }
            continue

        # Also check for old format: "TICKER | Company"
        old_ticker_match = re.match(r'^([A-Z]{1,5})\s+\|\s+(.+)$', line)
        if old_ticker_match:
            if current_ticker and current_data.get('vrp_ratio'):
                results.append(current_data)
            current_ticker = old_ticker_match.group(1)
            current_data = {
                'ticker': current_ticker,
                'company': old_ticker_match.group(2).strip(),
                'scan_date': datetime.now().strftime('%Y-%m-%d')
            }
            continue

        # VRP data - look for "VRP Ratio: X.XXx" or "VRP X.XXx"
        vrp_match = re.search(r'VRP(?:\s+Ratio)?[:\s]+(\d+\.?\d*)x', line, re.I)
        if vrp_match and current_data:
            current_data['vrp_ratio'] = float(vrp_match.group(1))

        # VRP Recommendation
        rec_match = re.search(r'Recommendation[:\s]+(EXCELLENT|GOOD|MARGINAL|SKIP)', line, re.I)
        if rec_match and current_data:
            current_data['vrp_tier'] = rec_match.group(1).upper()

        # Implied move - "Implied Move: X.XX%" or "✓ Implied Move: X.XX%"
        implied_match = re.search(r'Implied Move[:\s]+(\d+\.?\d*)%', line, re.I)
        if implied_match and current_data:
            current_data['implied_move_pct'] = float(implied_match.group(1))

        # Historical mean - "Historical Mean: X.XX%"
        hist_match = re.search(r'Historical Mean[:\s]+(\d+\.?\d*)%', line, re.I)
        if hist_match and current_data:
            current_data['historical_mean_pct'] = float(hist_match.group(1))

        # Liquidity tier
        liq_match = re.search(r'Liquidity Tier[:\s]+(EXCELLENT|WARNING|REJECT)', line, re.I)
        if liq_match and current_data:
            current_data['liquidity_tier'] = liq_match.group(1).upper().rstrip('*')

        # Recommended strategy
        strat_match = re.search(r'(Iron Condor|Iron Butterfly|Bull Put Spread|Bear Call Spread|Strangle|Straddle)', line, re.I)
        if strat_match and current_data:
            current_data['recommended_strategy'] = strat_match.group(1)

        # POP
        pop_match = re.search(r'POP[:\s]+(\d+\.?\d*)%', line, re.I)
        if pop_match and current_data:
            current_data['pop'] = float(pop_match.group(1))

        # Score
        score_match = re.search(r'(?:Overall\s+)?Score[:\s]+(\d+\.?\d*)', line, re.I)
        if score_match and current_data:
            current_data['overall_score'] = float(score_match.group(1))

        # Edge score
        edge_match = re.search(r'Edge Score[:\s]+(\d+\.?\d*)', line, re.I)
        if edge_match and current_data:
            current_data['edge_score'] = float(edge_match.group(1))

        # Mark tradeable
        if 'TRADEABLE OPPORTUNITY' in line and current_data:
            current_data['tradeable'] = True

    # Add last ticker
    if current_ticker and current_data.get('vrp_ratio'):
        results.append(current_data)

    return results


def export_to_csv(results: list[dict], output_path: str):
    """Export parsed results to CSV."""
    if not results:
        print("No results to export")
        return

    fieldnames = [
        'ticker', 'scan_date', 'vrp_ratio', 'vrp_tier',
        'implied_move_pct', 'historical_mean_pct', 'liquidity_tier',
        'edge_score', 'tradeable', 'recommended_strategy', 'pop', 'overall_score'
    ]

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        for row in sorted(results, key=lambda x: x.get('vrp_ratio', 0), reverse=True):
            writer.writerow(row)

    print(f"Exported {len(results)} results to: {output_path}")


def export_to_json(results: list[dict], output_path: str):
    """Export parsed results to JSON."""
    with open(output_path, 'w') as f:
        json.dump({
            'scan_date': datetime.now().strftime('%Y-%m-%d'),
            'total_tickers': len(results),
            'results': sorted(results, key=lambda x: x.get('vrp_ratio', 0), reverse=True)
        }, f, indent=2)

    print(f"Exported {len(results)} results to: {output_path}")


def main():
    """Main entry point."""
    # Read from stdin or file
    if len(sys.argv) > 1:
        input_path = sys.argv[1]
        with open(input_path) as f:
            text = f.read()
    else:
        text = sys.stdin.read()

    # Parse
    results = parse_scan_output(text)

    if not results:
        print("No scan results found in input")
        sys.exit(1)

    # Export
    output_dir = Path("docs/scan_exports")
    output_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime('%Y%m%d')
    csv_path = output_dir / f"scan_{date_str}.csv"
    json_path = output_dir / f"scan_{date_str}.json"

    export_to_csv(results, str(csv_path))
    export_to_json(results, str(json_path))

    # Print summary
    print(f"\nScan Summary ({date_str}):")
    print(f"  Total tickers: {len(results)}")
    excellent = [r for r in results if r.get('vrp_tier') == 'EXCELLENT']
    print(f"  EXCELLENT VRP: {len(excellent)}")
    print(f"  Top 3 by VRP:")
    for r in sorted(results, key=lambda x: x.get('vrp_ratio', 0), reverse=True)[:3]:
        print(f"    {r['ticker']}: {r.get('vrp_ratio', 0):.1f}x")


if __name__ == "__main__":
    main()
