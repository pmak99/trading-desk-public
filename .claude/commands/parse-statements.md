# Parse Broker Statements (PDF Parsing Skill)

Parse Fidelity monthly brokerage statements and extract trading data for P&L analysis.

## What This Does
- Extracts all options and stock trades from PDF statements
- Calculates realized gains/losses (short-term and long-term)
- Generates CSV journal and JSON data files
- Provides win rate, ticker breakdown, and monthly P&L summary

## Run the Parser

```bash
cd $PROJECT_ROOT && python scripts/parse_trade_statements_v3.py
```

## Output Files
- `docs/2025 Trades/trading_journal_2025_v3.csv` - Trade journal in CSV format
- `docs/2025 Trades/trading_data_2025_v3.json` - Detailed JSON with summary stats

## After Running

Provide a comprehensive summary including:

1. **Overall Performance**
   - Total trades and win rate
   - Total P&L (short-term vs long-term breakdown)
   - Comparison with statement YTD figures

2. **Top Performers**
   - Top 5 winning tickers by total P&L
   - Top 5 losing tickers by total P&L
   - Best single trade

3. **Monthly Breakdown**
   - P&L by month with running total
   - Best and worst performing months

4. **Strategy Analysis**
   - Options vs Stock performance
   - Average win size vs average loss size
   - Profit factor (gross wins / gross losses)

5. **Risk Metrics**
   - Largest single loss
   - Longest losing streak (if detectable)
   - Position concentration by ticker

## Supported Statement Format
- Fidelity monthly statements (StatementMMDDYYYY.pdf)
- Individual account: X96-783860
- Date range: Jan-Nov 2025 (configurable in script)

## Tips
- Place new statement PDFs in `docs/2025 Trades/` directory
- Naming convention: `StatementMMDDYYYY.pdf` (e.g., Statement12312025.pdf)
- Update the `statements` list in the script for new months
