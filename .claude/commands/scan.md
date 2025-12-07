# Scan Earnings by Date

Scan all tickers with earnings on a specific date with VRP analysis.

## Arguments
$ARGUMENTS (format: DATE - required, YYYY-MM-DD)

Examples:
- `/scan 2025-12-09` - Scan all earnings on December 9th
- `/scan 2025-12-15` - Scan all earnings on December 15th

## Step-by-Step Instructions

### Step 1: Parse Date Argument
- Date is REQUIRED in YYYY-MM-DD format
- If not provided, show error:
  ```
  ❌ Date required. Usage: /scan YYYY-MM-DD
     Example: /scan 2025-12-09
  ```

### Step 2: Check Market Status (Alpaca MCP)
```
mcp__alpaca__alpaca_get_clock
```

Display market status (informational):
```
⏰ Market: [OPEN/CLOSED] - [time info]
```

### Step 3: Run 2.0 Scan for Date
Execute the proven 2.0 scan mode:
```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && ./trade.sh scan $DATE
```

This provides:
- All tickers with earnings on date
- VRP ratio and tier for each
- Liquidity tier for each
- Quality score ranking

### Step 4: Identify TOP 5 VRP >= 3x Tickers
From scan results, identify the top 5 tickers where:
- VRP >= 3.0x (discovery threshold)
- Liquidity != REJECT

### Step 5: Add Sentiment for TOP 3 (Conditional)

For EACH of the top 3 qualified tickers:

**5a. Check sentiment cache:**
```bash
sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
  "SELECT sentiment, source, cached_at FROM sentiment_cache WHERE ticker='$TICKER' AND date='$DATE' ORDER BY CASE source WHEN 'perplexity' THEN 0 ELSE 1 END LIMIT 1;"
```
If found and < 3 hours old → use cached

**5b. If cache miss, use fallback chain:**

1. **Check budget:**
   ```bash
   sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
     "SELECT calls FROM api_budget WHERE date='$(date +%Y-%m-%d)';"
   ```
   If >= 150 → skip to WebSearch

2. **Try Perplexity:**
   ```
   mcp__perplexity__perplexity_ask with query="What is the current sentiment and analyst consensus for {TICKER} ahead of their earnings? Include recent news and whisper numbers."
   ```
   Cache result, record API call

3. **If fail, try WebSearch:**
   ```
   WebSearch with query="{TICKER} earnings sentiment analyst {DATE}"
   ```
   Cache with source="websearch"

4. **If all fail:**
   Show "Sentiment unavailable" but continue

## Output Format

```
══════════════════════════════════════════════════════
EARNINGS SCAN: {DATE}
══════════════════════════════════════════════════════

⏰ Market: [OPEN/CLOSED]

📅 ALL EARNINGS FOR {DATE}
┌──────┬─────────┬────────────┬───────┬────────────────────┐
│ Rank │ Ticker  │ VRP        │ Liq   │ Score              │
├──────┼─────────┼────────────┼───────┼────────────────────┤
│  1   │ NVDA    │ 8.2x ⭐    │ EXCEL │ 92                 │
│  2   │ AMD     │ 6.1x ⭐    │ EXCEL │ 85                 │
│  3   │ AVGO    │ 5.4x ✓     │ WARN  │ 72                 │
│  4   │ MU      │ 4.2x ✓     │ EXCEL │ 68                 │
│  5   │ ORCL    │ 3.1x ○     │ EXCEL │ 55                 │
│  6   │ CRM     │ 2.8x ○     │ WARN  │ 48                 │
│  7   │ WDAY    │ 2.1x ○     │ REJCT │ 32 🚫              │
│ ...  │ ...     │ ...        │ ...   │ ...                │
└──────┴─────────┴────────────┴───────┴────────────────────┘

Legend: ⭐ EXCELLENT (≥7x) | ✓ GOOD (≥4x) | ○ MARGINAL (≥1.5x) | 🚫 REJECT

📊 SUMMARY
   Total earnings: {N}
   VRP >= 3x: {M} tickers
   Liquidity REJECT: {R} tickers (avoid)

🔝 TOP 3 OPPORTUNITIES

1️⃣ NVDA - Earnings {BMO/AMC}
   VRP: 8.2x (EXCELLENT) | Implied: 8.5% | Historical: 1.0%
   Liquidity: EXCELLENT
   🧠 Sentiment: {cached/fresh/websearch}
   {Brief sentiment summary}

2️⃣ AMD - Earnings {BMO/AMC}
   VRP: 6.1x (GOOD) | Implied: 6.2% | Historical: 1.0%
   Liquidity: EXCELLENT
   🧠 Sentiment: {cached/fresh/websearch}
   {Brief sentiment summary}

3️⃣ AVGO - Earnings {BMO/AMC}
   VRP: 5.4x (GOOD) | Implied: 5.8% | Historical: 1.1%
   Liquidity: WARNING ⚠️
   🧠 Sentiment: {cached/fresh/websearch}
   {Brief sentiment summary}

💡 NEXT STEPS
   Run `/analyze NVDA` for full strategy recommendations
══════════════════════════════════════════════════════
```

## Cost Control
- Maximum 3 Perplexity calls (top 3 only)
- Only for VRP >= 3x AND Liquidity != REJECT (discovery threshold)
- Cache-aware to avoid duplicate calls
- If already primed with `/prime`, all sentiment from cache
