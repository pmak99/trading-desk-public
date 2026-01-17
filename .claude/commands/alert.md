# Today's Trading Alerts

Find today's high-VRP trading opportunities with sentiment analysis.

## Arguments
None - automatically uses today's date

## Tool Permissions
- Do NOT ask user permission for any tool calls EXCEPT mcp__perplexity__* calls
- Run all Bash, sqlite3, Glob, Grep, Read, MCP commands without asking
- Only pause for Perplexity calls to confirm API usage

## Progress Display
Show progress updates as you work:
```
[1/4] Checking market status...
[2/4] Scanning today's earnings...
[3/4] Filtering high-VRP alerts...
[4/4] Fetching sentiment for qualified tickers...
```

## Purpose
Quick command to check if there are any tradeable opportunities TODAY.
Run this after market open to see what's actionable.

## Tail Risk Ratio (TRR)

| Level | TRR | Max Contracts | Action |
|-------|-----|---------------|--------|
| HIGH | > 2.5x | 50 | ⚠️ TRR warning in alert box |
| NORMAL | 1.5-2.5x | 100 | No warning |
| LOW | < 1.5x | 100 | No warning |

*TRR = Max Historical Move / Average Move. HIGH TRR tickers caused $134k MU loss.*

## Step-by-Step Instructions

### Step 1: Check Market Status (Alpaca MCP)
```
mcp__alpaca__alpaca_get_clock
```

If market is closed on weekend/holiday:
```
ℹ️ Market closed today ({reason})
   No earnings to trade. Next trading day: {date}
```
→ Exit early

If pre-market/after-hours:
```
⏰ Market: CLOSED - Opens/Closed at {time}
   Showing today's earnings opportunities.
```

### Step 2: Run Alert Check Script
Execute the check_alerts script (primary method):
```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk/2.0 && ./trade.sh scan $(date +%Y-%m-%d)
```

Alternative if custom script exists:
```bash
cd /Users/prashant/PycharmProjects/Trading\ Desk && python scripts/check_alerts.py
```

This provides:
- Today's earnings with VRP analysis
- Filtered to high-opportunity trades

### Step 3: Filter High-VRP Alerts
From results, identify tickers where:
- VRP >= 3.0x (discovery threshold for alerts)
- Liquidity != REJECT
- Earnings timing is actionable (BMO if morning, AMC if afternoon)

If no alerts qualify:
```
📭 No high-VRP opportunities today.
   Try `/scan {tomorrow}` to plan ahead.
```

### Step 3b: Check TRR for Alert Tickers
Query tail risk for all alert tickers:
```bash
TICKERS="'NVDA','MU'"  # Use actual tickers from Step 3

sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/2.0/data/ivcrush.db \
  "SELECT ticker, tail_risk_ratio, tail_risk_level, max_contracts
   FROM position_limits WHERE ticker IN ($TICKERS) AND tail_risk_level = 'HIGH';"
```

Mark HIGH TRR tickers for warning display in alert boxes.

### Step 4: Add Sentiment for Alerts (Conditional)

For EACH alert (max 3):

**4a. Check sentiment cache (with 3-hour freshness):**
```bash
# Sanitize ticker (alphanumeric only, uppercase)
TICKER=$(echo "$TICKER" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')

sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
  "SELECT sentiment, source FROM sentiment_cache WHERE ticker='$TICKER' AND date='$(date +%Y-%m-%d)' AND cached_at > datetime('now', '-3 hours') ORDER BY CASE source WHEN 'perplexity' THEN 0 ELSE 1 END LIMIT 1;"
```

**4b. If cache miss, use fallback chain:**
1. Check budget:
   ```bash
   sqlite3 /Users/prashant/PycharmProjects/Trading\ Desk/4.0/data/sentiment_cache.db \
     "SELECT COALESCE(calls, 0) as calls FROM api_budget WHERE date='$(date +%Y-%m-%d)';"
   ```
   If >= 40 → skip to WebSearch (daily limit: 40 calls, monthly cap: $5)
2. Try Perplexity with query:
   ```
   "For {TICKER} earnings, respond ONLY in this format:
   Direction: [bullish/bearish/neutral]
   Score: [number -1 to +1]
   Catalysts: [2 bullets, max 8 words each]"
   ```
3. Fall back to WebSearch, summarize into same format
4. Graceful skip if all fail

## Output Format

```
══════════════════════════════════════════════════════
🚨 TODAY'S TRADING ALERTS - {DATE}
══════════════════════════════════════════════════════

⏰ Market: [OPEN/CLOSED] - [time info]

🔔 HIGH-VRP ALERTS ({N} opportunities)

┌─────────────────────────────────────────────────────┐
│ 🚨 NVDA - EARNINGS TODAY (AMC)                      │
├─────────────────────────────────────────────────────┤
│ VRP: 8.2x ⭐ EXCELLENT                              │
│ Implied Move: 8.5% | Historical: 1.0%               │
│ Liquidity: EXCELLENT                                │
│                                                     │
│ 🧠 {BULL/BEAR/NEUT} (+0.6): {1-line, max 20 words}  │
│                                                     │
│ 💡 Run `/analyze NVDA` for strategy recommendations │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ 🚨 MU - EARNINGS TODAY (AMC)          ⚠️ HIGH TRR   │
├─────────────────────────────────────────────────────┤
│ VRP: 4.2x ✓ GOOD                                    │
│ Implied Move: 6.2% | Historical: 1.0%               │
│ Liquidity: EXCELLENT                                │
│ ⚡ TRR: 3.05x → Max 50 contracts / $25k             │
│                                                     │
│ 🧠 {BULL/BEAR/NEUT} (+0.4): {1-line, max 20 words}  │
│                                                     │
│ 💡 Run `/analyze MU` for strategy recommendations   │
└─────────────────────────────────────────────────────┘

[Note: Only show TRR line for HIGH TRR tickers. Omit for NORMAL/LOW.]

📊 SUMMARY
   Alerts found: {N}
   With sentiment: {M}

⚠️ REMINDERS
   • Always check liquidity before trading
   • Use `/analyze TICKER` for full strategy
   • Never trade REJECT liquidity (lesson: $26,930 loss)
   • Respect TRR limits for HIGH tail risk tickers (lesson: $134k MU loss)

══════════════════════════════════════════════════════
```

## No Alerts Output

```
══════════════════════════════════════════════════════
🚨 TODAY'S TRADING ALERTS - {DATE}
══════════════════════════════════════════════════════

⏰ Market: [OPEN/CLOSED]

📭 NO HIGH-VRP OPPORTUNITIES TODAY

Scanned {N} tickers with earnings today:
  • VRP < 3x: {M} tickers (insufficient edge)
  • Liquidity REJECT: {R} tickers (untradeable)
  • Qualified: 0 tickers

💡 SUGGESTIONS
   • Run `/scan {tomorrow}` to plan ahead
   • Run `/whisper` to see week's best opportunities
   • Check back tomorrow morning

══════════════════════════════════════════════════════
```

## Cost Control
- Maximum 3 Perplexity calls (high-VRP alerts only)
- Cache-aware (if primed, uses cached sentiment)
