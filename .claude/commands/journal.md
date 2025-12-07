# Parse Trade Journal

Parse Fidelity monthly statements and generate trading journal with P&L analysis.

Run the trade journal parser:

```bash
cd $PROJECT_ROOT && python scripts/parse_trade_statements_v3.py
```

After running, provide a summary including:
1. Total trades and win rate
2. Total P&L (short-term and long-term)
3. Top 5 winning tickers
4. Top 5 losing tickers
5. Monthly P&L breakdown
6. Any patterns observed (e.g., strategy performance, ticker concentration)

The parser outputs:
- `docs/2025 Trades/trading_journal_2025_v3.csv` - CSV journal
- `docs/2025 Trades/trading_data_2025_v3.json` - Detailed JSON data
