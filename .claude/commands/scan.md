# Scan Earnings by Date

Scan all tickers with earnings on a specific date with VRP analysis.

## Arguments
$ARGUMENTS (format: DATE - required, YYYY-MM-DD)

Examples:
- `/scan 2025-12-09` - Scan all earnings on December 9th
- `/scan 2025-12-15` - Scan all earnings on December 15th

## Tool Permissions
- Do NOT ask user permission for any tool calls EXCEPT mcp__perplexity__* calls
- Run all Bash, sqlite3, Glob, Grep, Read commands without asking
- Only pause for Perplexity calls to confirm API usage

## Progress Display
Show progress updates as you work:
```
[1/4] Checking market status...
[2/4] Running 2.0 scan for date...
[3/4] Filtering VRP >= 1.8x tickers...
[4/4] Fetching sentiment for top 3...
```

## Tail Risk Ratio (TRR)

| Level | TRR | Max Contracts | Action |
|-------|-----|---------------|--------|
| HIGH | > 2.5x | 50 | ⚠️ TRR badge in table |
| NORMAL | 1.5-2.5x | 100 | No badge |
| LOW | < 1.5x | 100 | No badge |

*TRR = Max Historical Move / Average Move. HIGH TRR tickers caused significant MU loss.*

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
cd $PROJECT_ROOT/2.0 && ./trade.sh scan $DATE
```

This provides:
- All tickers with earnings on date
- VRP ratio and tier for each
- Liquidity tier for each
- Quality score ranking

### Step 4: Identify TOP 5 VRP >= 1.8x Tickers
From scan results, identify the top 5 tickers where:
- VRP >= 1.8x (discovery threshold - EXCELLENT tier)
- Liquidity != REJECT

### Step 4b: Check TRR for All Qualified Tickers
Query tail risk for all qualified tickers:
```bash
# Note: Tickers should already be sanitized (alphanumeric, uppercase) from 2.0 output
TICKERS="'NVDA','AMD','MU'"  # Use actual tickers from Step 4

sqlite3 $PROJECT_ROOT/2.0/data/ivcrush.db \
  "SELECT ticker, tail_risk_ratio, tail_risk_level, max_contracts
   FROM position_limits WHERE ticker IN ($TICKERS) AND tail_risk_level = 'HIGH';"
```

Mark HIGH TRR tickers for ⚠️ badge display.

### Step 5: Add Sentiment for TOP 3 (Conditional)

For EACH of the top 3 qualified tickers:

**5a. Check sentiment cache (with 3-hour freshness):**
```bash
# Sanitize ticker (alphanumeric only, uppercase)
TICKER=$(echo "$TICKER" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')

sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "SELECT sentiment, source, cached_at FROM sentiment_cache WHERE ticker='$TICKER' AND date='$DATE' AND cached_at > datetime('now', '-3 hours') ORDER BY CASE source WHEN 'perplexity' THEN 0 ELSE 1 END LIMIT 1;"
```
If found → use cached

**5b. If cache miss, use fallback chain:**

1. **Check budget:**
   ```bash
   sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
     "SELECT COALESCE(calls, 0) as calls FROM api_budget WHERE date='$(date +%Y-%m-%d)';"
   ```
   If >= 40 → skip to WebSearch (daily limit: 40 calls, monthly cap: $5)

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
┌──────┬─────────┬────────────┬───────┬───────┬─────┐
│ Rank │ Ticker  │ VRP        │ Liq   │ Score │ TRR │
├──────┼─────────┼────────────┼───────┼───────┼─────┤
│  1   │ NVDA    │ 8.2x ⭐    │ EXCEL │ 92    │     │
│  2   │ AMD     │ 6.1x ⭐    │ EXCEL │ 85    │     │
│  3   │ AVGO    │ 5.4x ✓     │ WARN  │ 72    │ ⚠️  │
│  4   │ MU      │ 4.2x ✓     │ EXCEL │ 68    │ ⚠️  │
│  5   │ ORCL    │ 3.1x ○     │ EXCEL │ 55    │     │
│  6   │ CRM     │ 2.8x ○     │ WARN  │ 48    │     │
│  7   │ WDAY    │ 2.1x ○     │ REJCT │ 32 🚫 │     │
│ ...  │ ...     │ ...        │ ...   │ ...   │     │
└──────┴─────────┴────────────┴───────┴───────┴─────┘

Legend: ⭐ EXCELLENT (≥1.8x) | ✓ GOOD (≥1.4x) | ○ MARGINAL (≥1.2x) | 🚫 REJECT
        TRR ⚠️ = HIGH tail risk (max 50 contracts)
*Note: Icons highlight relative strength; actual tier from 2.0 uses BALANCED mode thresholds*

📊 SUMMARY
   Total earnings: {N}
   VRP >= 1.8x: {M} tickers
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

⚡ HIGH TAIL RISK TICKERS (if any):
   • MU: TRR 3.05x → Max 50 contracts / $25k notional
   • AVGO: TRR 5.72x → Max 50 contracts / $25k notional
   [Only show tickers with TRR_LEVEL = "HIGH". Omit section if none.]

💡 NEXT STEPS
   Run `/analyze NVDA` for full strategy recommendations
══════════════════════════════════════════════════════
```

## Cost Control
- Maximum 3 Perplexity calls (top 3 only)
- Only for VRP >= 1.8x AND Liquidity != REJECT (discovery threshold)
- Cache-aware to avoid duplicate calls
- If already primed with `/prime`, all sentiment from cache
