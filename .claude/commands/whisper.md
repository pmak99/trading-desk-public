# Find Most Anticipated Earnings

Discover the week's most anticipated earnings with VRP analysis and AI sentiment - YOUR GO-TO FOR DISCOVERY.

## Arguments
$ARGUMENTS (format: [DATE] - optional)

**Default Week Logic:**
- Monday-Thursday: Use current week
- Friday-Sunday: Use next week (current week's earnings are mostly done)

Examples:
- `/whisper` - Auto-selects current or next week based on day
- `/whisper 2025-12-09` - Week containing that specific date

## Typical Workflow
```
Morning:  /prime           → Pre-cache all sentiment
Then:     /whisper         → Instant results (cache hits)
Pick:     /analyze NVDA    → Deep dive on best candidate
```

## Tool Permissions
- Do NOT ask user permission for any tool calls EXCEPT mcp__perplexity__* calls
- Run all Bash, sqlite3, Glob, Grep, Read commands without asking
- Only pause for Perplexity calls to confirm API usage

## Progress Display
Show progress updates as you work:
```
[1/5] Checking earnings cache freshness...
[2/5] Checking market status...
[3/5] Running 2.0 analysis for qualified tickers...
[4/5] Loading cached sentiment...
[5/5] Calculating 4.0 scores...
```

## Minimum Cutoffs

- **2.0 Score ≥ 50** (pre-sentiment filter)
- **4.0 Score ≥ 55** (post-sentiment filter)

## 4.0 Sentiment-Adjusted Scoring

**Formula:** `4.0 Score = 2.0 Score × (1 + Sentiment_Modifier)`

| Sentiment | Score Range | Modifier |
|-----------|-------------|----------|
| Strong Bullish | +0.7 to +1.0 | +12% |
| Bullish | +0.3 to +0.6 | +7% |
| Neutral | -0.2 to +0.2 | 0% |
| Bearish | -0.6 to -0.3 | -7% |
| Strong Bearish | -1.0 to -0.7 | -12% |

**4.0 Minimum:** After sentiment adjustment, only show if 4.0 Score ≥ 55

## Step-by-Step Instructions

### Step 1: Parse Date Argument
- Get current date from system: `date '+%Y-%m-%d %A'`
- **Default week logic (when no date argument provided):**
  - If Monday-Thursday → use current week (scan Mon-Fri of this week)
  - If Friday-Sunday → use next week (most current week earnings are done)
- If date argument provided, use that date's week
- IMPORTANT: Get actual current date from system, not assumptions

### Step 1.5: Check Earnings Cache Freshness (Auto-Sync if Stale)

First, calculate TARGET_MONDAY (needed for cache check):
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

Check if any earnings in the target week have stale cache (not validated in 24h):
```bash
STALE_COUNT=$(sqlite3 -noheader /Users/prashant/PycharmProjects/Trading\ Desk/2.0/data/ivcrush.db \
  "SELECT COUNT(*) FROM earnings_calendar
   WHERE earnings_date BETWEEN '$TARGET_MONDAY' AND date('$TARGET_MONDAY', '+4 days')
   AND (last_validated_at IS NULL OR last_validated_at < datetime('now', '-24 hours'));")
```

If STALE_COUNT > 0:
```
⚠️ Found {STALE_COUNT} tickers with stale earnings data (>24h old)
🔄 Running quick sync to refresh...
```
Then run sync:
```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && ./trade.sh sync 2>&1 | tail -5 || echo "⚠️ Sync completed with warnings"
```

If STALE_COUNT = 0:
```
✅ Earnings cache is fresh (validated within 24h)
```

### Step 2: Check Market Status (Alpaca MCP)
```
mcp__alpaca__alpaca_get_clock
```

**Date Detection Rules:**
- `is_open=true` → Market is open
- `is_open=false` AND it's weekday pre-9:30 AM ET → Pre-market
- `is_open=false` AND it's weekday post-4:00 PM ET → After-hours
- `is_open=false` AND Saturday/Sunday → Weekend

Display appropriate status:
- Pre-market: `⚠️ Pre-market - VRP uses prior close. Options refresh at 9:30 AM ET`
- After-hours: `⚠️ After-hours - Using today's close data`
- Weekend: `⚠️ Weekend - Using Friday's close data`

### Step 3: Run 2.0 Whisper Analysis

**Determine target week based on Step 1 logic:**
```bash
# Get current day of week (1=Mon, 7=Sun)
DAY_NUM=$(date '+%u')

# Calculate target Monday (portable for macOS and Linux)
if [ $DAY_NUM -ge 5 ]; then
    # Friday (5), Saturday (6), Sunday (7) → use next Monday
    # Friday: 8-5=3 days, Saturday: 8-6=2 days, Sunday: 8-7=1 day
    DAYS_TO_NEXT_MONDAY=$((8 - DAY_NUM))
    # macOS uses -v, Linux uses -d
    if [[ "$OSTYPE" == "darwin"* ]]; then
        TARGET_MONDAY=$(date -v+${DAYS_TO_NEXT_MONDAY}d '+%Y-%m-%d')
    else
        TARGET_MONDAY=$(date -d "+${DAYS_TO_NEXT_MONDAY} days" '+%Y-%m-%d')
    fi
else
    # Monday (1) through Thursday (4) → use this Monday
    DAYS_SINCE_MONDAY=$((DAY_NUM - 1))
    if [[ "$OSTYPE" == "darwin"* ]]; then
        TARGET_MONDAY=$(date -v-${DAYS_SINCE_MONDAY}d '+%Y-%m-%d')
    else
        TARGET_MONDAY=$(date -d "-${DAYS_SINCE_MONDAY} days" '+%Y-%m-%d')
    fi
fi
```

**Execute whisper with calculated week:**
```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && ./trade.sh whisper $TARGET_MONDAY
```

Or if date argument was provided, use that date directly:
```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && ./trade.sh whisper $PROVIDED_DATE
```

This provides:
- Most anticipated tickers for the week
- VRP ratios and tiers
- Liquidity grades
- Quality scores

### Step 4: Filter by 2.0 Score ≥ 50
Parse the whisper output and filter to tickers with 2.0 Score ≥ 50.

**IMPORTANT:** Do NOT suppress REJECT liquidity tickers from display. Show ALL qualified tickers (VRP >= 3x) in the results table, clearly marking REJECT ones as untradeable. This gives visibility into what opportunities exist even if liquidity is poor.

Take TOP 5 from filtered results for sentiment enrichment (skip REJECT for sentiment fetch to save budget, but still display them).

### Step 5: Gather Sentiment for TOP 3 (Conditional)

For EACH of the top 3 qualified tickers:

**5a. Check sentiment cache first (with 3-hour freshness):**
```bash
# Sanitize ticker (alphanumeric only, uppercase)
TICKER=$(echo "$TICKER" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')

sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
  "SELECT sentiment, source, cached_at FROM sentiment_cache WHERE ticker='$TICKER' AND date='$EARNINGS_DATE' AND cached_at > datetime('now', '-3 hours') ORDER BY CASE source WHEN 'perplexity' THEN 0 ELSE 1 END LIMIT 1;"
```
If found → use cached sentiment, note "(cached)"

**5b. If cache miss, check budget:**
```bash
sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
  "SELECT COALESCE(calls, 0) as calls FROM api_budget WHERE date='$(date +%Y-%m-%d)';"
```
If calls >= 40 → skip to WebSearch fallback (daily limit: 40 calls, monthly cap: $5)

**5c. Try Perplexity (if budget OK):**
```
mcp__perplexity__perplexity_ask with query="For {TICKER} earnings, respond ONLY in this format:
Direction: [bullish/bearish/neutral]
Score: [number -1 to +1]
Catalysts: [3 bullets, max 10 words each]
Risks: [2 bullets, max 10 words each]"
```
- Cache result with source="perplexity"
- Record API call in budget tracker

**5d. If Perplexity fails, try WebSearch:**
```
WebSearch with query="{TICKER} earnings sentiment analyst rating {DATE}"
```
- Summarize results into the same structured format above
- Cache with source="websearch"

**5e. If all fail:**
```
ℹ️ Sentiment unavailable for {TICKER}
```

### Step 6: Calculate 4.0 Scores & Adjusted Direction
For each ticker with sentiment:
1. Apply sentiment modifier to get 4.0 Score
2. Calculate adjusted direction using sentiment_direction module:
   ```python
   import sys
   sys.path.insert(0, '/Users/prashant/PycharmProjects/Trading Desk/4.0/src')
   from sentiment_direction import quick_adjust

   # skew_bias from 2.0 output, sentiment_score from Step 5
   adjusted_dir = quick_adjust(skew_bias, sentiment_score)
   ```
3. Drop any ticker with 4.0 Score < 55
4. Re-rank by 4.0 Score (descending)

### Step 7: Display Results

## Output Format

```
══════════════════════════════════════════════════════
MOST ANTICIPATED EARNINGS - Week of {DATE}
══════════════════════════════════════════════════════

⚠️ Market Status: [Pre-market/Open/After-hours/Weekend - time info]

🔝 4.0 SENTIMENT-ADJUSTED RESULTS (Full Table)

┌───┬────────┬────────────┬─────────┬───────────┬──────┬─────────────┬──────┬─────────────┬───────────┐
│ # │ TICKER │ Earnings   │ VRP     │ Imp Move  │ 2.0  │ Sentiment   │ 4.0  │ DIR (4.0)   │ LIQUIDITY │
├───┼────────┼────────────┼─────────┼───────────┼──────┼─────────────┼──────┼─────────────┼───────────┤
│ 1 │ LULU   │ Dec 11 AMC │ 4.67x ⭐│ 12.04%    │ 95.1 │ Bear (-0.6) │ 88.5 │ NEUTRAL*    │ 🚫 REJECT │
│ 2 │ ADBE   │ Dec 10 AMC │ 3.53x ✓ │ 8.01%     │ 88.2 │ Bull (+0.7) │ 98.8 │ BULLISH     │ WARNING   │
│ 3 │ AVGO   │ Dec 11 AMC │ 2.72x ○ │ 7.85%     │ 87.0 │ Bull (+0.6) │ 93.1 │ BULLISH     │ GOOD      │
│ 4 │ ORCL   │ Dec 10 AMC │ 3.87x ✓ │ 10.96%    │ 85.9 │ Bull (+0.4) │ 91.9 │ NEUTRAL     │ EXCELLENT │
└───┴────────┴────────────┴─────────┴───────────┴──────┴─────────────┴──────┴─────────────┴───────────┘

Legend: VRP ⭐ EXCELLENT (≥4x) | ✓ GOOD (≥3x) | ○ MARGINAL (≥1.5x)
        * = direction changed from 2.0 skew (sentiment conflict → hedge)

📊 TOP PICK: {TICKER} ({Date} {BMO/AMC})
   VRP: {X.X}x | Implied Move: {X.X}% | 4.0 Score: {X.X}
   Sentiment: {1-line summary, max 30 words}
   Direction: {2.0 Skew} → {4.0 Adjusted} ({rule applied})

⚠️ CONFLICTS (if any):
   • {TICKER}: Skew={X} vs Sentiment={Y} → Neutral stance (hedge both sides)

📦 CACHE STATUS
   Hits: X (instant, free)
   Misses: Y (fetched fresh)
   Budget: Z/40 calls today | $X.XX left this month

💡 NEXT STEPS
   Run `/analyze {TOP_TICKER}` for full strategy recommendations
══════════════════════════════════════════════════════
```

## Cost Control
- Maximum 5 Perplexity calls (top 5 only)
- Only for VRP >= 3x AND Liquidity != REJECT (discovery threshold)
- Cache hits are instant and free
- After `/prime`, all sentiment comes from cache

## After /prime vs Without /prime
- **After /prime:** All sentiment instant from cache (0 API calls)
- **Without /prime:** Fetches on-demand, caches for later commands
