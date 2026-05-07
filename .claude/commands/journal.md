# Parse Trade Journal from Fidelity

Parse Fidelity exports (CSV or PDF) and generate trading journal with P&L analysis and VRP correlation.

## Arguments
$ARGUMENTS

- One or more CSV file paths — parsed and combined in order
- `--ira` flag — tag all imported trades as IRA (default: TAXABLE)
- `pdf` — use PDF monthly statements (legacy)
- No argument — auto-detect (prefers CSV if found)

**Examples:**
- `/journal file.csv` — parse single taxable CSV
- `/journal 2024.csv 2025.csv 2026.csv --ira` — combine and import as IRA
- `/journal` — auto-detect latest CSV in Downloads/Desktop

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

### Step 1: Parse Arguments

Scan `$ARGUMENTS` for:
- `--ira` flag → set `ACCOUNT_TYPE=IRA`, remove from file list
- Remaining args that are file paths → collect as `CSV_FILES`
- If no file paths given → auto-detect (see below)

After collecting `CSV_FILES`, also check: if any filename contains "ira" (case-insensitive), automatically set `ACCOUNT_TYPE=IRA` even if `--ira` was not explicitly passed.

### Step 2: Detect or Collect CSV Files

**If explicit file paths provided:** use them directly. If multiple, combine them:
```bash
# Combine multiple CSVs (header once, data rows from all)
{ cat "file1.csv"; tail -n +2 "file2.csv"; tail -n +2 "file3.csv"; } > /tmp/journal_combined.csv
```
Use `/tmp/journal_combined.csv` as the input. If only one file, use it directly.

**If no file paths given**, auto-detect:
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

### Step 3: Parse CSV

Use `/tmp/journal_ira_export` as output dir when `--ira`, otherwise default output dir:

```bash
# With --ira:
"/Users/prashant/PycharmProjects/Trading Desk/2.0/venv/bin/python" \
  "/Users/prashant/PycharmProjects/Trading Desk/scripts/parse_fidelity_csv.py" \
  "/path/to/input.csv" --output "/tmp/journal_ira_export"

# Without --ira (taxable):
"/Users/prashant/PycharmProjects/Trading Desk/2.0/venv/bin/python" \
  "/Users/prashant/PycharmProjects/Trading Desk/scripts/parse_fidelity_csv.py" \
  "/path/to/input.csv"
```

If no explicit file path, run without argument (script has its own auto-detect):
```bash
"/Users/prashant/PycharmProjects/Trading Desk/2.0/venv/bin/python" "/Users/prashant/PycharmProjects/Trading Desk/scripts/parse_fidelity_csv.py"
```

### Step 3B: If No CSV, Use PDF Parser (Legacy)

```bash
"/Users/prashant/PycharmProjects/Trading Desk/2.0/venv/bin/python" "/Users/prashant/PycharmProjects/Trading Desk/scripts/parse_trade_statements_v3.py"
```

### Step 4: Duplicate Check

Before importing, check how many rows already exist in the DB for the same date range:

```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/2.0/data/ivcrush.db" \
  "SELECT MIN(sale_date), MAX(sale_date), COUNT(*) FROM trade_journal WHERE account_type='TAXABLE';"
# (use account_type='IRA' when --ira)
```

Report the overlap to the user and confirm before proceeding to import.

### Step 5: Import to DB

```bash
# With --ira:
"/Users/prashant/PycharmProjects/Trading Desk/2.0/venv/bin/python" \
  "/Users/prashant/PycharmProjects/Trading Desk/scripts/import_journal_to_db.py" \
  --csv "/tmp/journal_ira_export/trading_journal_enhanced.csv" \
  --account-type IRA

# Without --ira (taxable):
"/Users/prashant/PycharmProjects/Trading Desk/2.0/venv/bin/python" \
  "/Users/prashant/PycharmProjects/Trading Desk/scripts/import_journal_to_db.py" \
  --csv "/Users/prashant/PycharmProjects/Trading Desk/docs/2025 Trades/trading_journal_enhanced.csv"
```

### Step 6: Regroup into Strategies

```bash
# With --ira:
"/Users/prashant/PycharmProjects/Trading Desk/2.0/venv/bin/python" \
  "/Users/prashant/PycharmProjects/Trading Desk/scripts/regroup_strategies.py" \
  --account-type IRA

# Without --ira:
"/Users/prashant/PycharmProjects/Trading Desk/2.0/venv/bin/python" \
  "/Users/prashant/PycharmProjects/Trading Desk/scripts/regroup_strategies.py"
```

### Step 7: TRR Backfill

Run inline Python to tag new strategies with TRR:

```python
import sqlite3
DB = "/Users/prashant/PycharmProjects/Trading Desk/2.0/data/ivcrush.db"
account = "IRA"  # or "TAXABLE"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
rows = cur.execute(
    "SELECT id, symbol FROM strategies WHERE trr_at_entry IS NULL AND account_type=?", (account,)
).fetchall()
updated = skipped = 0
for row in rows:
    moves = cur.execute(
        "SELECT ABS(close_move_pct) m FROM historical_moves WHERE ticker=? AND close_move_pct IS NOT NULL ORDER BY earnings_date DESC LIMIT 8",
        (row['symbol'],)
    ).fetchall()
    if len(moves) < 2:
        skipped += 1; continue
    vals = [r['m'] for r in moves]
    avg = sum(vals) / len(vals)
    trr = round(max(vals) / avg, 3) if avg > 0 else None
    if trr:
        cur.execute("UPDATE strategies SET trr_at_entry=? WHERE id=?", (trr, row['id']))
        updated += 1
    else:
        skipped += 1
conn.commit(); conn.close()
print(f"TRR updated: {updated}, skipped: {skipped}")
```

### Step 8: Generate Summary

Present structured summary from parser output.

## Output Format

```
==============================================================
TRADE JOURNAL PARSED  [IRA]   ← include account type if --ira
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
