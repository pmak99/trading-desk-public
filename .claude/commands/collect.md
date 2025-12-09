# Collect Sentiment Data

Explicitly collect and store sentiment for a ticker to build the historical dataset for backtesting.

## Arguments
$ARGUMENTS (format: TICKER [DATE] - ticker required, date optional defaults to next earnings)

Examples:
- `/collect NVDA` - Collect sentiment for NVDA's next earnings
- `/collect ORCL 2025-12-09` - Collect sentiment for ORCL earnings on Dec 9

## Tool Permissions
- Do NOT ask user permission for any tool calls EXCEPT mcp__perplexity__* calls
- Run all Bash, sqlite3, WebSearch commands without asking
- Only pause for Perplexity calls to confirm API usage

## Progress Display
Show progress updates as you work:
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
- Enables correlation analysis after 30+ days of data

## Step-by-Step Instructions

### Step 1: Parse Arguments
- TICKER is required (uppercase)
- DATE is optional, format YYYY-MM-DD
- If no date, look up next earnings date for ticker

### Step 2: Get Ticker Context from 2.0
Run analysis to get VRP and implied move:
```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && ./trade.sh $TICKER $DATE 2>&1 | head -100
```

Extract from output:
- VRP Ratio (e.g., 3.87x)
- Implied Move % (e.g., 10.96%)
- Earnings Date
- Liquidity Tier

### Step 3: Check if Already Collected
```bash
# Sanitize ticker (alphanumeric only, uppercase)
TICKER=$(echo "$TICKER" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')

sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
  "SELECT collected_at, source, sentiment_direction FROM sentiment_history
   WHERE ticker='$TICKER' AND earnings_date='$DATE';"
```

If exists, show:
```
ℹ️ Sentiment already collected for $TICKER on $DATE
   Source: {source}
   Direction: {direction}
   Collected: {timestamp}

   To re-collect, delete existing record first:
   sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
     "DELETE FROM sentiment_history WHERE ticker='$TICKER' AND earnings_date='$DATE';"
```
Then continue with collection.

### Step 4: Fetch Sentiment via Fallback Chain

**4a. Try WebSearch first (free):**
```
WebSearch with query="$TICKER earnings sentiment analyst expectations $DATE"
```
Summarize results into structured format:
- Direction: [bullish/bearish/neutral]
- Score: [number -1 to +1]
- Catalysts: [3 bullets, max 10 words each]
- Risks: [2 bullets, max 10 words each]

**4b. If WebSearch insufficient, try Perplexity:**
Check budget first:
```bash
sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
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

### Step 5: Analyze Sentiment Direction
Based on the sentiment text, classify as:
- **bullish**: Analyst upgrades, positive guidance, beat expectations expected, strong catalysts
- **bearish**: Downgrades, concerns about guidance, miss expected, negative catalysts
- **neutral**: Mixed signals, no clear direction

Optionally assign a score (-1.0 to +1.0):
- Strong bullish: +0.7 to +1.0
- Mild bullish: +0.2 to +0.6
- Neutral: -0.2 to +0.2
- Mild bearish: -0.6 to -0.2
- Strong bearish: -1.0 to -0.7

### Step 6: Save to Sentiment History
```bash
sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
  "INSERT OR REPLACE INTO sentiment_history
   (ticker, earnings_date, collected_at, source, sentiment_text,
    sentiment_score, sentiment_direction, vrp_ratio, implied_move_pct, updated_at)
   VALUES ('$TICKER', '$DATE', datetime('now'), '$SOURCE', '$SENTIMENT_TEXT',
           $SCORE, '$DIRECTION', $VRP_RATIO, $IMPLIED_MOVE, datetime('now'));"
```

### Step 7: Also Cache for Immediate Use
```bash
sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
  "INSERT OR REPLACE INTO sentiment_cache
   (ticker, date, source, sentiment, cached_at)
   VALUES ('$TICKER', '$DATE', '$SOURCE', '$SENTIMENT_TEXT', datetime('now'));"
```

## Output Format

```
══════════════════════════════════════════════════════
📊 COLLECTING SENTIMENT: $TICKER
══════════════════════════════════════════════════════

📅 Earnings Date: $DATE
📈 VRP Context:
   VRP Ratio: 3.87x (EXCELLENT)
   Implied Move: 10.96%
   Liquidity: WARNING

🔍 SENTIMENT ANALYSIS
   Source: WebSearch / Perplexity
   Direction: BULLISH / BEARISH / NEUTRAL | Score: +0.6
   Catalysts: {3 bullets, max 10 words each}
   Risks: {2 bullets, max 10 words each}

✅ SAVED TO HISTORY
   Record ID: $TICKER-$DATE
   Ready for outcome backfill after earnings

💡 NEXT STEPS
   • After earnings on $DATE, run: /backfill $TICKER $DATE
   • Or batch backfill: /backfill --pending
══════════════════════════════════════════════════════
```

## Cost Control
- Tries free WebSearch first
- Only uses Perplexity if WebSearch insufficient AND budget allows
- Single call per ticker (no duplicates)
- Shows budget status after collection
