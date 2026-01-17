# Analyze Ticker for IV Crush

Deep dive on a single ticker with full strategy generation - YOUR GO-TO FOR TRADING DECISIONS.

## Arguments
$ARGUMENTS (format: TICKER [EARNINGS_DATE])

Examples:
- `/analyze NVDA` - Analyze NVDA with auto-detected earnings date
- `/analyze NVDA 2025-12-19` - Analyze NVDA for specific earnings date

## Tool Permissions
- Do NOT ask user permission for any tool calls EXCEPT mcp__perplexity__* calls
- Run all Bash, sqlite3, Glob, Grep, Read commands without asking
- Only pause for Perplexity calls to confirm API usage

## ⛔ BANNED API CALLS
**NEVER call these - they return massive responses (12k+ tokens):**
- `finnhub_stock_ownership` - BANNED (insider transactions too large)
- `finnhub_stock_fundamentals` - BANNED unless specifically requested

**Only allowed Finnhub call:** `finnhub_news_sentiment` with operation="get_company_news"

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

### VRP Tiers (BALANCED mode - default)
| Tier | Threshold | Action |
|------|-----------|--------|
| EXCELLENT | ≥ 1.8x | Full size, high confidence |
| GOOD | ≥ 1.4x | Full size |
| MARGINAL | ≥ 1.2x | Reduced size |
| SKIP | < 1.2x | No trade |

*Note: 2.0 system uses BALANCED mode by default. LEGACY mode (7x/4x/1.5x) available via VRP_THRESHOLD_MODE env var.*

### Liquidity Tiers
| Tier | OI/Position | Spread | Action |
|------|-------------|--------|--------|
| EXCELLENT | ≥5x | ≤8% | Full size |
| GOOD | 2-5x | 8-12% | Full size |
| WARNING | 1-2x | 12-15% | Reduce 50% |
| REJECT | <1x | >15% | 🚫 NO TRADE |

*Final tier = worse of (OI tier, Spread tier)*

### Tail Risk Ratio (TRR)
| Level | TRR | Max Contracts | Max Notional | Action |
|-------|-----|---------------|--------------|--------|
| HIGH | > 2.5x | 50 | $25,000 | ⚠️ Reduce size 50% |
| NORMAL | 1.5-2.5x | 100 | $50,000 | Standard sizing |
| LOW | < 1.5x | 100 | $50,000 | Standard sizing |

*TRR = Max Historical Move / Average Move. HIGH TRR tickers have extreme earnings surprises.*
*Lesson: significant MU loss (Dec 2025) - 200 contracts on HIGH TRR ticker.*

### Budget Limits
- Daily calls: 40 max
- Monthly budget: $5.00
- Cost per call: ~$0.006

## Step-by-Step Instructions

### Step 0: Parse Arguments and Auto-Detect Earnings Date
Parse the arguments to extract ticker and optional date:
- If format is `TICKER YYYY-MM-DD` → use provided date
- If format is just `TICKER` → look up next earnings date from database

**Auto-detect earnings date and timing (if not provided):**
```bash
# Sanitize ticker (alphanumeric only, uppercase) - CRITICAL for SQL safety
TICKER=$(echo "$TICKER" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')

# Use sanitized $TICKER in ALL subsequent queries
sqlite3 2.0/data/ivcrush.db \
  "SELECT earnings_date, timing, CAST(julianday(earnings_date) - julianday('now') AS INTEGER) as days_until FROM earnings_calendar WHERE ticker='$TICKER' AND earnings_date >= date('now') ORDER BY earnings_date ASC LIMIT 1;"
```
Save `{EARNINGS_DATE}`, `{TIMING}` (BMO/AMC), and `{DAYS_UNTIL}` for later use.

**If no upcoming earnings in database, try Finnhub as fallback:**
```
mcp__finnhub__finnhub_calendar_data with:
  operation="get_earnings_calendar"
  symbol="{TICKER}"
  from_date="{TODAY}"
  to_date="{90_DAYS_FROM_NOW}"
```

Parse response: `earningsCalendar[0].date` and `earningsCalendar[0].hour` (amc/bmo).

**If still no earnings found after Finnhub fallback:**
```
❌ No upcoming earnings found for {TICKER}
   Neither database nor Finnhub have upcoming earnings.
   Provide date manually: /analyze {TICKER} YYYY-MM-DD
```

### Step 1: Run 2.0 Core Analysis
Execute the proven 2.0 analysis script with ticker and earnings date:
```bash
cd 2.0 && ./trade.sh $TICKER $EARNINGS_DATE
```
(Use the date from Step 0 - either user-provided or auto-detected)

**Error handling:** If the script fails (exit code non-zero or no output):
```
❌ 2.0 analysis failed for $TICKER
   Run `/health` to check system status
   Or try: cd 2.0 && ./trade.sh health
```
→ Exit early, do not continue to sentiment fetch

**Parse from output** (needed for Steps 3-4):
- `{VRP_RATIO}` - the X.Xx multiplier
- `{LIQUIDITY_TIER}` - EXCELLENT/GOOD/WARNING/REJECT
- `{DIRECTIONAL_BIAS}` - NEUTRAL/BULLISH/BEARISH (from skew analysis)

If any field cannot be parsed, use defaults: VRP=0, LIQUIDITY=REJECT, BIAS=NEUTRAL

### Step 1b: Check Tail Risk Ratio (TRR)
Query the position_limits table to check if this ticker has elevated tail risk:
```bash
sqlite3 2.0/data/ivcrush.db \
  "SELECT tail_risk_ratio, tail_risk_level, max_contracts, max_notional, max_move, avg_move
   FROM position_limits WHERE ticker='$TICKER';"
```

**Parse TRR data:**
- `{TRR_RATIO}` - tail_risk_ratio (e.g., 3.05)
- `{TRR_LEVEL}` - tail_risk_level (HIGH/NORMAL/LOW)
- `{MAX_CONTRACTS}` - position limit (50 for HIGH, 100 otherwise)
- `{MAX_NOTIONAL}` - max position value ($25k for HIGH, $50k otherwise)
- `{MAX_MOVE}` - historical max earnings move
- `{AVG_MOVE}` - historical average move

**If no row returned, calculate TRR from historical_moves:**
```bash
sqlite3 2.0/data/ivcrush.db \
  "SELECT MAX(ABS(gap_move_pct)) as max_move, AVG(ABS(gap_move_pct)) as avg_move,
          COUNT(*) as quarters
   FROM historical_moves WHERE ticker='$TICKER';"
```
Then: `TRR_RATIO = max_move / avg_move`
- If TRR > 2.5 → TRR_LEVEL = "HIGH", MAX_CONTRACTS = 50
- If TRR >= 1.5 → TRR_LEVEL = "NORMAL", MAX_CONTRACTS = 100
- Else → TRR_LEVEL = "LOW", MAX_CONTRACTS = 100

This provides:
- VRP ratio and tier (uses configured mode - default BALANCED: ≥1.8x/≥1.4x/≥1.2x)
- Implied move vs historical mean
- Liquidity tier (EXCELLENT/GOOD/WARNING/REJECT)
- Strategy recommendations with Greeks
- Position sizing (Half-Kelly)

**Liquidity tier handling:**
- EXCELLENT/GOOD → Full position size, proceed normally
- WARNING → Reduce size 50%, note in output
- REJECT → Display warning below, skip sentiment (Step 3)

**CRITICAL:** If Liquidity = REJECT, display prominent warning:
```
🚫 LIQUIDITY REJECT - DO NOT TRADE
   Low open interest or wide spreads make this untradeable.
   (Lesson from significant loss on WDAY/ZS/SYM)
```

### Step 2: Gather Free News Data (Finnhub MCP)
Always fetch this regardless of VRP - it's free (rate limit: 60 calls/min).

⛔ **ONLY ONE FINNHUB CALL ALLOWED:**
```
mcp__finnhub__finnhub_news_sentiment with:
  operation="get_company_news"
  symbol="{TICKER}"
  from_date="{7_DAYS_AGO}"
  to_date="{TODAY}"
```
**IMPORTANT:** Pass parameters as top-level arguments, NOT nested in a JSON object.
The response may be large (70k+ chars) - extract only first 5 headlines.

⛔ **DO NOT CALL ANY OTHER FINNHUB TOOLS** - especially NOT:
- `finnhub_stock_ownership` (returns 23k tokens)
- `finnhub_stock_fundamentals` (returns 12k+ tokens)

**Extract from news response (ignore all other fields):**

**News** - use ONLY first 5 items, extract only `headline` and `source`:
```
📰 NEWS (last 7 days)
   • "{headline}" - {source}
   • "{headline}" - {source}
   ... (up to 5 headlines max)
```

### Step 3: AI Sentiment (Conditional - Fetch for any TRADEABLE opportunity)

**For direct /analyze requests:** Fetch sentiment for any ticker where 2.0 says "TRADEABLE".
This is different from discovery commands (/whisper, /scan) which use VRP ≥ 3x threshold.

**Skip sentiment ONLY if:**
- 2.0 output says "SKIP" or "NOT TRADEABLE" (VRP below marginal threshold)
- Liquidity = REJECT (not tradeable anyway)

**If qualified, use fallback chain:**

1. **Check sentiment cache first** (3-hour TTL):
   ```bash
   sqlite3 4.0/data/sentiment_cache.db \
     "SELECT sentiment, source, cached_at FROM sentiment_cache WHERE ticker='$TICKER' AND date='$(date +%Y-%m-%d)' AND cached_at > datetime('now', '-3 hours') ORDER BY CASE source WHEN 'perplexity' THEN 0 ELSE 1 END LIMIT 1;"
   ```
   If result returned → use cached sentiment, note "(cached from {source})"

2. **If cache miss, check budget:**
   ```bash
   sqlite3 4.0/data/sentiment_cache.db \
     "SELECT COALESCE(calls, 0) as calls FROM api_budget WHERE date='$(date +%Y-%m-%d)';"
   ```
   If calls ≥ 40 → skip to WebSearch fallback (daily limit: 40 calls, monthly cap: $5)

3. **Try Perplexity (if budget OK):**
   ```
   mcp__perplexity__perplexity_ask with query="For {TICKER} earnings on {EARNINGS_DATE}, respond ONLY in this format:
   Direction: [bullish/bearish/neutral]
   Score: [number -1 to +1]
   Catalysts: [3 bullets, max 10 words each]
   Risks: [2 bullets, max 10 words each]"
   ```
   - Cache result AND record API call with single upsert:
     ```sql
     INSERT INTO sentiment_cache (ticker, date, source, sentiment, cached_at)
     VALUES ('$TICKER', '$EARNINGS_DATE', 'perplexity', '$RESULT', datetime('now'));

     INSERT INTO api_budget (date, calls, cost, last_updated)
     VALUES ('$(date +%Y-%m-%d)', 1, 0.006, datetime('now'))
     ON CONFLICT(date) DO UPDATE SET
       calls = calls + 1,
       cost = cost + 0.005,
       last_updated = datetime('now');
     ```

4. **If Perplexity fails, try WebSearch:**
   ```
   WebSearch with query="{TICKER} earnings sentiment analyst rating {EARNINGS_DATE}"
   ```
   - Summarize results into the same structured format above
   - Cache with source="websearch":
     ```sql
     INSERT INTO sentiment_cache (ticker, date, source, sentiment, cached_at)
     VALUES ('$TICKER', '$EARNINGS_DATE', 'websearch', '$RESULT', datetime('now'));
     ```

5. **If all fail, show graceful message:**
   ```
   ℹ️ AI sentiment unavailable. Displaying raw news from Finnhub above.
   ```

### Step 4: Sentiment-Adjusted Direction (4.0 Enhancement)

If sentiment was gathered, adjust the directional bias from 2.0's skew analysis.

**Use values parsed in Step 1:**
- `{DIRECTIONAL_BIAS}` from 2.0 output (NEUTRAL/BULLISH/BEARISH)
- `{SENTIMENT_SCORE}` from Step 3 (-1 to +1)

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

⚡ TAIL RISK (if TRR_LEVEL = HIGH)
   TRR: {X.XX}x (HIGH)
   Max Contracts: {50}
   Max Notional: ${25,000}
   Reason: Historical max {X.X}% vs avg {X.X}%
   ⚠️ REDUCE POSITION SIZE - Elevated tail risk
   [If TRR_LEVEL != HIGH: omit this section entirely]

📰 NEWS SUMMARY (Finnhub)
   • {Recent headline 1}
   • {Recent headline 2}
   (up to 5 headlines)

🧠 AI SENTIMENT {(cached/fresh/websearch)}
   Direction: {BULLISH/BEARISH/NEUTRAL} | Score: {-1 to +1}
   Catalysts: {bullet list, 3 max}
   Risks: {bullet list, 2 max}
   [Or: "ℹ️ Skipped - not tradeable" / "ℹ️ Unavailable"]

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
   • [If TRR HIGH: "⚠️ HIGH TAIL RISK - Max {50} contracts / ${25k} notional"]

══════════════════════════════════════════════════════
```

## Cost Control
- Finnhub news: Always (free, 60/min rate limit)
- Finnhub earnings calendar: Fallback when database empty (free)
- Sentiment cache: 3-hour TTL, checked before any API call
- Perplexity: For any TRADEABLE opportunity (Liquidity ≠ REJECT) AND cache miss AND budget OK
- WebSearch: Free fallback if Perplexity fails or budget exceeded
- Maximum 1 paid API call per /analyze
