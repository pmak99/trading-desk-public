# Today's Trading Alerts

Find today's high-VRP trading opportunities with sentiment analysis.

## Arguments
None - automatically uses today's date

## Tool Permissions
- Do NOT ask user permission for any tool calls EXCEPT mcp__perplexity__* calls
- Run all Bash, sqlite3, Glob, Grep, Read, MCP commands without asking
- Only pause for Perplexity calls to confirm API usage

## Progress Display
```
[1/4] Checking market status...
[2/4] Scanning today's earnings...
[3/4] Filtering high-VRP alerts...
[4/4] Fetching sentiment for qualified tickers...
```

## Step-by-Step Instructions

### Step 1: Check Market Status
```bash
DAY_OF_WEEK=$(date '+%A')
CURRENT_HOUR=$(date '+%H')
TODAY=$(date '+%Y-%m-%d')
```

If market is closed on weekend:
```
Market closed today (Weekend)
   No earnings to trade. Run /whisper for next week's opportunities.
```
-> Exit early

If pre-market/after-hours, display informational note and continue.

### Step 2: Run Today's Scan
```bash
cd "$PROJECT_ROOT/core" && ./trade.sh scan $(date +%Y-%m-%d)
```

### Step 3: Filter High-VRP Alerts
From results, identify tickers where:
- VRP >= 1.8x (EXCELLENT tier)
- Liquidity != REJECT
- Earnings timing is actionable (BMO if morning, AMC if afternoon)

If no alerts qualify:
```
No high-VRP opportunities today.
   Try /scan {tomorrow} to plan ahead.
```

Query TRR for alert tickers:
```bash
TICKERS="'NVDA','MU'"  # Use actual tickers

sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT ticker, tail_risk_ratio, tail_risk_level, max_contracts
   FROM position_limits WHERE ticker IN ($TICKERS) AND tail_risk_level = 'HIGH';"
```

### Step 4: Add Sentiment for Alerts (max 3)

**4a. Check sentiment cache:**
```bash
TICKER=$(echo "$RAW_TICKER" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')

sqlite3 "$PROJECT_ROOT/sentiment/data/sentiment_cache.db" \
  "SELECT sentiment, source FROM sentiment_cache
   WHERE ticker='$TICKER' AND date='$(date +%Y-%m-%d)'
   AND cached_at > datetime('now', '-3 hours')
   ORDER BY CASE source WHEN 'council' THEN 0 WHEN 'perplexity' THEN 1 ELSE 2 END LIMIT 1;"
```

**4b. If cache miss, use fallback chain:**
1. Check budget (40/day limit)
2. Try Perplexity (max 3 calls)
3. Fall back to `mcp__perplexity__perplexity_search`
4. Graceful skip if all fail

## Output Format

```
==============================================================
TODAY'S TRADING ALERTS - {DATE}
==============================================================

Market: [OPEN/CLOSED] - [time info]

HIGH-VRP ALERTS ({N} opportunities)

--- NVDA - EARNINGS TODAY (AMC) ---
VRP: 8.2x EXCELLENT
Implied Move: 8.5% | Historical: 1.0%
Liquidity: EXCELLENT
Sentiment: BULL (+0.6): {1-line, max 20 words}
-> Run /analyze NVDA for strategy recommendations

--- MU - EARNINGS TODAY (AMC) --- [HIGH TRR]
VRP: 4.2x GOOD
Implied Move: 6.2% | Historical: 1.0%
Liquidity: EXCELLENT
TRR: 3.05x -> Max 50 contracts / $25k
Sentiment: BULL (+0.4): {1-line, max 20 words}
-> Run /analyze MU for strategy recommendations

SUMMARY
   Alerts found: {N}
   With sentiment: {M}

REMINDERS
   Always check liquidity before trading
   Use /analyze TICKER for full strategy
   Respect TRR limits for HIGH tail risk tickers
==============================================================
```

## No Alerts Output

```
==============================================================
TODAY'S TRADING ALERTS - {DATE}
==============================================================

Market: [OPEN/CLOSED]

NO HIGH-VRP OPPORTUNITIES TODAY

Scanned {N} tickers with earnings today:
  VRP < 1.8x: {M} tickers (insufficient edge)
  Liquidity REJECT: {R} tickers
  Qualified: 0 tickers

SUGGESTIONS
   Run /scan {tomorrow} to plan ahead
   Run /whisper to see week's best opportunities
==============================================================
```

## Cost Control
- Maximum 3 Perplexity calls (high-VRP alerts only)
- Cache-aware (if primed, uses cached sentiment)
