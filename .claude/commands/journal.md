# Parse Trade Journal from Fidelity Statements

Parse Fidelity monthly statements and generate trading journal with P&L analysis.

## Arguments
None - automatically processes PDFs in the statements directory

## Tool Permissions
- Do NOT ask user permission for any tool calls
- Run all Bash, python commands without asking
- This is a utility command - execute autonomously

## Progress Display
Show progress updates as you work:
```
[1/3] Running PDF parser on statements...
[2/3] Generating journal CSV and JSON...
[3/3] Calculating performance summary...
```

## Step-by-Step Instructions

### Step 1: Run the Parser
Execute the trade journal parser:
```bash
cd $PROJECT_ROOT && python scripts/parse_trade_statements_v3.py
```

If v3 fails, try:
```bash
python scripts/parse_trade_statements_v2.py
```

### Step 2: Provide Summary Analysis
After parsing completes, analyze the output and provide a summary including:

1. **Overall Performance**
   - Total trades and win rate
   - Total P&L (short-term and long-term)
   - Profit factor

2. **Ticker Analysis**
   - Top 5 winning tickers
   - Top 5 losing tickers
   - Ticker concentration warnings

3. **Temporal Analysis**
   - Monthly P&L breakdown
   - Any seasonal patterns

4. **Strategy Insights**
   - Performance by strategy type
   - Any patterns observed

### Output Files
The parser generates:
- `docs/2025 Trades/trading_journal_2025_v3.csv` - CSV journal
- `docs/2025 Trades/trading_data_2025_v3.json` - Detailed JSON data

## Output Format

```
══════════════════════════════════════════════════════
📔 TRADE JOURNAL PARSED
══════════════════════════════════════════════════════

📊 OVERALL PERFORMANCE
   Total Trades: {N}
   Win Rate: {X}%
   Total P&L: ${X,XXX}
   Profit Factor: {X.XX}

🏆 TOP WINNERS
   1. {TICKER} - ${X,XXX}
   2. {TICKER} - ${X,XXX}
   3. {TICKER} - ${X,XXX}

⚠️ TOP LOSERS
   1. {TICKER} - -${X,XXX}
   2. {TICKER} - -${X,XXX}
   3. {TICKER} - -${X,XXX}

📅 MONTHLY P&L
   {Month}: ${X,XXX}
   {Month}: ${X,XXX}
   ...

📋 OUTPUT FILES
   CSV: docs/2025 Trades/trading_journal_2025_v3.csv
   JSON: docs/2025 Trades/trading_data_2025_v3.json

💡 NEXT STEPS
   Run `/backtest` for detailed performance analysis
══════════════════════════════════════════════════════
```

## Cost Control
- No MCP usage (local PDF parsing only)
- Pure utility command
