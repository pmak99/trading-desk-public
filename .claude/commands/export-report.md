# Export Trading Report (Excel/CSV Export Skill)

Generate formatted trading reports and export scan results to spreadsheets.

## Quick Export

Export today's scan results to CSV:

```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && ./trade.sh scan $(date +%Y-%m-%d) 2>&1 | python ../scripts/export_scan_results.py
```

Output files will be in `docs/scan_exports/`:
- `scan_YYYYMMDD.csv` - CSV format
- `scan_YYYYMMDD.json` - JSON format

## Available Export Types

### 1. Export Scan Results to CSV
After running a scan, export the results:

```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && ./trade.sh scan $(date +%Y-%m-%d) 2>&1 | tee /tmp/scan_output.txt
```

Then parse and format into CSV with columns:
- Ticker, Earnings Date, VRP Ratio, VRP Tier
- Implied Move %, Historical Mean Move %
- Liquidity Tier, Recommended Strategy
- POP, Reward/Risk, Overall Score

### 2. Export Trade Journal to Excel Format
Read the existing journal and create formatted output:

```bash
cat "$PROJECT_ROOT/docs/2025 Trades/trading_journal_2025_v3.csv"
```

### 3. Generate Performance Summary Report

Read the JSON data for detailed analysis:

```bash
cat "$PROJECT_ROOT/docs/2025 Trades/trading_data_2025_v3.json"
```

## Report Templates

### Weekly Performance Report
Generate a report with:
- Week's closed trades
- Net P&L for the week
- Win rate
- Top winner and biggest loser
- Running YTD total

### Monthly Performance Report
Generate a report with:
- All trades closed in the month
- P&L by ticker
- Strategy breakdown (spreads vs naked options)
- Comparison to previous month
- YTD cumulative performance

### Ticker Analysis Report
For a specific ticker, show:
- All historical trades
- Total P&L from that ticker
- Win rate for that ticker
- Average hold time
- Best and worst trade

## Export Formats

When user requests export, generate the data in requested format:
- **CSV**: Comma-separated, Excel-compatible
- **JSON**: Structured data for programmatic use
- **Markdown Table**: For documentation/notes

## Usage Examples

"Export this week's trades to CSV"
"Generate a monthly report for November 2025"
"Show me all NVDA trades in spreadsheet format"
"Create a YTD performance summary"
