# Find Most Anticipated Earnings

Discover the week's most anticipated earnings with VRP analysis and AI sentiment.

## Arguments
$ARGUMENTS (format: [DATE] - optional)

**Default Week Logic:**
- Monday-Thursday: Use current week
- Friday-Sunday: Use next week (current week's earnings are mostly done)

Examples:
- `/whisper` - Auto-selects current or next week based on day
- `/whisper 2026-02-10` - Week containing that specific date

## Tool Permissions
- Do NOT ask user permission for any tool calls EXCEPT mcp__perplexity__* calls
- Run all Bash, sqlite3, Glob, Grep, Read commands without asking
- Only pause for Perplexity calls to confirm API usage

## Progress Display
Show progress updates as you work:
```
[1/6] Determining target week...
[2/6] Running core whisper analysis...
[3/6] Filtering qualified tickers (2.0 Score >= 50)...
[4/6] Checking tail risk ratios...
[5/6] Fetching sentiment for top 5...
[6/6] Calculating sentiment scores and ranking...
```

## Reference: Scoring & Thresholds

> Proprietary scoring cutoffs, sentiment modifier values, and TRR tier tables removed from public version.

## Step-by-Step Instructions

### Step 1: Parse Date Argument
- Get current date from system: `date '+%Y-%m-%d %A'`
- **Default week logic (when no date argument provided):**
  - If Monday-Thursday: use current week (scan Mon-Fri of this week)
  - If Friday-Sunday: use next week (most current week earnings are done)
- If date argument provided, use that date's week

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

**Check if today is a trading day (informational only):**
```bash
# Simple weekday/weekend check
DAY_OF_WEEK=$(date '+%A')
CURRENT_HOUR=$(date '+%H')
```
- Weekend (Sat/Sun): Show "Weekend - Using Friday's close data"
- Weekday before 9:30 AM ET: Show "Pre-market - VRP uses prior close"
- Weekday after 4:00 PM ET: Show "After-hours - Using today's close data"
- Otherwise: Show "Market hours"

### Step 2: Run core Whisper Analysis

```bash
cd "$PROJECT_ROOT/core" && ./trade.sh whisper $TARGET_MONDAY
```

Or if date argument was provided:
```bash
cd "$PROJECT_ROOT/core" && ./trade.sh whisper $PROVIDED_DATE
```

This discovers earnings from Yahoo Finance and runs VRP analysis on each ticker.

### Step 3: Filter by core Score >= 50
Parse the whisper output and filter to tickers with core Score >= 50.

**IMPORTANT:** Do NOT suppress REJECT liquidity tickers from display. Show ALL qualified tickers (VRP >= 1.8x EXCELLENT tier) in the results table, clearly marking REJECT ones. This gives visibility into what opportunities exist even if liquidity is poor.

Take TOP 5 from filtered results for sentiment enrichment (skip REJECT for sentiment fetch to save budget, but still display them).

### Step 4: Check TRR for All Qualified Tickers
Query tail risk for all qualified tickers in one batch:
```bash
# Tickers should already be sanitized (alphanumeric, uppercase) from core output
TICKERS="'NVDA','AAPL','MU'"  # Use actual qualified tickers

sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT ticker, tail_risk_ratio, tail_risk_level, max_contracts
   FROM position_limits WHERE ticker IN ($TICKERS) AND tail_risk_level = 'HIGH';"
```

If ticker not in position_limits, calculate from historical_moves:
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT ticker,
          MAX(ABS(gap_move_pct)) / AVG(ABS(gap_move_pct)) as trr
   FROM historical_moves
   WHERE ticker IN ($TICKERS)
   GROUP BY ticker
   HAVING trr > 2.5;"
```

### Step 5: Gather Sentiment for Top 5 Non-REJECT Tickers

For EACH of the top 5 qualified non-REJECT tickers:

**5a. Check sentiment cache first:**
```bash
TICKER=$(echo "$RAW_TICKER" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')

sqlite3 "$PROJECT_ROOT/sentiment/data/sentiment_cache.db" \
  "SELECT sentiment, source, cached_at FROM sentiment_cache
   WHERE ticker='$TICKER' AND date='$EARNINGS_DATE'
   AND cached_at > datetime('now', '-3 hours')
   ORDER BY CASE source WHEN 'council' THEN 0 WHEN 'perplexity' THEN 1 ELSE 2 END LIMIT 1;"
```
If found: use cached sentiment, note "(cached)"

**5b. If cache miss, check budget:**
```bash
sqlite3 "$PROJECT_ROOT/sentiment/data/sentiment_cache.db" \
  "SELECT COALESCE(calls, 0) as calls FROM api_budget WHERE date='$(date +%Y-%m-%d)';"
```
If calls >= 40: skip to WebSearch fallback (daily limit: 40 calls, monthly cap: $5)

**5c. Try Perplexity (if budget OK):**
```
mcp__perplexity__perplexity_ask with query="For {TICKER} earnings on {DATE}, respond ONLY in this format:
Direction: [bullish/bearish/neutral]
Score: [number -1 to +1]
Catalysts: [3 bullets, max 10 words each]
Risks: [2 bullets, max 10 words each]"
```
- Cache result with source="perplexity"
- Record API call in budget tracker

**5d. If Perplexity fails, try WebSearch:**
```
mcp__perplexity__perplexity_search with query="{TICKER} earnings sentiment analyst rating {DATE}"
```
- Summarize results into the same structured format
- Cache with source="websearch"

**5e. If all fail:**
```
Sentiment unavailable for {TICKER}
```

### Step 6: Calculate sentiment Scores & Adjusted Direction
For each ticker with sentiment:
1. Apply sentiment modifier to get sentiment Score
2. Apply the 3-rule direction system:
   - Rule 1: Neutral skew + sentiment signal -> use sentiment direction
   - Rule 2: Skew conflicts with sentiment -> go Neutral (hedge)
   - Rule 3: Otherwise -> keep original skew
3. Drop any ticker with sentiment Score < 55
4. Re-rank by sentiment Score (descending)

## Output Format

```
==============================================================
MOST ANTICIPATED EARNINGS - Week of {DATE}
==============================================================

Market Status: [Pre-market/Open/After-hours/Weekend - time info]

4.0 SENTIMENT-ADJUSTED RESULTS

 #  TICKER  Earnings     VRP      Imp Move  core   Sentiment      sentiment   DIR(4.0)    LIQUIDITY  TRR
 1  LULU    Feb 11 AMC   4.67x    12.04%    95.1  Bear (-0.6)    88.5  NEUTRAL*    REJECT
 2  MU      Feb 10 AMC   3.53x    8.01%     88.2  Bull (+0.7)    98.8  BULLISH     GOOD       HIGH
 3  AVGO    Feb 11 AMC   2.72x    7.85%     87.0  Bull (+0.6)    93.1  BULLISH     GOOD       HIGH
 4  ORCL    Feb 10 AMC   3.87x    10.96%    85.9  Bull (+0.4)    91.9  NEUTRAL     EXCELLENT

Legend: VRP EXCELLENT (>=1.8x) | GOOD (>=1.4x) | MARGINAL (>=1.2x)
        * = direction changed from core skew (sentiment conflict -> hedge)
        TRR HIGH = elevated tail risk (max 50 contracts)

TOP PICK: {TICKER} ({Date} {BMO/AMC})
   VRP: {X.X}x | Implied Move: {X.X}% | sentiment Score: {X.X}
   Sentiment: {1-line summary, max 30 words}
   Direction: {2.0 Skew} -> {4.0 Adjusted} ({rule applied})

CONFLICTS (if any):
   {TICKER}: Skew={X} vs Sentiment={Y} -> Neutral stance (hedge both sides)

HIGH TAIL RISK (if any):
   {TICKER}: TRR {X.XX}x -> Max 50 contracts / $25k notional
   [Only show tickers with tail_risk_level = "HIGH". Omit section if none.]

CACHE STATUS
   Hits: X (instant, free)
   Misses: Y (fetched fresh)
   Budget: Z/40 calls today | $X.XX left this month

NEXT STEPS
   Run /analyze {TOP_TICKER} for full strategy recommendations
==============================================================
```

## Cost Control
- Maximum 5 Perplexity calls (top 5 only)
- Only for VRP >= 1.8x AND Liquidity != REJECT
- Cache hits are instant and free
- After /prime, all sentiment comes from cache

## Typical Workflow
```
Morning:  /prime           -> Pre-cache all sentiment
Then:     /whisper         -> Instant results (cache hits)
Pick:     /analyze NVDA    -> Deep dive on best candidate
```
