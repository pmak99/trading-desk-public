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

### Step 2: Check Market Status (Alpaca MCP)
```
mcp__alpaca__alpaca_get_clock
```

If market is closed:
```
⚠️ Market closed - VRP uses prior close data
   Next open: {timestamp}
```

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

Take TOP 3 from filtered results for sentiment enrichment.

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
   sys.path.insert(0, '$PROJECT_ROOT/4.0/src')
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

⚠️ Market Status: [OPEN/CLOSED - time info]

📅 EARNINGS CALENDAR
┌──────────┬──────────┬─────────┬────────────┬──────────┐
│ Date     │ Ticker   │ VRP     │ Liquidity  │ Score    │
├──────────┼──────────┼─────────┼────────────┼──────────┤
│ Mon 12/9 │ NVDA     │ 8.2x ⭐ │ EXCELLENT  │ 92       │
│ Mon 12/9 │ AMD      │ 6.1x ⭐ │ EXCELLENT  │ 85       │
│ Tue 12/10│ AVGO     │ 5.4x ✓  │ WARNING    │ 72       │
│ ...      │ ...      │ ...     │ ...        │ ...      │
└──────────┴──────────┴─────────┴────────────┴──────────┘

Legend: ⭐ EXCELLENT (≥7x) | ✓ GOOD (≥4x) | ○ MARGINAL (≥1.5x)

🔝 TOP OPPORTUNITIES (Sentiment-Adjusted)

│ # │ TICKER │ 2.0  │ Sentiment   │ 4.0  │ DIR (4.0)   │ LIQ     │
├───┼────────┼──────┼─────────────┼──────┼─────────────┼─────────┤
│ 1 │ NVDA   │ 92.0 │ Bull (+0.6) │ 98.4 │ BULLISH     │ EXCEL   │
│ 2 │ ORCL   │ 74.0 │ Bull (+0.4) │ 79.2 │ BULLISH*    │ GOOD    │
│ 3 │ LULU   │ 68.0 │ Bear (-0.2) │ 63.2 │ NEUTRAL*    │ WARN ⚠️ │

* = direction changed from 2.0 skew (sentiment override)

TOP PICK: NVDA (Dec 10 AMC)
  VRP: 8.2x | Move: 8.5% | 4.0 Score: 98.4
  Sentiment: {1-line summary, max 30 words}

📊 CACHE STATUS
   Hits: X (instant, free)
   Misses: Y (fetched fresh)
   Budget: Z/150 calls today

💡 NEXT STEPS
   Run `/analyze NVDA` for full strategy recommendations
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
