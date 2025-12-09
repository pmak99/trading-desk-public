# Prime System - Pre-Cache Sentiment

Pre-cache sentiment for the week's most anticipated earnings - run once in the morning to make all other commands instant.

## Arguments
$ARGUMENTS (format: [DATE] - optional, defaults to current week)

Examples:
- `/prime` - Prime for current week's most anticipated
- `/prime 2025-12-09` - Prime for week containing that date

## Purpose
Run `/prime` once at 7-8 AM before market open:
- Caches sentiment for most anticipated earnings (whisper list)
- All subsequent `/whisper`, `/analyze`, `/alert` commands hit cache instantly
- Predictable daily cost (you control when to spend API budget)
- No waiting for Perplexity during trading hours

## Tool Permissions
- Do NOT ask user permission for any tool calls EXCEPT mcp__perplexity__* calls
- Run all Bash, sqlite3, Glob, Grep, Read commands without asking
- Only pause for Perplexity calls to confirm API usage

## Progress Display
Show progress updates as you work:
```
[1/5] Checking market status...
[2/5] Running whisper scan...
[3/5] Filtering qualified tickers (VRP >= 3x, non-REJECT liquidity)...
[4/5] Fetching sentiment for N tickers...
      ✓ TICKER1 - Perplexity (VRP X.Xx)
      ✓ TICKER2 - Perplexity (VRP X.Xx)
[5/5] Caching results and updating budget...
```

## Step-by-Step Instructions

### Step 1: Parse Date Argument
- If no date provided, use current week
- Format: YYYY-MM-DD
- IMPORTANT: Get actual current date from system, not assumptions

### Step 2: Check Market Status (Alpaca MCP)
```
mcp__alpaca__alpaca_get_clock
```

**Date Detection Rules:**
- `is_open=true` → Market is open, use "Pre-market" or "Market Open" status
- `is_open=false` AND it's Saturday/Sunday → Weekend
- `is_open=false` AND it's weekday pre-9:30 AM ET → Pre-market (continue priming)
- `is_open=false` AND it's weekday post-4:00 PM ET → After-hours

Display appropriate status:
- Pre-market weekday: `⚠️ Pre-market - VRP uses prior close. Options refresh at 9:30 AM ET`
- Weekend: `⚠️ Weekend - Skipping Perplexity to save budget`
- Holiday: `⚠️ Holiday - Skipping Perplexity to save budget`

### Step 3: Run 2.0 Whisper Mode
Execute whisper to get the week's most anticipated earnings:
```bash
cd $PROJECT_ROOT/2.0 && ./trade.sh whisper
```

This provides:
- Most anticipated tickers from Earnings Whispers
- VRP ratios and tiers for each
- Liquidity grades
- Quality scores
- Earnings dates for the week

### Step 4: Filter Qualified Tickers
From whisper results, filter to tickers where:
- VRP >= 3.0x (discovery threshold for sentiment priming)
- Liquidity != REJECT

Note: 3x is the discovery threshold for priming. Position sizing still uses 4x rule.

### Step 5: Check Budget Status
```bash
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "SELECT COALESCE((SELECT calls FROM api_budget WHERE date='$(date +%Y-%m-%d)'), 0) as calls;"
```

If near budget limit (>35 calls), warn:
```
⚠️ Budget warning: {calls}/40 calls used today
   Limiting priming to top {remaining} tickers
```

### Step 6: Fetch Sentiment for Each Qualified Ticker

For EACH qualified ticker (in order of VRP score):

**6a. Check if already cached:**
```bash
# Sanitize ticker (alphanumeric only, uppercase)
TICKER=$(echo "$TICKER" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')

sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "SELECT 1 FROM sentiment_cache WHERE ticker='$TICKER' AND date='$EARNINGS_DATE' AND cached_at > datetime('now', '-3 hours');"
```
If exists → skip, mark as "○ already cached"

**6b. If cache miss, fetch via fallback chain:**

1. **Try Perplexity (if budget OK, < 40 calls):**
   ```
   mcp__perplexity__perplexity_ask with query="For {TICKER} earnings on {DATE}, respond ONLY in this format:
   Direction: [bullish/bearish/neutral]
   Score: [number -1 to +1]
   Catalysts: [2 bullets, max 10 words each]
   Risks: [1 bullet, max 10 words]"
   ```
   - If success: cache with source="perplexity", increment budget counter
   - If fail: continue to WebSearch

2. **Try WebSearch (fallback):**
   ```
   WebSearch with query="{TICKER} earnings sentiment analyst rating December 2025"
   ```
   - Summarize results into same structured format above
   - If success: cache with source="websearch"
   - If fail: mark as "✗ sentiment unavailable"

**6c. Save to sentiment_history (permanent storage for backtesting):**
After each successful fetch, also save to the permanent history table:
```bash
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
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
  ✓ LULU  - Perplexity (VRP 5.27x, Dec 11)
  ✓ RH    - Perplexity (VRP 4.14x, Dec 11)
  ✓ ORCL  - Perplexity (VRP 3.87x, Dec 10)
  ✓ AVGO  - WebSearch (VRP 3.19x, Dec 11)
  ○ TOL   - already cached
  ✗ CIEN  - sentiment unavailable
```

### Step 7: Update Budget Tracker
After all fetches, record total calls made (use UPDATE then INSERT for safety):
```bash
# First try UPDATE (if row exists)
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "UPDATE api_budget SET calls = calls + $NEW_CALLS, cost = cost + $NEW_COST, last_updated = datetime('now') WHERE date = '$DATE';"

# If no row was updated, INSERT a new one
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "INSERT OR IGNORE INTO api_budget (date, calls, cost, last_updated) VALUES ('$DATE', $NEW_CALLS, $NEW_COST, datetime('now'));"
```

## Output Format

```
══════════════════════════════════════════════════════
🔄 PRIMING SYSTEM - WHISPER MODE (Week of {DATE})
══════════════════════════════════════════════════════

⚠️ Market Status: [status message if relevant]

📊 WHISPER RESULTS
   Most anticipated: {N} tickers
   VRP >= 3x qualified: {M} tickers
   Liquidity REJECT: {R} tickers (excluded)

🔄 FETCHING SENTIMENT
   ✓ LULU  - Perplexity (VRP 5.27x, Dec 11)
   ✓ RH    - Perplexity (VRP 4.14x, Dec 11)
   ✓ ORCL  - Perplexity (VRP 3.87x, Dec 10)
   ✓ AVGO  - WebSearch (VRP 3.19x, Dec 11)
   ○ TOL   - already cached


📦 PRIMING COMPLETE
   ┌────────────────────────────────┐
   │ New caches:     4              │
   │ Cache hits:     1 (skipped)    │
   │ Failures:       0              │
   │                                │
   │ API calls today: 4/40          │
   │ Monthly budget: $4.95 left     │
   └────────────────────────────────┘

✅ System primed! All commands will use cached sentiment.

💡 NEXT STEPS
   Run `/whisper` to see ranked opportunities (instant)
   Run `/analyze TICKER` for full analysis (instant sentiment)
══════════════════════════════════════════════════════
```

## Cost Control
- Uses whisper mode (most anticipated) instead of full scan
- Only primes VRP >= 3x tickers (discovery threshold)
- Position sizing still uses 4x rule (3x is for discovery, not full sizing)
- Skips already-cached tickers (no duplicate calls)
- Skips non-trading days entirely (save budget)
- Shows budget status after completion
- Typically 5-12 calls per week depending on whisper list

## Weekend/Holiday Handling
- Detects via `mcp__alpaca__alpaca_get_clock`
- Skips Perplexity calls on non-trading days
- Still shows whisper results for planning
- Suggests priming on next trading day
