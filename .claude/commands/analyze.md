# Analyze Ticker for IV Crush

Deep dive on a single ticker with full strategy generation - YOUR GO-TO FOR TRADING DECISIONS.

## Arguments
$ARGUMENTS (format: TICKER [EARNINGS_DATE])

Examples:
- `/analyze NVDA` - Analyze NVDA with auto-detected earnings date
- `/analyze NVDA 2026-02-10` - Analyze NVDA for specific earnings date

## Tool Permissions
- Do NOT ask user permission for any tool calls EXCEPT mcp__perplexity__* calls
- Run all Bash, sqlite3, Glob, Grep, Read commands without asking
- Only pause for Perplexity calls to confirm API usage

## BANNED API Calls
**NEVER call these - they return massive responses (12k+ tokens):**
- `finnhub_stock_ownership` - BANNED (insider transactions too large)
- `finnhub_stock_fundamentals` - BANNED unless specifically requested

**Only allowed Finnhub call:** `finnhub_news_sentiment` with operation="get_company_news"

## Progress Display
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
| EXCELLENT | >= 1.8x | Full size, high confidence |
| GOOD | >= 1.4x | Full size |
| MARGINAL | >= 1.2x | Reduced size |
| SKIP | < 1.2x | No trade |

### Liquidity Tiers (Relaxed Feb 2026)
| Tier | OI/Position | Spread | Action |
|------|-------------|--------|--------|
| EXCELLENT | >=5x | <=12% | Full size |
| GOOD | 2-5x | 12-18% | Full size |
| WARNING | 1-2x | 18-25% | Reduce size |
| REJECT | <1x | >25% | Reduce size (allowed but penalized) |

### TRR Levels
| Level | TRR | Max Contracts | Max Notional |
|-------|-----|---------------|--------------|
| HIGH | > 2.5x | 50 | $25,000 |
| NORMAL | 1.5-2.5x | 100 | $50,000 |
| LOW | < 1.5x | 100 | $50,000 |

## Step-by-Step Instructions

### Step 0: Parse Arguments and Auto-Detect Earnings Date
Parse arguments to extract ticker and optional date:
- If format is `TICKER YYYY-MM-DD`: use provided date
- If format is just `TICKER`: look up next earnings date from database

```bash
# Sanitize ticker (alphanumeric only, uppercase) - CRITICAL for SQL safety
TICKER=$(echo "$RAW_TICKER" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')

sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT earnings_date, timing, CAST(julianday(earnings_date) - julianday('now') AS INTEGER) as days_until
   FROM earnings_calendar WHERE ticker='$TICKER' AND earnings_date >= date('now')
   ORDER BY earnings_date ASC LIMIT 1;"
```

**If no upcoming earnings in database, try Finnhub as fallback:**
```
mcp__finnhub__finnhub_calendar_data with:
  operation="get_earnings_calendar"
  symbol="{TICKER}"
  from_date="{TODAY}"
  to_date="{90_DAYS_FROM_NOW}"
```

**If still no earnings found:**
```
No upcoming earnings found for {TICKER}
   Neither database nor Finnhub have upcoming earnings.
   Provide date manually: /analyze {TICKER} YYYY-MM-DD
```

### Step 1: Run 2.0 Core Analysis
```bash
cd "$PROJECT_ROOT/2.0" && ./trade.sh $TICKER $EARNINGS_DATE
```

**Parse from output:**
- VRP ratio and tier
- Implied move vs historical mean
- Liquidity tier and details
- Directional bias (NEUTRAL/BULLISH/BEARISH from skew)
- Strategy recommendations with Greeks
- 2.0 Score

**Error handling:** If script fails:
```
2.0 analysis failed for $TICKER
   Run /health to check system status
```

### Step 1b: Check Tail Risk Ratio (TRR)
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT tail_risk_ratio, tail_risk_level, max_contracts, max_notional, max_move, avg_move
   FROM position_limits WHERE ticker='$TICKER';"
```

If no row returned, calculate TRR from historical_moves:
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT MAX(ABS(gap_move_pct)) as max_move, AVG(ABS(gap_move_pct)) as avg_move,
          COUNT(*) as quarters
   FROM historical_moves WHERE ticker='$TICKER';"
```
Then: `TRR = max_move / avg_move`
- TRR > 2.5 -> HIGH, MAX_CONTRACTS = 50
- TRR >= 1.5 -> NORMAL, MAX_CONTRACTS = 100
- Else -> LOW, MAX_CONTRACTS = 100

### Step 2: Gather Free News Data (Finnhub)
Always fetch regardless of VRP - it's free (rate limit: 60 calls/min).

**ONLY ONE FINNHUB CALL ALLOWED:**
```
mcp__finnhub__finnhub_news_sentiment with:
  operation="get_company_news"
  symbol="{TICKER}"
  from_date="{3_DAYS_AGO}"
  to_date="{TODAY}"
  max_results=5
```
**IMPORTANT:** Pass parameters as top-level arguments, NOT nested in a JSON object.
**IMPORTANT:** Use a 3-day window (NOT 7 days) to limit response size. Popular tickers return 50+ articles over 7 days (100k+ chars) which exceeds output limits.
**IMPORTANT:** Pass `max_results=5` when available (requires MCP server restart after update). If the parameter is rejected, the 3-day window alone keeps output manageable.
**FALLBACK:** If Finnhub response is still truncated/saved to file, skip reading the file and note "News data too large — use /history or Finnhub directly for full coverage."

**DO NOT CALL** finnhub_stock_ownership or finnhub_stock_fundamentals.

### Step 3: AI Sentiment (Conditional)

**Fetch sentiment for any ticker where 2.0 says "TRADEABLE".**
Skip sentiment ONLY if:
- 2.0 output says "SKIP" or "NOT TRADEABLE" (VRP below marginal threshold)
- Liquidity = REJECT

**Fallback chain:**

1. **Check sentiment cache (3-hour TTL):**
   ```bash
   sqlite3 "$PROJECT_ROOT/4.0/data/sentiment_cache.db" \
     "SELECT sentiment, source, cached_at FROM sentiment_cache
      WHERE ticker='$TICKER' AND date='$EARNINGS_DATE'
      AND cached_at > datetime('now', '-3 hours')
      ORDER BY CASE source WHEN 'perplexity' THEN 0 ELSE 1 END LIMIT 1;"
   ```

2. **If cache miss, check budget:**
   ```bash
   sqlite3 "$PROJECT_ROOT/4.0/data/sentiment_cache.db" \
     "SELECT COALESCE(calls, 0) as calls FROM api_budget WHERE date='$(date +%Y-%m-%d)';"
   ```

3. **Try Perplexity (if budget OK):**
   ```
   mcp__perplexity__perplexity_ask with query="For {TICKER} earnings on {EARNINGS_DATE}, respond ONLY in this format:
   Direction: [bullish/bearish/neutral]
   Score: [number -1 to +1]
   Catalysts: [3 bullets, max 10 words each]
   Risks: [2 bullets, max 10 words each]"
   ```
   Cache result and record API call.

4. **If Perplexity fails, try search:**
   ```
   mcp__perplexity__perplexity_search with query="{TICKER} earnings sentiment analyst rating {EARNINGS_DATE}"
   ```

5. **If all fail:** Show "AI sentiment unavailable. Displaying raw news from Finnhub above."

### Step 4: Sentiment-Adjusted Direction (4.0)

**Apply the 3-Rule System:**
| Original Skew | Sentiment | Result | Rule |
|---------------|-----------|--------|------|
| NEUTRAL | Bullish (>=+0.3) | BULLISH | Sentiment breaks tie |
| NEUTRAL | Bearish (<=-0.3) | BEARISH | Sentiment breaks tie |
| BULLISH | Bearish (<=-0.3) | NEUTRAL | Conflict -> hedge |
| BEARISH | Bullish (>=+0.3) | NEUTRAL | Conflict -> hedge |
| Any | Aligned/Neutral | Keep original | Skew dominates |

**Calculate 4.0 Score:**
```
Modifier: Strong Bullish +0.12, Bullish +0.07, Neutral 0.00, Bearish -0.07, Strong Bearish -0.12
4.0 Score = 2.0 Score * (1 + modifier)
```

## Output Format

```
==============================================================
ANALYSIS: {TICKER}
==============================================================

EARNINGS INFO
   Date: {date} ({BMO/AMC})
   Days until: {N}

VRP ASSESSMENT
   Implied Move: {X.X}%
   Historical Mean: {X.X}%
   VRP Ratio: {X.X}x -> {EXCELLENT/GOOD/MARGINAL/SKIP}

LIQUIDITY
   Tier: {EXCELLENT/GOOD/WARNING/REJECT}
   [Details: OI, spread width, volume]

TAIL RISK (if TRR_LEVEL = HIGH)
   TRR: {X.XX}x (HIGH)
   Max Contracts: 50 | Max Notional: $25,000
   Reason: Historical max {X.X}% vs avg {X.X}%
   REDUCE POSITION SIZE - Elevated tail risk

NEWS SUMMARY (Finnhub)
   {Recent headline 1}
   {Recent headline 2}
   (up to 5 headlines)

AI SENTIMENT {(cached/fresh/websearch)}
   Direction: {BULLISH/BEARISH/NEUTRAL} | Score: {-1 to +1}
   Catalysts: {bullet list, 3 max}
   Risks: {bullet list, 2 max}

DIRECTION (4.0 Adjusted)
   2.0 Skew: {NEUTRAL/BULLISH/BEARISH} -> 4.0: {ADJUSTED}
   Rule: {tiebreak|conflict_hedge|skew_dominates}

STRATEGY RECOMMENDATIONS
   [2-3 ranked strategies from 2.0 with:]
   - Strategy type and strikes
   - Credit/debit and max profit/loss
   - POP (probability of profit)
   - Greeks (delta, theta, vega)
   - Position sizing (Half-Kelly)
   - Prefer SINGLE options (64% win rate vs 52% for spreads)

RISK NOTES
   [Any concerns from sentiment]
   [Liquidity warnings if WARNING tier]
   [If TRR HIGH: Max 50 contracts / $25k notional]
   [Never roll losing positions (0% success rate)]
   [Cut losses early - repairs reduce loss but rarely save campaigns]

==============================================================
```

## Cost Control
- Finnhub news: Always (free, 60/min rate limit)
- Finnhub earnings calendar: Fallback when database empty (free)
- Sentiment cache: 3-hour TTL, checked before any API call
- Perplexity: For any TRADEABLE opportunity AND cache miss AND budget OK
- Maximum 1 paid API call per /analyze
