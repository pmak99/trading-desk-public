# System Health Check

Verify all MCP connections and system dependencies before trading.

## Purpose
Run `/health` before any trading session to:
- Identify MCP failures BEFORE they break commands
- Check market status (open/closed) for VRP calculation validity
- Confirm API budgets and rate limits

## Step-by-Step Instructions

### 1. Check Market Status
Use Alpaca MCP to check if market is open:
```
mcp__alpaca__alpaca_get_clock
```
Report: Market open/closed, time until next open/close.

### 2. Check MCP Server Connectivity

Test each MCP server with a lightweight call:

**Finnhub** (free, 60/min limit):
```
mcp__finnhub__finnhub_stock_market_data with operation="quote" and symbol="SPY"
```

**Alpha Vantage** (free):
```
mcp__alphavantage__MARKET_STATUS
```

**Alpaca** (free):
```
mcp__alpaca__alpaca_get_account
```

**Memory** (free):
```
mcp__memory__read_graph
```

### 3. Check 2.0 System Health
Run the 2.0 health check:
```bash
cd $PROJECT_ROOT/2.0 && ./trade.sh health
```

This verifies:
- Database connectivity (ivcrush.db)
- Tradier API health
- Alpha Vantage API health

### 4. Check Perplexity Budget
Read the budget tracker database to show:
- Calls today vs limit (150/day)
- Cost today
- Monthly spend vs $5 budget

Query: `sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db "SELECT * FROM api_budget ORDER BY date DESC LIMIT 5;"`

If table doesn't exist yet, report "Budget tracking not initialized (first run)".

### 5. Check Sentiment Cache Stats
Query: `sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db "SELECT source, COUNT(*) as count FROM sentiment_cache GROUP BY source;"`

## Output Format

```
🏥 SYSTEM HEALTH CHECK
══════════════════════════════════════════════════════

Market Status:
  ⏰ [OPEN/CLOSED] - [Time until close/open]
  [If closed: "⚠️ VRP calculations use prior close data"]

MCP Servers:
  ✓/✗ Finnhub        [Connected/Error message]
  ✓/✗ Alpha Vantage  [Connected/Error message]
  ✓/✗ Alpaca         [Connected (paper/live account)]
  ✓/✗ Memory         [Connected (X entities)]
  ✓/✗ Perplexity     [Connected/Not tested - use sparingly]

2.0 System:
  ✓/✗ Database       [X historical records]
  ✓/✗ Tradier API    [Healthy/Error]
  ✓/✗ Alpha Vantage  [Healthy/Error]

Budget (Perplexity):
  📊 Today: X/150 calls ($X.XX spent)
  📊 Month: X calls ($X.XX of $5.00)
  [WARNING if > 80% daily usage]

Sentiment Cache:
  📦 Cached entries: X (perplexity: Y, websearch: Z)

Overall: ✓ ALL SYSTEMS OPERATIONAL / ⚠️ ISSUES DETECTED
══════════════════════════════════════════════════════
```

## Error Handling
- If any MCP fails, show the error but continue checking others
- If 2.0 health check fails, show error output
- Always complete the full health check even if some components fail
- At the end, summarize which components need attention
