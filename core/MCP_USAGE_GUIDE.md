# MCP Servers Usage Guide for IV Crush Trading System

## Installed MCP Servers

| Server | Status | API Limit | Purpose |
|--------|--------|-----------|---------|
| **Alpha Vantage** | Connected | 25 req/day | Options data, IV analysis, technical indicators |
| **Yahoo Finance** | Connected | Unlimited | Options chains, earnings dates, real-time quotes |
| **Sequential Thinking** | Connected | Unlimited | Complex strategy logic, multi-step reasoning |
| **Octagon AI** | Connected | 2-week Pro trial | Earnings transcripts, SEC filings, 13F holdings |
| **Composer Trade** | Needs auth | Free backtesting | Strategy backtesting (saving requires $5/mo) |
| **Alpaca** | Connected | Paper trading | Execute paper trades, test strategies |

## Daily API Budget Strategy

### Priority Usage Order:
1. **Yahoo Finance** (unlimited) - Use for bulk data fetching
2. **Sequential Thinking** (unlimited) - Use for complex analysis
3. **Octagon AI** (trial) - Use for earnings research
4. **Alpha Vantage** (25/day) - Reserve for specific IV data
5. **Composer** - Use for backtesting strategies

### Recommended Workflow

```
Morning Research (9:00 AM):
1. Yahoo Finance: Get upcoming earnings calendar
2. Yahoo Finance: Fetch options chains for candidates
3. Sequential Thinking: Analyze IV patterns and setup criteria

Pre-Trade Analysis:
1. Octagon AI: Review earnings transcripts from past quarters
2. Octagon AI: Check SEC filings and institutional holdings
3. Alpha Vantage: Get precise IV rank/percentile data

Strategy Validation:
1. Composer: Backtest the IV crush strategy
2. Sequential Thinking: Multi-step risk assessment
```

## Example MCP Queries

### 1. Alpha Vantage - IV Analysis
```
Use the Alpha Vantage MCP to:
- Get implied volatility rank for NVDA
- Fetch historical IV data for the past 30 days
- Calculate IV percentile relative to 1-year range
```

### 2. Yahoo Finance - Options & Earnings
```
Use the Yahoo Finance MCP to:
- Get all earnings dates for this week
- Fetch complete options chain for AAPL expiring in 7 days
- Get current stock price and recent price history
```

### 3. Octagon AI - Research
```
Use the Octagon AI MCP to:
- Get earnings transcript summary for TSLA Q3 2024
- Find institutional ownership changes (13F filings)
- Search SEC filings for revenue guidance mentions
```

### 4. Sequential Thinking - Strategy Logic
```
Use the Sequential Thinking MCP to:
- Analyze multi-leg options strategies
- Break down complex trade decision trees
- Evaluate risk/reward scenarios step-by-step
```

### 5. Composer Trade - Backtesting
```
Use the Composer MCP to:
- Backtest "sell puts on IV crush" strategy
- Test historical performance of your criteria
- Validate win rate and profit factor
```

### 6. Alpaca - Paper Trading
```
Use the Alpaca MCP to:
- Place paper trades to test your strategy
- Get account balance and positions
- Check open orders and fill status
- Monitor P&L without risking real money
```

## Integration with Existing APIs

Your system already uses these APIs directly:
- **Tradier**: Brokerage execution (keep as-is)
- **Alpha Vantage**: Can use MCP or direct API
- **Yahoo Finance**: Can use MCP or yfinance library
- **Reddit API**: Social sentiment (keep as-is)
- **Perplexity API**: AI search (keep as-is)
- **Gemini AI**: Analysis (keep as-is)

### Recommended Hybrid Approach

| Data Type | Source | Reason |
|-----------|--------|--------|
| Earnings dates | Yahoo Finance MCP | Unlimited calls |
| Options chains | Yahoo Finance MCP | Unlimited, real-time |
| IV rank/percentile | Alpha Vantage (direct) | More control over rate limits |
| Order execution | Tradier (direct) | Keep brokerage separate |
| Earnings transcripts | Octagon AI MCP | Specialized analysis |
| Complex reasoning | Sequential Thinking MCP | Better multi-step logic |

## Troubleshooting

### Check MCP Status
```bash
claude
/mcp
```

### Common Issues

1. **"Failed to connect"**
   - MCP server needs npm package download on first use
   - Wait 30-60 seconds and try again

2. **"Needs authentication" (Composer)**
   - Free backtesting works without login
   - Only need auth to save/share backtests

3. **Rate limit reached (Alpha Vantage)**
   - 25 requests per day on free tier
   - Switch to Yahoo Finance MCP for remaining queries

## API Keys Location

Environment variables are set in your MCP configuration:
- `OCTAGON_API_KEY` - Octagon AI access
- Alpha Vantage key is embedded in the URL

To update keys, edit: `~/.claude.json`

## Best Practices

1. **Batch your Alpha Vantage requests**
   - Plan what you need before querying
   - Use Yahoo Finance for exploratory data

2. **Use Sequential Thinking for complex decisions**
   - Trade setup validation
   - Risk assessment workflows
   - Multi-criteria analysis

3. **Cache frequently used data**
   - Your existing cache infrastructure handles this
   - MCPs don't have built-in caching

4. **Monitor your Octagon trial**
   - You have 2 weeks of Pro access
   - Prioritize earnings transcript research

## Notes

### FMP (Financial Modeling Prep)
The FMP MCP runs as HTTP server on port 8080 which can conflict with other services. For fundamental data, use Alpha Vantage MCP or direct API calls to FMP.
