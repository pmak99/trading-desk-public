# Prime System - Pre-Cache Sentiment

Pre-cache sentiment for today's earnings - run once in the morning to make all other commands instant.

## Arguments
$ARGUMENTS (format: [DATE] - optional, defaults to today)

Examples:
- `/prime` - Prime for today's earnings
- `/prime 2025-12-09` - Prime for specific date

## Purpose
Run `/prime` once at 7-8 AM before market open:
- All subsequent `/whisper`, `/analyze`, `/alert` commands hit cache instantly
- Predictable daily cost (you control when to spend API budget)
- No waiting for Perplexity during trading hours

## Step-by-Step Instructions

### Step 1: Parse Date Argument
- If no date provided, use today's date
- Format: YYYY-MM-DD

### Step 2: Check Market Status (Alpaca MCP)
```
mcp__alpaca__alpaca_get_clock
```

Detect non-trading days:
- If `is_open=false` AND weekend/holiday:
  ```
  ⚠️ No trading today ({reason})
     Skipping Perplexity calls to save budget.
     Showing last trading day data for reference.
  ```
  → Skip Steps 5-6, just display scan results

- If `is_open=false` but regular pre-market:
  ```
  ⚠️ Market closed - VRP uses prior close data
     Options data will refresh after 9:30 AM ET
  ```
  → Continue with priming

### Step 3: Run 2.0 Scan for Date
Execute scan to get all earnings for the date:
```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && ./trade.sh scan $DATE
```

This provides:
- All tickers with earnings on date
- VRP ratios and tiers
- Liquidity grades
- Quality scores

### Step 4: Filter Qualified Tickers
From scan results, filter to tickers where:
- VRP >= 4.0x (GOOD or EXCELLENT tier)
- Liquidity != REJECT

These are worth caching sentiment for.

### Step 5: Check Budget Status
```bash
sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
  "SELECT COALESCE((SELECT calls FROM api_budget WHERE date='$(date +%Y-%m-%d)'), 0) as calls;"
```

If near budget limit (>120 calls), warn:
```
⚠️ Budget warning: {calls}/150 calls used today
   Limiting priming to top {remaining} tickers
```

### Step 6: Fetch Sentiment for Each Qualified Ticker

For EACH qualified ticker (in order of VRP score):

**6a. Check if already cached:**
```bash
sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
  "SELECT 1 FROM sentiment_cache WHERE ticker='$TICKER' AND date='$DATE' AND cached_at > datetime('now', '-3 hours');"
```
If exists → skip, mark as "○ already cached"

**6b. If cache miss, fetch via fallback chain:**

1. **Try Perplexity (if budget OK):**
   ```
   mcp__perplexity__perplexity_ask with query="What is the current sentiment and analyst consensus for {TICKER} ahead of their earnings? Include recent news, analyst upgrades/downgrades, whisper numbers, and any concerns or catalysts."
   ```
   - If success: cache with source="perplexity", increment budget counter
   - If fail: continue to WebSearch

2. **Try WebSearch (fallback):**
   ```
   WebSearch with query="{TICKER} earnings sentiment analyst rating {DATE}"
   ```
   - If success: cache with source="websearch"
   - If fail: mark as "✗ sentiment unavailable"

**6c. Save to sentiment_history (permanent storage for backtesting):**
After each successful fetch, also save to the permanent history table:
```bash
sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
  "INSERT OR REPLACE INTO sentiment_history
   (ticker, earnings_date, collected_at, source, sentiment_text,
    sentiment_score, sentiment_direction, vrp_ratio, implied_move_pct, updated_at)
   VALUES ('$TICKER', '$DATE', datetime('now'), '$SOURCE', '$SENTIMENT_TEXT',
           $SCORE, '$DIRECTION', $VRP_RATIO, $IMPLIED_MOVE, datetime('now'));"
```

Score sentiment_direction as:
- "bullish" if clearly positive (analyst upgrades, beat expectations, positive catalysts)
- "bearish" if clearly negative (downgrades, concerns, negative catalysts)
- "neutral" if mixed or unclear

This builds a permanent dataset for validating AI sentiment value-add.

**6d. Display progress:**
```
  ✓ NVDA - Perplexity (VRP 8.2x)
  ✓ AMD  - Perplexity (VRP 6.1x)
  ✓ AVGO - WebSearch fallback (VRP 5.4x)
  ○ ORCL - already in cache
  ✗ MU   - sentiment unavailable
```

### Step 7: Update Budget Tracker
After all fetches, record total calls made:
```bash
sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
  "INSERT OR REPLACE INTO api_budget (date, calls, cost, last_updated)
   VALUES ('$DATE', (SELECT COALESCE(calls,0) FROM api_budget WHERE date='$DATE') + $NEW_CALLS,
           (SELECT COALESCE(cost,0) FROM api_budget WHERE date='$DATE') + $NEW_COST,
           datetime('now'));"
```

## Output Format

```
══════════════════════════════════════════════════════
🔄 PRIMING SYSTEM FOR {DATE}
══════════════════════════════════════════════════════

⚠️ Market Status: [status message if relevant]

📊 SCAN RESULTS
   Earnings found: {N} tickers
   VRP > 4x qualified: {M} tickers
   Liquidity REJECT: {R} tickers (excluded)

🔄 FETCHING SENTIMENT
   ✓ NVDA - Perplexity (VRP 8.2x)
   ✓ AMD  - Perplexity (VRP 6.1x)
   ✓ AVGO - WebSearch (VRP 5.4x)
   ○ ORCL - already cached
   ○ MU   - already cached

📦 PRIMING COMPLETE
   ┌────────────────────────────────┐
   │ New caches:     3              │
   │ Cache hits:     2 (skipped)    │
   │ Failures:       0              │
   │                                │
   │ API calls today: 8/150         │
   │ Budget remaining: $4.76        │
   └────────────────────────────────┘

✅ System primed! All commands will use cached sentiment.

💡 NEXT STEPS
   Run `/whisper` to see most anticipated (instant)
   Run `/analyze TICKER` for full analysis (instant sentiment)
══════════════════════════════════════════════════════
```

## Cost Control
- Only primes VRP >= 4x tickers (no wasted calls on low-edge trades)
- Skips already-cached tickers (no duplicate calls)
- Skips non-trading days entirely (save budget)
- Shows budget status after completion
- Typically 3-8 calls per day depending on earnings density

## Weekend/Holiday Handling
- Detects via `mcp__alpaca__alpaca_get_clock`
- Skips Perplexity calls on non-trading days
- Still shows scan results for planning
- Suggests priming on next trading day
