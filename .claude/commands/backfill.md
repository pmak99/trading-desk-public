# Backfill Sentiment Outcomes

Record post-earnings outcomes for sentiment records to enable accuracy analysis.

## Arguments
$ARGUMENTS (format: TICKER DATE | --pending | --stats)

Examples:
- `/backfill NVDA 2025-12-09` - Backfill outcome for specific ticker/date
- `/backfill --pending` - Backfill all pending outcomes (earnings already passed)
- `/backfill --stats` - Show sentiment prediction accuracy statistics

## Tool Permissions
- Do NOT ask user permission for any tool calls
- Run all Bash, sqlite3 commands without asking
- This is a data maintenance command - execute autonomously

## Progress Display
Show progress updates as you work:
```
[1/3] Verifying sentiment records exist...
[2/3] Fetching actual outcomes from 2.0 database...
[3/3] Updating sentiment_history with results...
```

## Purpose
Complete the sentiment collection loop:
1. `/collect` or `/prime` captures pre-earnings sentiment
2. `/backfill` records actual outcomes after earnings
3. Analysis shows if sentiment predicted direction correctly

## Step-by-Step Instructions

### Mode 1: Single Ticker Backfill (`/backfill TICKER DATE`)

**Step 1: Verify record exists**
```bash
# Sanitize ticker (alphanumeric only, uppercase)
TICKER=$(echo "$TICKER" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')

sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "SELECT ticker, sentiment_direction, vrp_ratio FROM sentiment_history
   WHERE ticker='$TICKER' AND earnings_date='$DATE';"
```

If not found:
```
❌ No sentiment record found for $TICKER on $DATE
   Collect sentiment first with: /collect $TICKER $DATE
```

**Step 2: Get actual outcome from 2.0 database**
```bash
sqlite3 $PROJECT_ROOT/2.0/data/ivcrush.db \
  "SELECT close_move_pct, gap_move_pct,
          CASE WHEN close_move_pct >= 0 THEN 'UP' ELSE 'DOWN' END as direction
   FROM historical_moves
   WHERE ticker='$TICKER' AND earnings_date='$DATE';"
```
**Field definitions:**
- `close_move_pct` = Total earnings move (earnings_close - prev_close) / prev_close - **USE THIS for actual move**
- `gap_move_pct` = Pre-market gap only (earnings_open - prev_close) / prev_close
- `intraday_move_pct` = Intraday only (earnings_close - earnings_open) / earnings_open

If no outcome data yet:
```
⏳ Outcome not yet available for $TICKER on $DATE
   Historical data may not be recorded yet.
   Try running: cd 2.0 && python scripts/backfill_yfinance.py $TICKER
```

**Step 3: Update sentiment_history with outcome**
```bash
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "UPDATE sentiment_history
   SET actual_move_pct = $MOVE,
       actual_direction = '$DIRECTION',
       prediction_correct = CASE
         WHEN sentiment_direction = 'bullish' AND '$DIRECTION' = 'UP' THEN 1
         WHEN sentiment_direction = 'bearish' AND '$DIRECTION' = 'DOWN' THEN 1
         WHEN sentiment_direction IN ('bullish', 'bearish') THEN 0
         ELSE NULL
       END,
       updated_at = datetime('now')
   WHERE ticker='$TICKER' AND earnings_date='$DATE';"
```

**Step 4: Optionally record trade outcome**
Ask user:
```
Did you trade $TICKER on $DATE?
  [W] WIN - Position was profitable
  [L] LOSS - Position lost money
  [S] SKIP - Did not trade
  [Enter] Skip recording trade outcome
```

If answered, update:
```bash
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "UPDATE sentiment_history SET trade_outcome = '$OUTCOME'
   WHERE ticker='$TICKER' AND earnings_date='$DATE';"
```

### Mode 2: Batch Backfill (`/backfill --pending`)

**Step 1: Get all pending records**
```bash
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "SELECT ticker, earnings_date, sentiment_direction
   FROM sentiment_history
   WHERE actual_move_pct IS NULL
   AND earnings_date < date('now')
   ORDER BY earnings_date;"
```

**Step 2: For each pending record, try to backfill**
Loop through and attempt to get outcome from 2.0 database.

**Step 3: Report results**
```
Backfill Results:
  ✓ NVDA 2025-12-09: UP 5.2% (predicted: bullish ✓)
  ✓ AMD 2025-12-09: DOWN 3.1% (predicted: neutral -)
  ✗ ORCL 2025-12-10: No data yet

  Backfilled: 2
  Still pending: 1
```

### Mode 3: Statistics (`/backfill --stats`)

**Step 1: Query accuracy stats**
```bash
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "SELECT
     COUNT(*) as total,
     SUM(CASE WHEN actual_move_pct IS NOT NULL THEN 1 ELSE 0 END) as with_outcomes,
     SUM(CASE WHEN prediction_correct = 1 THEN 1 ELSE 0 END) as correct,
     SUM(CASE WHEN prediction_correct = 0 THEN 1 ELSE 0 END) as incorrect
   FROM sentiment_history;"
```

**Step 2: Query by direction**
```bash
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "SELECT sentiment_direction,
          COUNT(*) as total,
          SUM(CASE WHEN prediction_correct = 1 THEN 1 ELSE 0 END) as correct,
          AVG(actual_move_pct) as avg_move
   FROM sentiment_history
   WHERE actual_move_pct IS NOT NULL
   GROUP BY sentiment_direction;"
```

**Step 3: Query trade outcomes**
```bash
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "SELECT trade_outcome, COUNT(*) as cnt
   FROM sentiment_history
   WHERE trade_outcome IS NOT NULL
   GROUP BY trade_outcome;"
```

## Output Format

### Single Backfill
```
══════════════════════════════════════════════════════
📊 BACKFILL OUTCOME: $TICKER $DATE
══════════════════════════════════════════════════════

Pre-Earnings Sentiment:
  Direction: BULLISH
  Score: +0.6
  VRP: 3.87x

Post-Earnings Outcome:
  Actual Move: 5.2%
  Direction: UP ✓

Prediction: CORRECT ✓
  (Bullish sentiment → Stock went UP)

Trade Outcome: [recorded if provided]

✅ Record updated
══════════════════════════════════════════════════════
```

### Batch Backfill
```
══════════════════════════════════════════════════════
📊 BATCH BACKFILL: Pending Outcomes
══════════════════════════════════════════════════════

Processing 5 pending records...

  ✓ NVDA 2025-12-09: UP 5.2% (bullish → CORRECT)
  ✓ AMD 2025-12-09: DOWN 3.1% (neutral → N/A)
  ✓ AVGO 2025-12-10: UP 8.4% (bullish → CORRECT)
  ✗ ORCL 2025-12-10: No outcome data
  ✗ GME 2025-12-11: No outcome data

Summary:
  Backfilled: 3
  Still pending: 2

Run again after historical data is updated.
══════════════════════════════════════════════════════
```

### Statistics
```
══════════════════════════════════════════════════════
📊 SENTIMENT PREDICTION ACCURACY
══════════════════════════════════════════════════════

Overall Statistics:
  Total records: 45
  With outcomes: 32
  Pending: 13

Prediction Accuracy:
  Correct: 21 / 28 predictions = 75.0%
  (Only bullish/bearish counted, neutral excluded)

By Direction:
  ┌───────────┬───────┬─────────┬──────────┬──────────┐
  │ Sentiment │ Total │ Correct │ Accuracy │ Avg Move │
  ├───────────┼───────┼─────────┼──────────┼──────────┤
  │ Bullish   │ 18    │ 14      │ 77.8%    │ 4.2%     │
  │ Bearish   │ 10    │ 7       │ 70.0%    │ 5.1%     │
  │ Neutral   │ 4     │ N/A     │ N/A      │ 3.8%     │
  └───────────┴───────┴─────────┴──────────┴──────────┘

Trade Outcomes (where recorded):
  WIN:  12
  LOSS: 5
  SKIP: 8
  Win Rate: 70.6%

💡 INSIGHTS
  • Bullish predictions more reliable than bearish
  • Sentiment adds ~25% edge over random (50%)
  • Consider higher position sizing on strong bullish
══════════════════════════════════════════════════════
```

## Notes
- Outcomes require 2.0 historical_moves data (may need backfill_yfinance.py)
- Trade outcomes are optional but valuable for ROI analysis
- Need ~30 records with outcomes for statistically meaningful accuracy
