# MCP Servers Usage Guide for IV Crush Trading System

## Current API Integrations

The 2.0 system uses the following data sources directly (not via MCP):

| API | Status | Purpose | Rate Limits |
|-----|--------|---------|-------------|
| **Tradier** | Active | Option chains, real-time IV, Greeks | 10/sec, 120/min |
| **Alpha Vantage** | Active | Earnings calendar, price history | 5/min, 500/day |
| **yFinance** | Active (fallback) | Historical prices, options data | Unlimited |
| **Reddit (PRAW)** | Active | Earnings whisper sentiment | Authenticated |

## Optional MCP Servers

These MCP servers can be used as supplementary data sources in Claude Code:

| Server | Status | Purpose |
|--------|--------|---------|
| **Alpha Vantage** | Available | Options data, technical indicators |
| **Yahoo Finance** | Available | Options chains, earnings dates, real-time quotes |
| **Sequential Thinking** | Available | Complex strategy logic, multi-step reasoning |
| **Alpaca** | Available | Execute paper trades, test strategies |

## Daily API Budget Strategy

### Priority Usage Order:
1. **Tradier** (primary) - Professional-grade IV data via ORATS
2. **Alpha Vantage** (25/day free) - Earnings calendar, fallback prices
3. **yFinance** (unlimited) - Bulk historical data, backup options
4. **Yahoo Finance MCP** (unlimited) - Alternative for exploratory data

### Recommended Workflow

```
Morning Research (9:00 AM):
1. Tradier: Fetch option chains for candidates (via trade.sh)
2. Alpha Vantage: Get earnings calendar (via trade.sh)
3. Yahoo Finance MCP: Exploratory data if needed

Pre-Trade Analysis:
1. Run ./trade.sh TICKER DATE for complete VRP analysis
2. Use Sequential Thinking MCP for complex multi-leg strategies
3. Alpha Vantage direct: Reserved for specific IV data

Strategy Validation:
1. Sequential Thinking MCP: Multi-step risk assessment
2. Alpaca MCP: Paper trade execution
```

## Example MCP Queries

### 1. Yahoo Finance - Options & Earnings
```
Use the Yahoo Finance MCP to:
- Get all earnings dates for this week
- Fetch complete options chain for AAPL expiring in 7 days
- Get current stock price and recent price history
```

### 2. Sequential Thinking - Strategy Logic
```
Use the Sequential Thinking MCP to:
- Analyze multi-leg options strategies
- Break down complex trade decision trees
- Evaluate risk/reward scenarios step-by-step
```

### 3. Alpaca - Paper Trading
```
Use the Alpaca MCP to:
- Place paper trades to test your strategy
- Get account balance and positions
- Check open orders and fill status
- Monitor P&L without risking real money
```

## Integration with Existing APIs

Your system already uses these APIs directly (configured in .env):
- **Tradier**: Primary options data source (circuit breaker protected)
- **Alpha Vantage**: Earnings calendar (rate limited)
- **Reddit API**: Social sentiment for whisper mode

### Recommended Hybrid Approach

| Data Type | Source | Reason |
|-----------|--------|--------|
| Option chains | Tradier (direct) | Professional-grade IV via ORATS |
| Earnings dates | Alpha Vantage (direct) | NASDAQ vendor data |
| Historical prices | yFinance (direct) | Unlimited, reliable |
| Complex reasoning | Sequential Thinking MCP | Better multi-step logic |
| Paper trading | Alpaca MCP | Test strategies |

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

2. **Rate limit reached (Alpha Vantage)**
   - 5 requests per minute, 500 per day on free tier
   - Switch to Yahoo Finance MCP or yFinance for remaining queries

3. **Tradier API errors**
   - Check TRADIER_API_KEY in .env
   - Circuit breaker may have opened after 5 failures (recovers after 60s)

## API Keys Location

Environment variables are set in your `.env` file:
- `TRADIER_API_KEY` - Tradier options data
- `ALPHA_VANTAGE_KEY` - Earnings calendar

To view MCP configuration: `~/.claude.json`

## Best Practices

1. **Use trade.sh for primary analysis**
   - Handles all API calls with retry logic and caching
   - Auto-backfills missing historical data

2. **Reserve Alpha Vantage calls**
   - Plan what you need before querying
   - Use yFinance for exploratory historical data

3. **Use Sequential Thinking for complex decisions**
   - Trade setup validation
   - Risk assessment workflows
   - Multi-criteria analysis

4. **Leverage built-in caching**
   - L1 memory cache (30s TTL)
   - L2 SQLite cache (5min TTL)
   - Hybrid cache survives restarts

## Notes

The 2.0 system is self-contained and uses only Tradier and Alpha Vantage APIs for core VRP analysis. MCP servers are optional supplementary tools for advanced workflows.
