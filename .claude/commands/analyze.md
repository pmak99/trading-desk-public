# Analyze Ticker for IV Crush

Deep dive on a single ticker with full strategy generation - YOUR GO-TO FOR TRADING DECISIONS.

## Arguments
$ARGUMENTS (format: TICKER [EARNINGS_DATE])

Examples:
- `/analyze NVDA` - Analyze NVDA with auto-detected earnings date
- `/analyze NVDA 2025-12-19` - Analyze NVDA for specific earnings date

## Tool Permissions
- Do NOT ask user permission for any tool calls EXCEPT mcp__perplexity__* calls
- Run all Bash, sqlite3, Glob, Grep, Read, Finnhub commands without asking
- Only pause for Perplexity calls to confirm API usage

## Progress Display
Show progress updates as you work:
```
[1/5] Detecting earnings date...
[2/5] Running 2.0 core analysis...
[3/5] Fetching news data (Finnhub)...
[4/5] Loading/fetching sentiment...
[5/5] Generating final report...
```

## Reference Tables

### VRP Tiers
| Tier | Threshold | Action |
|------|-----------|--------|
| EXCELLENT | ≥ 7.0x | Full size, high confidence |
| GOOD | ≥ 4.0x | Full size |
| MARGINAL | ≥ 1.5x | Reduced size |
| SKIP | < 1.5x | No trade |

### Liquidity Tiers
| Tier | OI/Position | Spread | Action |
|------|-------------|--------|--------|
| EXCELLENT | ≥5x | ≤8% | Full size |
| GOOD | 2-5x | 8-12% | Full size |
| WARNING | 1-2x | 12-15% | Reduce 50% |
| REJECT | <1x | >15% | 🚫 NO TRADE |

*Final tier = worse of (OI tier, Spread tier)*

### Budget Limits
- Daily calls: 40 max
- Monthly budget: $5.00
- Cost per call: ~$0.005

## Step-by-Step Instructions

### Step 0: Parse Arguments and Auto-Detect Earnings Date
Parse the arguments to extract ticker and optional date:
- If format is `TICKER YYYY-MM-DD` → use provided date
- If format is just `TICKER` → look up next earnings date from database

**Auto-detect earnings date and timing (if not provided):**
```bash
# Sanitize ticker (alphanumeric only, uppercase)
TICKER=$(echo "$TICKER" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')

sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/2.0/data/ivcrush.db \
  "SELECT earnings_date, timing, CAST(julianday(earnings_date) - julianday('now') AS INTEGER) as days_until FROM earnings_calendar WHERE ticker='$TICKER' AND earnings_date >= date('now') ORDER BY earnings_date ASC LIMIT 1;"
```
Save `{EARNINGS_DATE}`, `{TIMING}` (BMO/AMC), and `{DAYS_UNTIL}` for later use.

If no upcoming earnings found, display error and exit:
```
❌ No upcoming earnings found for {TICKER}
   Run ./trade.sh sync to refresh calendar, or provide date manually:
   /analyze {TICKER} YYYY-MM-DD
```

### Step 1: Run 2.0 Core Analysis
Execute the proven 2.0 analysis script with ticker and earnings date:
```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && ./trade.sh $TICKER $EARNINGS_DATE
```
(Use the date from Step 0 - either user-provided or auto-detected)

**Error handling:** If the script fails (exit code non-zero or no output):
```
❌ 2.0 analysis failed for $TICKER
   Run `/health` to check system status
   Or try: cd 2.0 && ./trade.sh health
```
→ Exit early, do not continue to sentiment fetch

This provides:
- VRP ratio and tier (EXCELLENT ≥7x, GOOD ≥4x, MARGINAL ≥1.5x, SKIP <1.5x)
- Implied move vs historical mean
- Liquidity tier (EXCELLENT/WARNING/REJECT)
- Strategy recommendations with Greeks
- Position sizing (Half-Kelly)

**CRITICAL:** If Liquidity = REJECT, display prominent warning:
```
🚫 LIQUIDITY REJECT - DO NOT TRADE
   Low open interest or wide spreads make this untradeable.
   (Lesson from $26,930 loss on WDAY/ZS/SYM)
```

### Step 2: Gather Free News Data (Finnhub MCP)
Always fetch this regardless of VRP - it's free. **Run both calls in parallel:**

```
mcp__finnhub__finnhub_news_sentiment with operation="get_company_news", symbol="{TICKER}", from_date="{7_DAYS_AGO}", to_date="{TODAY}", limit=5
mcp__finnhub__finnhub_stock_ownership with operation="get_insider_transactions", symbol="{TICKER}", limit=10
```

**Note:** The `limit` parameter reduces API response size to prevent context overflow.

**Extract from responses (ignore other fields):**

**News** - use only `headline` and `source` fields:
```
📰 NEWS (last 7 days)
   • "{headline}" - {source}
   • "{headline}" - {source}
   ... (up to 5 headlines)
```

**Insider Transactions** - count by `transactionCode` (S=sell, P=buy, G=gift):
```
👔 INSIDER ACTIVITY
   Sells: {count} transactions
   Buys: {count} transactions
   Notable: {name} {sold/bought} {change} shares @ ${transactionPrice}
```
Only mention 1-2 notable transactions (largest by dollar value = |change| × transactionPrice).

### Step 3: AI Sentiment (Conditional - Only if VRP ≥ 3x AND Liquidity ≠ REJECT)

**Skip sentiment if:**
- VRP < 3x (insufficient edge for discovery)
- Liquidity = REJECT (not tradeable anyway)

**If qualified, use fallback chain:**

1. **Check sentiment cache first:**
   ```bash
   sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
     "SELECT sentiment, source, cached_at FROM sentiment_cache WHERE ticker='$TICKER' AND date='$(date +%Y-%m-%d)' AND cached_at > datetime('now', '-3 hours') ORDER BY CASE source WHEN 'perplexity' THEN 0 ELSE 1 END LIMIT 1;"
   ```
   If result returned → use cached sentiment, note "(cached from {source})"

2. **If cache miss, check budget:**
   ```bash
   sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
     "SELECT COALESCE(calls, 0) as calls FROM api_budget WHERE date='$(date +%Y-%m-%d)';"
   ```
   If calls ≥ 40 → skip to WebSearch fallback (daily limit: 40 calls, monthly cap: $5)

3. **Try Perplexity (if budget OK):**
   ```
   mcp__perplexity__perplexity_ask with query="For {TICKER} earnings, respond ONLY in this format:
   Direction: [bullish/bearish/neutral]
   Score: [number -1 to +1]
   Catalysts: [3 bullets, max 10 words each]
   Risks: [2 bullets, max 10 words each]"
   ```
   - Cache result: `INSERT INTO sentiment_cache (ticker, date, source, sentiment, cached_at) VALUES ('$TICKER', '$DATE', 'perplexity', '$RESULT', datetime('now'));`
   - Record API call (use UPDATE then INSERT for safety):
     ```sql
     UPDATE api_budget SET calls = calls + 1, cost = cost + 0.005, last_updated = datetime('now') WHERE date = '$DATE';
     INSERT OR IGNORE INTO api_budget (date, calls, cost, last_updated) VALUES ('$DATE', 1, 0.005, datetime('now'));
     ```

4. **If Perplexity fails, try WebSearch:**
   ```
   WebSearch with query="{TICKER} earnings sentiment analyst rating {DATE}"
   ```
   - Summarize results into the same structured format above
   - Cache with source="websearch"

5. **If all fail, show graceful message:**
   ```
   ℹ️ AI sentiment unavailable. Displaying raw news from Finnhub above.
   ```

### Step 4: Sentiment-Adjusted Direction (4.0 Enhancement)

If sentiment was gathered, adjust the directional bias from 2.0's skew analysis.

**Extract from previous steps:**
- From 2.0 output (Step 1): `Directional Bias: {NEUTRAL/BULLISH/BEARISH}`
- From sentiment (Step 3): `Score: {-1 to +1}`

**Apply the 3-Rule System:**
| Original Skew | Sentiment | Result | Rule |
|---------------|-----------|--------|------|
| NEUTRAL | Bullish (≥+0.2) | → BULLISH | Sentiment breaks tie |
| NEUTRAL | Bearish (≤-0.2) | → BEARISH | Sentiment breaks tie |
| BULLISH | Bearish (≤-0.2) | → NEUTRAL | Conflict → hedge |
| BEARISH | Bullish (≥+0.2) | → NEUTRAL | Conflict → hedge |
| Any | Aligned/Neutral | → Keep original | Skew dominates |

**Strategy Impact:**
- BULLISH → Favor bull put spreads over straddles
- BEARISH → Favor bear call spreads over straddles
- NEUTRAL → Straddles, iron condors (hedged)
- Conflict (CHANGED to NEUTRAL) → Strongly prefer hedged strategies

## Output Format

```
══════════════════════════════════════════════════════
ANALYSIS: {TICKER}
══════════════════════════════════════════════════════

📅 EARNINGS INFO
   Date: {date} ({BMO/AMC})
   Days until: {N}

📊 VRP ASSESSMENT
   Implied Move: {X.X}%
   Historical Mean: {X.X}%
   VRP Ratio: {X.X}x → {EXCELLENT/GOOD/MARGINAL/SKIP}
   [Scoring weights: 55% VRP, 25% Move, 20% Liquidity]

💧 LIQUIDITY
   Tier: {EXCELLENT/WARNING/REJECT}
   [Details: OI, spread width, volume]
   [If REJECT: 🚫 DO NOT TRADE]

📰 NEWS SUMMARY (Finnhub)
   • {Recent headline 1}
   • {Recent headline 2}
   • Earnings history: {beat/miss pattern}
   • Insider activity: {summary}

🧠 AI SENTIMENT {(cached/fresh/websearch)}
   Direction: {BULLISH/BEARISH/NEUTRAL} | Score: {-1 to +1}
   Catalysts: {bullet list, 3 max}
   Risks: {bullet list, 2 max}
   [Or: "ℹ️ Skipped - VRP < 3x" / "ℹ️ Unavailable"]

🎯 DIRECTION (4.0 Adjusted)
   2.0 Skew: {NEUTRAL/BULLISH/BEARISH} → 4.0: {ADJUSTED}
   Rule: {tiebreak|conflict_hedge|skew_dominates}
   [If CHANGED: "⚠️ Sentiment shifted direction - review strategy alignment"]

📈 STRATEGY RECOMMENDATIONS
   [2-3 ranked strategies from 2.0 with:]
   - Strategy type and strikes
   - Credit/debit and max profit/loss
   - POP (probability of profit)
   - Greeks (delta, theta, vega)
   - Position sizing (Half-Kelly)

⚠️ RISK NOTES
   • [Any concerns from sentiment]
   • [Liquidity warnings if WARNING tier]
   • [High implied move caution if > 15%]

══════════════════════════════════════════════════════
```

## Cost Control
- Finnhub calls: Always (free, 60/min limit)
- Perplexity: Only if VRP ≥ 3x AND Liquidity ≠ REJECT AND cache miss AND budget OK
- Maximum 1 Perplexity call per /analyze
