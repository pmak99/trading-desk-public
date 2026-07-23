"""Export parsed trades to CSV and print the terminal summary report."""

import csv
from typing import Dict, List

from journal.models import Trade


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


def print_summary(stats: Dict, skipped_count: int = 0, unable_rows: list = None):
    """Print formatted summary"""

    print("\n" + "=" * 70)
    print("TRADING JOURNAL SUMMARY")
    print("=" * 70)

    print(f"\n   OVERALL PERFORMANCE")
    print(f"   Total Trades:    {stats['total_trades']}")
    print(f"   Win Rate:        {stats['win_rate']}%")
    print(f"   Winners:         {stats['winners']}")
    print(f"   Losers:          {stats['losers']}")

    print(f"\n   PROFIT & LOSS")
    print(f"   Total P&L:       ${stats['total_pnl']:,.2f}")
    print(f"   From Winners:    ${stats['winner_pnl']:,.2f}")
    print(f"   From Losers:     ${stats['loser_pnl']:,.2f}")
    print(f"   Avg Win:         ${stats['avg_win']:,.2f}")
    print(f"   Avg Loss:        ${stats['avg_loss']:,.2f}")
    print(f"   Profit Factor:   {stats['profit_factor']}")

    print(f"\n   BY INSTRUMENT")
    print(f"   Options:         {stats['options_count']}")
    print(f"   Stocks:          {stats['stocks_count']}")

    if stats.get('by_strategy_type'):
        print(f"\n   BY STRATEGY TYPE")
        for stype, data in stats['by_strategy_type'].items():
            print(f"   {stype:12} {data['count']:4} trades  {data['win_rate']:5.1f}% win  ${data['pnl']:>12,.2f}")

    if stats['by_option_type']:
        print(f"\n   BY OPTION TYPE")
        for otype, data in stats['by_option_type'].items():
            win_rate = 100 * data['wins'] / data['count'] if data['count'] > 0 else 0
            print(f"   {otype:8} {data['count']:4} trades  {win_rate:5.1f}% win  ${data['pnl']:>12,.2f}")

    print(f"\n   TOP 5 TICKERS BY P&L")
    for i, (ticker, data) in enumerate(list(stats['by_ticker'].items())[:5]):
        print(f"   {ticker:8} {data['count']:3} trades  {data['win_rate']:5.1f}% win  ${data['pnl']:>12,.2f}")

    print(f"\n   MONTHLY P&L")
    ytd = 0
    current_year = None
    for month, data in stats['by_month'].items():
        # Determine year from month string (format: YYYY-MM)
        month_year = int(month.split('-')[0]) if '-' in month else None
        # Reset YTD at start of each new calendar year
        if month_year and month_year != current_year:
            current_year = month_year
            ytd = 0
        ytd += data['pnl']
        print(f"   {month}  {data['count']:3} trades  {data['win_rate']:5.1f}% win  ${data['pnl']:>10,.2f}  (YTD: ${ytd:>12,.2f})")

    if stats['earnings_correlated'] > 0:
        print(f"\n   EARNINGS CORRELATION")
        print(f"   Trades matched to earnings: {stats['earnings_correlated']}")

    if stats['wash_sales']['count'] > 0:
        print(f"\n   WASH SALES")
        print(f"   Count:           {stats['wash_sales']['count']}")
        print(f"   Disallowed:      ${stats['wash_sales']['total']:,.2f}")

    if unable_rows:
        print(f"\n   *** DATA GAP — FIDELITY MISSING G/L FOR {len(unable_rows)} POSITIONS ***")
        print(f"   These positions were closed but Fidelity could not export gain/loss.")
        print(f"   Totals above are UNDERSTATED. Verify these in Fidelity directly:\n")
        for r in unable_rows:
            exp = r['expiration'] or '?'
            strike = r['strike'] or '?'
            otype = r['option_type'] or '?'
            print(f"   {r['symbol']:6}  {otype:4}  strike {strike}  exp {exp}")
            print(f"          Description: {r['description']}")
        print()

    if skipped_count > 0:
        print(f"\n   PARSING NOTES")
        print(f"   Rows skipped:    {skipped_count}")
