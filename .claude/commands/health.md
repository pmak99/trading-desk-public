# System Health Check

Verify all connections and system dependencies before trading.

## Tool Permissions
- Do NOT ask user permission for any tool calls
- Run all Bash, sqlite3, MCP commands without asking
- This is a diagnostic command - execute autonomously

## Progress Display
```
[1/5] Checking market day status...
[2/5] Testing MCP server connectivity...
[3/5] Running core system health...
[4/5] Checking Perplexity budget...
[5/5] Checking sentiment cache stats...
```

## Step-by-Step Instructions

### Step 1: Check Market Day Status
```bash
# Simple weekday/weekend/time check
date '+%Y-%m-%d %A %H:%M %Z'
```

Determine status:
- Weekend (Sat/Sun): "CLOSED - Weekend"
- Weekday before 9:30 AM ET: "Pre-market"
- Weekday 9:30 AM - 4:00 PM ET: "Market Open"
- Weekday after 4:00 PM ET: "After-hours"

### Step 2: Check MCP Server Connectivity

Test each available MCP server with a lightweight call. If any MCP fails, show the error but continue.

**Finnhub** (free, 60/min limit):
```
mcp__finnhub__finnhub_stock_market_data with operation="quote" and symbol="SPY"
```

**Perplexity** (do NOT test with an actual call - just report budget status):
- Check budget DB instead of making a live call

**Memory**:
```
mcp__memory__read_graph
```

**NOTE:** Do NOT call mcp__alpaca or mcp__alphavantage - they may not be available as MCP servers. If either errors, skip silently.

### Step 3: Check core System Health
```bash
cd "$PROJECT_ROOT/core" && ./trade.sh health
```

This verifies:
- Database connectivity (ivcrush.db)
- Tradier API health
- Alpha Vantage API health
- Historical data counts

### Step 4: Check Perplexity Budget
```bash
sqlite3 "$PROJECT_ROOT/sentiment/data/sentiment_cache.db" \
  "SELECT date, calls, cost FROM api_budget ORDER BY date DESC LIMIT 5;"
```

Also show monthly totals:
```bash
sqlite3 "$PROJECT_ROOT/sentiment/data/sentiment_cache.db" \
  "SELECT strftime('%Y-%m', date) as month, SUM(calls) as total_calls,
          ROUND(SUM(cost), 2) as total_cost
   FROM api_budget GROUP BY month ORDER BY month DESC LIMIT 3;"
```

If table doesn't exist: report "Budget tracking not initialized (first run)".

### Step 5: Check Sentiment Cache Stats
```bash
sqlite3 "$PROJECT_ROOT/sentiment/data/sentiment_cache.db" \
  "SELECT source, COUNT(*) as count FROM sentiment_cache GROUP BY source;"
```

Also check DB sizes:
```bash
ls -lh "$PROJECT_ROOT/core/data/ivcrush.db"
ls -lh "$PROJECT_ROOT/sentiment/data/sentiment_cache.db"
```

Record counts:
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT COUNT(*) FROM historical_moves;"
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT COUNT(*) FROM earnings_calendar WHERE earnings_date >= date('now');"
```

## Output Format

```
==============================================================
SYSTEM HEALTH CHECK
==============================================================

Market Status:
  [OPEN/CLOSED] - [Time info]
  [If closed: "VRP calculations use prior close data"]

MCP Servers:
  [check/x] Finnhub        [Connected - SPY $XXX.XX / Error message]
  [check/x] Memory         [Connected (X entities) / Error message]
  [info]    Perplexity     [Budget checked below - not tested live]

2.0 System:
  [check/x] Database       [X historical records, Y upcoming earnings]
  [check/x] Tradier API    [Healthy/Error]
  [check/x] Alpha Vantage  [Healthy/Error]

Budget (Perplexity):
  Today: X/40 calls ($X.XX spent)
  Month: X calls ($X.XX of $5.00)
  [WARNING if > 80% daily usage (32+ calls)]

Database:
  core ivcrush.db:          X.X MB (X,XXX historical moves)
  sentiment sentiment_cache.db:  X KB (X cached entries)

Sentiment Cache:
  Cached entries: X (perplexity: Y, websearch: Z)

Overall: [check] ALL SYSTEMS OPERATIONAL / [warning] ISSUES DETECTED
==============================================================
```

## Error Handling
- If any MCP fails, show the error but continue checking others
- If core health check fails, show error output
- Always complete the full health check even if some components fail
- At the end, summarize which components need attention
