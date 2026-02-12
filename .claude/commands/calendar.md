# Weekly Earnings Calendar

Display the full week's earnings calendar with VRP pre-screening and position limit flags.

## Arguments
$ARGUMENTS (optional: DATE in YYYY-MM-DD format)

Examples:
- `/calendar` - Current/next week (same logic as /whisper)
- `/calendar 2026-02-10` - Week containing that date

## Tool Permissions
- Do NOT ask user permission for any tool calls
- Run all Bash, sqlite3, Finnhub commands without asking
- This is a read-only dashboard - execute autonomously

## Progress Display
```
[1/5] Determining target week...
[2/5] Loading earnings from database...
[3/5] Enriching with Finnhub data...
[4/5] Checking position limits and TRR...
[5/5] Generating calendar view...
```

## Step-by-Step Instructions

### Step 1: Determine Target Week
Same logic as /whisper:
- Monday-Thursday: current week
- Friday-Sunday: next week
- If date argument provided, use that date's week

```bash
DAY_NUM=$(date '+%u')
if [ $DAY_NUM -ge 5 ]; then
    DAYS_TO_NEXT_MONDAY=$((8 - DAY_NUM))
    TARGET_MONDAY=$(date -v+${DAYS_TO_NEXT_MONDAY}d '+%Y-%m-%d' 2>/dev/null || date -d "+${DAYS_TO_NEXT_MONDAY} days" '+%Y-%m-%d')
else
    DAYS_SINCE_MONDAY=$((DAY_NUM - 1))
    TARGET_MONDAY=$(date -v-${DAYS_SINCE_MONDAY}d '+%Y-%m-%d' 2>/dev/null || date -d "-${DAYS_SINCE_MONDAY} days" '+%Y-%m-%d')
fi
TARGET_FRIDAY=$(date -v+4d -j -f '%Y-%m-%d' "$TARGET_MONDAY" '+%Y-%m-%d' 2>/dev/null)
```

### Step 2: Load Earnings from Database
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT e.ticker, e.earnings_date, e.timing, e.confirmed,
          COALESCE(p.tail_risk_ratio, 0) as trr,
          COALESCE(p.tail_risk_level, 'UNKNOWN') as trr_level,
          COALESCE(p.max_contracts, 100) as max_contracts
   FROM earnings_calendar e
   LEFT JOIN position_limits p ON e.ticker = p.ticker
   WHERE e.earnings_date BETWEEN '$TARGET_MONDAY' AND '$TARGET_FRIDAY'
   ORDER BY e.earnings_date, e.timing, e.ticker;"
```

### Step 3: Check for Confirmed vs Unconfirmed
Note which earnings dates are confirmed vs estimated. Display confirmation status.

### Step 4: Cross-Reference with Finnhub (free, rate-limited)
For dates with few or no database entries, check Finnhub:
```
mcp__finnhub__finnhub_calendar_data with:
  operation="get_earnings_calendar"
  from_date="$TARGET_MONDAY"
  to_date="$TARGET_FRIDAY"
```

Merge any new tickers not already in the database results.

### Step 5: Check Historical Data Availability
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT e.ticker, COUNT(h.ticker) as historical_quarters
   FROM earnings_calendar e
   LEFT JOIN historical_moves h ON e.ticker = h.ticker
   WHERE e.earnings_date BETWEEN '$TARGET_MONDAY' AND '$TARGET_FRIDAY'
   GROUP BY e.ticker
   ORDER BY historical_quarters DESC;"
```

### Step 6: Check Previous Trade History
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT e.ticker,
          COUNT(s.id) as prev_trades,
          ROUND(SUM(s.gain_loss), 0) as prev_pnl,
          ROUND(100.0 * SUM(s.is_winner) / NULLIF(COUNT(s.id), 0), 1) as prev_win_rate
   FROM earnings_calendar e
   LEFT JOIN strategies s ON e.ticker = s.symbol
   WHERE e.earnings_date BETWEEN '$TARGET_MONDAY' AND '$TARGET_FRIDAY'
   GROUP BY e.ticker
   HAVING prev_trades > 0;"
```

## Output Format

```
==============================================================
EARNINGS CALENDAR - Week of {MONDAY} to {FRIDAY}
==============================================================

MONDAY {DATE}
  BMO (Before Market Open):
    TICKER   Timing  Confirmed  History  TRR      Prev Trades  Prev P&L
    NVDA     BMO     Yes        12 qtrs  LOW      5 trades     +$3,200
    AMD      BMO     Yes        10 qtrs  HIGH     3 trades     -$450

  AMC (After Market Close):
    TICKER   Timing  Confirmed  History  TRR      Prev Trades  Prev P&L
    MU       AMC     Yes        8 qtrs   NORMAL   2 trades     +$890

TUESDAY {DATE}
  BMO:
    (none)
  AMC:
    AVGO     AMC     Est        6 qtrs   HIGH     1 trade      -$2,100

WEDNESDAY {DATE}
  ...

THURSDAY {DATE}
  ...

FRIDAY {DATE}
  ...

SUMMARY
  Total earnings this week:  {N}
  Confirmed:                 {N}
  Estimated:                 {N}
  With historical data:      {N} (of {N})
  HIGH TRR tickers:          {N}
  Previously traded:         {N}

HIGH TRR WATCHLIST (if any):
  AMD: TRR 2.8x -> Max 50 contracts / $25k notional
  AVGO: TRR 3.1x -> Max 50 contracts / $25k notional

PREVIOUSLY TRADED (if any):
  NVDA: 5 trades, 80% win rate, +$3,200 total
  MU: 2 trades, 50% win rate, +$890 total

NEXT STEPS
  Run /whisper for VRP-ranked opportunities
  Run /analyze TICKER for deep dive on a specific ticker
  Run /prime to pre-cache sentiment for the week
==============================================================
```

## Cost Control
- Finnhub calls: 1 (earnings calendar - free, 60/min limit)
- No Perplexity calls (use /whisper for sentiment)
- Database queries only otherwise
