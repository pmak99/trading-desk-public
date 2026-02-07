# Prime System - Pre-Cache Sentiment

Pre-cache sentiment for the week's most anticipated earnings. Run once in the morning to make all other commands instant.

## Arguments
$ARGUMENTS (format: [DATE] - optional, defaults to current week)

Examples:
- `/prime` - Prime for current week's most anticipated
- `/prime 2026-02-10` - Prime for week containing that date

## Purpose
Run `/prime` once at 7-8 AM before market open:
- Caches sentiment for most anticipated earnings (whisper list)
- All subsequent `/whisper`, `/analyze`, `/alert` commands hit cache instantly
- Predictable daily cost (you control when to spend API budget)

## Tool Permissions
- Do NOT ask user permission for any tool calls EXCEPT mcp__perplexity__* calls
- Run all Bash, sqlite3, Glob, Grep, Read commands without asking
- Only pause for Perplexity calls to confirm API usage

## Progress Display
```
[1/5] Determining target week...
[2/5] Running whisper scan...
[3/5] Filtering qualified tickers (VRP >= 1.8x, non-REJECT)...
[4/5] Fetching sentiment for N tickers...
      check TICKER1 - Perplexity (VRP X.Xx)
      check TICKER2 - Perplexity (VRP X.Xx)
[5/5] Caching results and updating budget...
```

## Step-by-Step Instructions

### Step 1: Parse Date Argument
- If no date provided, use current week
- IMPORTANT: Get actual current date from system, not assumptions

**Calculate target Monday:**
```bash
DAY_NUM=$(date '+%u')
if [ $DAY_NUM -ge 5 ]; then
    DAYS_TO_NEXT_MONDAY=$((8 - DAY_NUM))
    TARGET_MONDAY=$(date -v+${DAYS_TO_NEXT_MONDAY}d '+%Y-%m-%d' 2>/dev/null || date -d "+${DAYS_TO_NEXT_MONDAY} days" '+%Y-%m-%d')
else
    DAYS_SINCE_MONDAY=$((DAY_NUM - 1))
    TARGET_MONDAY=$(date -v-${DAYS_SINCE_MONDAY}d '+%Y-%m-%d' 2>/dev/null || date -d "-${DAYS_SINCE_MONDAY} days" '+%Y-%m-%d')
fi
```

### Step 2: Check Market Status
```bash
DAY_OF_WEEK=$(date '+%A')
```

**Weekend/Holiday handling:**
- Weekend: Show "Weekend - Skipping Perplexity to save budget" and skip sentiment fetch. Still show whisper results for planning.
- Holiday: Same as weekend
- Weekday: Continue with full priming

### Step 3: Run 2.0 Whisper Mode
```bash
cd "$PROJECT_ROOT/2.0" && ./trade.sh whisper $TARGET_MONDAY
```

**IMPORTANT:** Always pass the date argument to whisper. Without it, whisper may default to the wrong week.

### Step 4: Filter Qualified Tickers
From whisper results, filter to tickers where:
- VRP >= 1.8x (EXCELLENT tier - discovery threshold for priming)
- Liquidity != REJECT

### Step 5: Check Budget Status
```bash
sqlite3 "$PROJECT_ROOT/4.0/data/sentiment_cache.db" \
  "SELECT COALESCE((SELECT calls FROM api_budget WHERE date='$(date +%Y-%m-%d)'), 0) as calls;"
```

If near budget limit (>35 calls):
```
Budget warning: {calls}/40 calls used today
   Limiting priming to top {remaining} tickers
```

### Step 6: Fetch Sentiment for Each Qualified Ticker

For EACH qualified ticker (in order of VRP score):

**6a. Check if already cached:**
```bash
TICKER=$(echo "$RAW_TICKER" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')

sqlite3 "$PROJECT_ROOT/4.0/data/sentiment_cache.db" \
  "SELECT 1 FROM sentiment_cache WHERE ticker='$TICKER' AND date='$EARNINGS_DATE'
   AND cached_at > datetime('now', '-3 hours');"
```
If exists: skip, mark as "already cached"

**6b. If cache miss, fetch via fallback chain:**

1. **Try Perplexity (if budget OK, < 40 calls):**
   ```
   mcp__perplexity__perplexity_ask with query="For {TICKER} earnings on {DATE}, respond ONLY in this format:
   Direction: [bullish/bearish/neutral]
   Score: [number -1 to +1]
   Catalysts: [2 bullets, max 10 words each]
   Risks: [1 bullet, max 10 words]"
   ```
   If success: cache with source="perplexity", increment budget

2. **Try search (fallback):**
   ```
   mcp__perplexity__perplexity_search with query="{TICKER} earnings sentiment analyst rating {DATE}"
   ```
   If success: cache with source="websearch"

3. If all fail: mark as "sentiment unavailable"

**6c. Save to sentiment_history (permanent storage for backtesting):**
```bash
sqlite3 "$PROJECT_ROOT/4.0/data/sentiment_cache.db" \
  "INSERT OR REPLACE INTO sentiment_history
   (ticker, earnings_date, collected_at, source, sentiment_text,
    sentiment_score, sentiment_direction, vrp_ratio, implied_move_pct, updated_at)
   VALUES ('$TICKER', '$DATE', datetime('now'), '$SOURCE', '$SENTIMENT_TEXT',
           $SCORE, '$DIRECTION', $VRP_RATIO, $IMPLIED_MOVE, datetime('now'));"
```

**6d. Display progress per ticker:**
```
  check LULU  - Perplexity (VRP 5.27x, Feb 11)
  check RH    - Perplexity (VRP 4.14x, Feb 11)
  check ORCL  - search fallback (VRP 3.87x, Feb 10)
  skip  TOL   - already cached
  x     CIEN  - sentiment unavailable
```

### Step 7: Update Budget Tracker
```bash
sqlite3 "$PROJECT_ROOT/4.0/data/sentiment_cache.db" \
  "INSERT INTO api_budget (date, calls, cost, last_updated)
   VALUES ('$(date +%Y-%m-%d)', $NEW_CALLS, $NEW_COST, datetime('now'))
   ON CONFLICT(date) DO UPDATE SET
     calls = calls + $NEW_CALLS,
     cost = cost + $NEW_COST,
     last_updated = datetime('now');"
```

## Output Format

```
==============================================================
PRIMING SYSTEM - Week of {DATE}
==============================================================

Market Status: [status message]

WHISPER RESULTS
   Most anticipated: {N} tickers
   VRP >= 1.8x qualified: {M} tickers
   Liquidity REJECT: {R} tickers (excluded)

FETCHING SENTIMENT
   check LULU  - Perplexity (VRP 5.27x, Feb 11)
   check RH    - Perplexity (VRP 4.14x, Feb 11)
   check ORCL  - Perplexity (VRP 3.87x, Feb 10)
   check AVGO  - search (VRP 3.19x, Feb 11)
   skip  TOL   - already cached

PRIMING COMPLETE
   New caches:     4
   Cache hits:     1 (skipped)
   Failures:       0
   API calls today: 4/40
   Monthly budget: $4.95 left

System primed! All commands will use cached sentiment.

NEXT STEPS
   Run /whisper to see ranked opportunities (instant)
   Run /analyze TICKER for full analysis (instant sentiment)
==============================================================
```

## Cost Control
- Uses whisper mode (most anticipated) instead of full scan
- Only primes VRP >= 1.8x tickers (EXCELLENT tier)
- Skips already-cached tickers (no duplicate calls)
- Skips non-trading days entirely (save budget)
- Typically 5-12 calls per week depending on whisper list

## Weekend/Holiday Handling
- Detects via simple day-of-week check
- Skips Perplexity calls on non-trading days
- Still shows whisper results for planning
- Suggests priming on next trading day
