# Today's Trading Alerts

Find today's high-VRP trading opportunities with sentiment analysis.

## Arguments
None - automatically uses today's date

## Purpose
Quick command to check if there are any tradeable opportunities TODAY.
Run this after market open to see what's actionable.

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
cd $PROJECT_ROOT/2.0 && ./trade.sh scan $(date +%Y-%m-%d)
```

Alternative if custom script exists:
```bash
cd $PROJECT_ROOT && python scripts/check_alerts.py
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

### Step 4: Add Sentiment for Alerts (Conditional)

For EACH alert (max 3):

**4a. Check sentiment cache:**
```bash
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "SELECT sentiment, source FROM sentiment_cache WHERE ticker='$TICKER' AND date='$(date +%Y-%m-%d)' ORDER BY CASE source WHEN 'perplexity' THEN 0 ELSE 1 END LIMIT 1;"
```

**4b. If cache miss, use fallback chain:**
1. Check budget (< 40 calls)
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
│ 🚨 AMD - EARNINGS TODAY (BMO)                       │
├─────────────────────────────────────────────────────┤
│ VRP: 6.1x ⭐ GOOD                                   │
│ Implied Move: 6.2% | Historical: 1.0%               │
│ Liquidity: EXCELLENT                                │
│                                                     │
│ 🧠 {BULL/BEAR/NEUT} (+0.4): {1-line, max 20 words}  │
│                                                     │
│ 💡 Run `/analyze AMD` for strategy recommendations  │
└─────────────────────────────────────────────────────┘

📊 SUMMARY
   Alerts found: {N}
   With sentiment: {M}

⚠️ REMINDERS
   • Always check liquidity before trading
   • Use `/analyze TICKER` for full strategy
   • Never trade REJECT liquidity (lesson: significant loss)

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
