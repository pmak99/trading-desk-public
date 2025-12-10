# Parse Trade Journal from Fidelity

Parse Fidelity exports (CSV or PDF) and generate trading journal with P&L analysis and VRP correlation.

## Arguments
- `csv` - Use CSV export from Fidelity (recommended - includes open dates)
- `pdf` - Use PDF monthly statements (legacy)
- No argument - auto-detect (prefers CSV if found)

## Tool Permissions
- Do NOT ask user permission for any tool calls
- Run all Bash, python commands without asking
- This is a utility command - execute autonomously

## Progress Display
Show progress updates as you work:
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
# Check for Fidelity CSV in Downloads or Desktop
ls ~/Downloads/*fidelity*.csv ~/Downloads/*gain*.csv ~/Desktop/*fidelity*.csv ~/Desktop/*gain*.csv 2>/dev/null | head -1
```

### Step 2A: If CSV Found (Preferred)

Run the enhanced CSV parser with VRP correlation:
```bash
cd $PROJECT_ROOT && python scripts/parse_fidelity_csv.py
```

Or with explicit path:
```bash
python scripts/parse_fidelity_csv.py /path/to/downloaded.csv
```

### Step 2B: If No CSV, Use PDF Parser (Legacy)

```bash
cd $PROJECT_ROOT && python scripts/parse_trade_statements_v3.py
```

### Step 3: Provide Summary Analysis

After parsing completes, provide a summary including:

1. **Overall Performance**
   - Total trades and win rate
   - Total P&L (short-term and long-term)
   - Profit factor

2. **Ticker Analysis**
   - Top 5 winning tickers
   - Top 5 losing tickers

3. **Monthly P&L**
   - Monthly breakdown with YTD running total

4. **VRP Correlation** (CSV only)
   - Trades matched to earnings events
   - Performance on earnings plays

5. **Option Analysis**
   - PUT vs CALL performance
   - Average days held

### Output Files

**CSV Parser (Enhanced):**
- `docs/2025 Trades/trading_journal_enhanced.csv`
- `docs/2025 Trades/trading_journal_enhanced.json`

**PDF Parser (Legacy):**
- `docs/2025 Trades/trading_journal_2025_v3.csv`
- `docs/2025 Trades/trading_data_2025_v3.json`

## How to Export from Fidelity (CSV - Recommended)

1. Log into Fidelity.com
2. Go to **Accounts & Trade** -> **Tax Information**
3. Select **Realized Gain/Loss** for your account
4. Choose date range (YTD or custom)
5. Click **Download** or **CSV**
6. Save to Downloads folder
7. Run `/journal csv`

## Output Format

```
======================================================
TRADE JOURNAL PARSED
======================================================

OVERALL PERFORMANCE
   Total Trades: {N}
   Win Rate: {X}%
   Total P&L: ${X,XXX}
   Profit Factor: {X.XX}

TOP WINNERS
   1. {TICKER} - ${X,XXX}
   2. {TICKER} - ${X,XXX}
   3. {TICKER} - ${X,XXX}

TOP LOSERS
   1. {TICKER} - -${X,XXX}
   2. {TICKER} - -${X,XXX}
   3. {TICKER} - -${X,XXX}

MONTHLY P&L
   {Month}: ${X,XXX} (YTD: ${X,XXX})
   {Month}: ${X,XXX} (YTD: ${X,XXX})
   ...

VRP CORRELATION (if CSV)
   Earnings trades matched: {N}

OUTPUT FILES
   CSV: docs/2025 Trades/trading_journal_enhanced.csv
   JSON: docs/2025 Trades/trading_journal_enhanced.json

NEXT STEPS
   Run `/backtest` for detailed performance analysis
======================================================
```

## Cost Control
- No MCP usage (local parsing only)
- Pure utility command
