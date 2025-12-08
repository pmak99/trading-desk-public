# Find Most Anticipated Earnings

Discover the week's most anticipated earnings with VRP analysis and AI sentiment - YOUR GO-TO FOR DISCOVERY.

## Arguments
$ARGUMENTS (format: [DATE] - optional, defaults to current week's Monday)

Examples:
- `/whisper` - This week's most anticipated
- `/whisper 2025-12-09` - Week starting from specific date

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
[1/4] Checking market status...
[2/4] Running 2.0 analysis for qualified tickers...
[3/4] Loading cached sentiment...
[4/4] Calculating 4.0 scores...
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
- If no date provided, use current week's Monday
- If date provided, use that as the week start
- IMPORTANT: Get actual current date from system, not assumptions

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
Execute the proven 2.0 whisper mode:
```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && ./trade.sh whisper
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

**5a. Check sentiment cache first:**
```bash
sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
  "SELECT sentiment, source, cached_at FROM sentiment_cache WHERE ticker='$TICKER' AND date='$(date +%Y-%m-%d)' ORDER BY CASE source WHEN 'perplexity' THEN 0 ELSE 1 END LIMIT 1;"
```
If found and < 3 hours old → use cached sentiment, note "(cached)"

**5b. If cache miss, check budget:**
```bash
sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
  "SELECT calls FROM api_budget WHERE date='$(date +%Y-%m-%d)';"
```
If calls >= 150 → skip to WebSearch fallback

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
   Budget: Z/150 calls today

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
