# Find Most Anticipated Earnings

Discover the week's most anticipated earnings with VRP analysis and sentiment - YOUR GO-TO FOR DISCOVERY.

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

### Step 4: Identify TOP 3 VRP > 4x Tickers
Parse the whisper output to find the top 3 tickers with:
- VRP >= 4.0x (GOOD or EXCELLENT tier)
- Liquidity != REJECT

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
mcp__perplexity__perplexity_ask with query="What is the current sentiment and analyst consensus for {TICKER} ahead of their earnings? Include recent news, analyst upgrades/downgrades, whisper numbers, and any concerns or catalysts."
```
- Cache result with source="perplexity"
- Record API call in budget tracker

**5d. If Perplexity fails, try WebSearch:**
```
WebSearch with query="{TICKER} earnings sentiment analyst rating {DATE}"
```
- Cache with source="websearch"

**5e. If all fail:**
```
ℹ️ Sentiment unavailable for {TICKER}
```

### Step 6: Display Results

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

🔝 TOP OPPORTUNITIES (VRP > 4x)

1️⃣ NVDA - {earnings_date} {BMO/AMC}
   VRP: 8.2x (EXCELLENT) | Implied Move: 8.5%
   Liquidity: EXCELLENT
   🧠 Sentiment: {cached/fresh}
   {Perplexity or WebSearch sentiment summary}

2️⃣ AMD - {earnings_date} {BMO/AMC}
   VRP: 6.1x (GOOD) | Implied Move: 6.2%
   Liquidity: EXCELLENT
   🧠 Sentiment: {cached/fresh}
   {Perplexity or WebSearch sentiment summary}

3️⃣ AVGO - {earnings_date} {BMO/AMC}
   VRP: 5.4x (GOOD) | Implied Move: 5.8%
   Liquidity: WARNING ⚠️
   🧠 Sentiment: {cached/fresh}
   {Perplexity or WebSearch sentiment summary}

📊 CACHE STATUS
   Hits: X (instant, free)
   Misses: Y (fetched fresh)
   Budget: Z/150 calls today

💡 NEXT STEPS
   Run `/analyze NVDA` for full strategy recommendations
══════════════════════════════════════════════════════
```

## Cost Control
- Maximum 3 Perplexity calls (top 3 only)
- Only for VRP >= 4x AND Liquidity != REJECT
- Cache hits are instant and free
- After `/prime`, all sentiment comes from cache

## After /prime vs Without /prime
- **After /prime:** All sentiment instant from cache (0 API calls)
- **Without /prime:** Fetches on-demand, caches for later commands
