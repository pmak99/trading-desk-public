# Parse Trade Journal from Fidelity

Parse Fidelity exports (CSV or PDF) and generate trading journal with P&L analysis and VRP correlation.

## Arguments
$ARGUMENTS

- `csv` - Use CSV export from Fidelity (recommended - includes open dates and VRP correlation)
- `pdf` - Use PDF monthly statements (legacy)
- No argument - auto-detect (prefers CSV if found)

## Tool Permissions
- Do NOT ask user permission for any tool calls
- Run all Bash, python commands without asking
- This is a utility command - execute autonomously
- No MCP or Perplexity usage - pure local utility

## Progress Display
```
[1/4] Detecting input format...
[2/4] Parsing trades...
[3/4] Correlating with VRP database...
[4/4] Generating summary...
```

## Step-by-Step Instructions

### Step 1: Detect Input Format

Check for CSV first (better data), then fall back to PDF:

```bash
ls -t ~/Downloads/*fidelity*.csv ~/Downloads/*gain*.csv ~/Downloads/*Gain*.csv ~/Downloads/*realized*.csv ~/Desktop/*fidelity*.csv ~/Desktop/*gain*.csv 2>/dev/null | head -5
```

If argument is `pdf` or no CSV found:
```bash
ls -t ~/Downloads/*fidelity*.pdf ~/Downloads/*statement*.pdf ~/Desktop/*fidelity*.pdf 2>/dev/null | head -5
```

If no files found at all:
```
NO FIDELITY EXPORT FOUND

Looked in ~/Downloads and ~/Desktop for:
  CSV: *fidelity*.csv, *gain*.csv, *realized*.csv
  PDF: *fidelity*.pdf, *statement*.pdf

To export from Fidelity:
  1. Log into Fidelity.com
  2. Accounts & Trade > Tax Information
  3. Select Realized Gain/Loss for your account
  4. Choose date range (YTD or custom)
  5. Click Download/CSV
  6. Save to Downloads folder
  7. Run /journal again
```

### Step 2A: If CSV Found (Preferred)

Run the enhanced CSV parser:
```bash
"$PROJECT_ROOT/2.0/venv/bin/python" "$PROJECT_ROOT/scripts/parse_fidelity_csv.py" "/path/to/detected/file.csv"
```

If no explicit file path, run without argument (script has its own auto-detect):
```bash
"$PROJECT_ROOT/2.0/venv/bin/python" "$PROJECT_ROOT/scripts/parse_fidelity_csv.py"
```

### Step 2B: If No CSV, Use PDF Parser (Legacy)

```bash
"$PROJECT_ROOT/2.0/venv/bin/python" "$PROJECT_ROOT/scripts/parse_trade_statements_v3.py"
```

### Step 3: Correlate with VRP Database

If CSV parser was used, it automatically correlates with ivcrush.db. No extra step needed.

For additional DB context:
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT symbol, COUNT(*) trades, ROUND(SUM(gain_loss), 0) total_pnl,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) win_rate
   FROM strategies
   GROUP BY symbol
   ORDER BY total_pnl DESC
   LIMIT 10;"
```

### Step 4: Generate Summary

Present structured summary from parser output.

## Output Format

```
==============================================================
TRADE JOURNAL PARSED
==============================================================

OVERALL PERFORMANCE
  Total Trades:   {N}
  Win Rate:       {X.X}%
  Total P&L:      ${X,XXX}
  Profit Factor:  {X.XX}
  Short-Term P&L: ${X,XXX}
  Long-Term P&L:  ${X,XXX}

TOP 5 WINNERS
  1. {TICKER} {description} -- +${X,XXX}
  2. ...

TOP 5 LOSERS
  1. {TICKER} {description} -- -${X,XXX}
  2. ...

MONTHLY P&L
  {Month YYYY}: ${X,XXX}  (YTD: ${X,XXX})
  ...

VRP CORRELATION (CSV only)
  Earnings trades matched:   {N}
  Earnings trade win rate:   {X}%
  Non-earnings trade win rate: {X}%

OPTION TYPE BREAKDOWN
  PUT trades:  {N} ({X}% win rate, ${X,XXX} P&L)
  CALL trades: {N} ({X}% win rate, ${X,XXX} P&L)

OUTPUT FILES
  CSV:  docs/2025 Trades/trading_journal_enhanced.csv
  JSON: docs/2025 Trades/trading_journal_enhanced.json

NEXT STEPS
  Run /backtest for detailed performance analysis
==============================================================
```

## Error Handling
- If parser script fails, display the full error output
- If CSV has unexpected format, show first 3 lines and column headers detected
- If DB is locked, note it and show parser results without correlation

## Cost Control
- No MCP usage (local parsing only)
- Pure utility command
