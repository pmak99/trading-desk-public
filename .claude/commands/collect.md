# Collect Sentiment Data

Explicitly collect and store sentiment for a ticker to build the historical dataset for backtesting.

## Arguments
$ARGUMENTS (format: TICKER [DATE] - ticker required, date optional defaults to next earnings)

Examples:
- `/collect NVDA` - Collect sentiment for NVDA's next earnings
- `/collect ORCL 2026-02-10` - Collect sentiment for ORCL earnings on Feb 10

## Tool Permissions
- Do NOT ask user permission for any tool calls EXCEPT mcp__perplexity__* calls
- Run all Bash, sqlite3, search commands without asking
- Only pause for Perplexity calls to confirm API usage

## Progress Display
```
[1/5] Parsing arguments...
[2/5] Getting VRP context from 2.0...
[3/5] Checking existing records...
[4/5] Fetching sentiment via fallback chain...
[5/5] Saving to sentiment history...
```

## Purpose
Build a permanent sentiment dataset for validating AI value-add:
- Collects pre-earnings sentiment
- Stores with VRP context (ratio, implied move)
- Later backfilled with actual outcomes via `/backfill`
- Enables correlation analysis after 30+ records

## Step-by-Step Instructions

### Step 1: Parse Arguments
- TICKER is required (uppercase)
- DATE is optional, format YYYY-MM-DD
- If no date, look up next earnings date

```bash
TICKER=$(echo "$RAW_TICKER" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')

# Auto-detect earnings date if not provided
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT earnings_date, timing FROM earnings_calendar
   WHERE ticker='$TICKER' AND earnings_date >= date('now')
   ORDER BY earnings_date ASC LIMIT 1;"
```

### Step 2: Get Ticker Context from 2.0
```bash
cd "$PROJECT_ROOT/core" && ./trade.sh $TICKER $DATE 2>&1 | head -100
```

Extract from output:
- VRP Ratio (e.g., 3.87x)
- Implied Move % (e.g., 10.96%)
- Liquidity Tier

### Step 3: Check if Already Collected
```bash
sqlite3 "$PROJECT_ROOT/sentiment/data/sentiment_cache.db" \
  "SELECT collected_at, source, sentiment_direction FROM sentiment_history
   WHERE ticker='$TICKER' AND earnings_date='$DATE';"
```

If exists, show what's already collected and continue (will overwrite with INSERT OR REPLACE).

### Step 4: Fetch Sentiment via Fallback Chain

**4a. Try search first (free):**
```
mcp__perplexity__perplexity_search with query="$TICKER earnings sentiment analyst expectations $DATE"
```
Summarize into structured format:
- Direction: [bullish/bearish/neutral]
- Score: [-1.0 to +1.0]
- Catalysts: [3 bullets]
- Risks: [2 bullets]

**4b. If search insufficient, try Perplexity ask:**
Check budget first:
```bash
sqlite3 "$PROJECT_ROOT/sentiment/data/sentiment_cache.db" \
  "SELECT COALESCE((SELECT calls FROM api_budget WHERE date=date('now')), 0) as calls;"
```

If under budget (< 40):
```
mcp__perplexity__perplexity_ask with query="For $TICKER earnings on $DATE, respond ONLY in this format:
Direction: [bullish/bearish/neutral]
Score: [number -1 to +1]
Catalysts: [3 bullets, max 10 words each]
Risks: [2 bullets, max 10 words each]"
```

### Step 5: Classify Sentiment Direction
- **bullish**: Analyst upgrades, positive guidance, beat expected, strong catalysts
- **bearish**: Downgrades, concerns about guidance, miss expected
- **neutral**: Mixed signals, no clear direction

> Score mapping ranges removed from public version.

### Step 6: Save to Sentiment History
```bash
sqlite3 "$PROJECT_ROOT/sentiment/data/sentiment_cache.db" \
  "INSERT OR REPLACE INTO sentiment_history
   (ticker, earnings_date, collected_at, source, sentiment_text,
    sentiment_score, sentiment_direction, vrp_ratio, implied_move_pct, updated_at)
   VALUES ('$TICKER', '$DATE', datetime('now'), '$SOURCE', '$SENTIMENT_TEXT',
           $SCORE, '$DIRECTION', $VRP_RATIO, $IMPLIED_MOVE, datetime('now'));"
```

### Step 7: Also Cache for Immediate Use
```bash
sqlite3 "$PROJECT_ROOT/sentiment/data/sentiment_cache.db" \
  "INSERT OR REPLACE INTO sentiment_cache
   (ticker, date, source, sentiment, cached_at)
   VALUES ('$TICKER', '$DATE', '$SOURCE', '$SENTIMENT_TEXT', datetime('now'));"
```

## Output Format

```
==============================================================
COLLECTING SENTIMENT: $TICKER
==============================================================

Earnings Date: $DATE
VRP Context:
   VRP Ratio: 3.87x (EXCELLENT)
   Implied Move: 10.96%
   Liquidity: WARNING

SENTIMENT ANALYSIS
   Source: search / Perplexity
   Direction: BULLISH | Score: +0.6
   Catalysts:
     - {bullet 1}
     - {bullet 2}
     - {bullet 3}
   Risks:
     - {bullet 1}
     - {bullet 2}

SAVED TO HISTORY
   Record: $TICKER-$DATE
   Ready for outcome backfill after earnings

NEXT STEPS
   After earnings on $DATE, run: /backfill $TICKER $DATE
   Or batch backfill: /backfill --pending
==============================================================
```

## Cost Control
- Tries free search first
- Only uses Perplexity ask if search insufficient AND budget allows
- Single call per ticker (no duplicates)
