# Historical Earnings Analysis

Visualize historical earnings moves with statistical analysis and pattern insights.

## Arguments
$ARGUMENTS (format: TICKER - required)

Examples:
- `/history NVDA` - Show NVDA's earnings history
- `/history AMD` - Show AMD's earnings history

## Tool Permissions
- Do NOT ask user permission for any tool calls
- Run all Bash, sqlite3 commands without asking
- This is a visualization command - execute autonomously

## Progress Display
```
[1/4] Fetching historical earnings data...
[2/4] Calculating statistics...
[3/4] Checking position limits and TRR...
[4/4] Analyzing patterns...
```

## Step-by-Step Instructions

### Step 1: Parse Ticker Argument
- Ticker is REQUIRED
- If not provided:
  ```
  Ticker required. Usage: /history TICKER
     Example: /history NVDA
  ```

Sanitize: `TICKER=$(echo "$RAW" | tr '[:lower:]' '[:upper:]' | tr -cd '[:alnum:]')`

### Step 2: Query Historical Moves
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT earnings_date,
          ROUND(gap_move_pct, 2) as gap_move,
          ROUND(close_move_pct, 2) as close_move,
          ROUND(intraday_move_pct, 2) as intraday,
          CASE WHEN close_move_pct >= 0 THEN 'UP' ELSE 'DOWN' END as direction
   FROM historical_moves
   WHERE ticker='$TICKER'
   ORDER BY earnings_date DESC;"
```

If no data:
```
NO HISTORICAL DATA

No earnings history found for {TICKER} in the database.

Possible reasons:
  New ticker or recent IPO
  Ticker symbol changed
  Not yet tracked

Suggestions:
  Run /analyze {TICKER} to check current VRP
  Run /maintenance backfill to add historical data
```

### Step 3: Calculate Statistics
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT COUNT(*) as quarters,
          ROUND(AVG(ABS(gap_move_pct)), 2) as mean_move,
          ROUND(MAX(ABS(gap_move_pct)), 2) as max_move,
          ROUND(MIN(ABS(gap_move_pct)), 2) as min_move,
          ROUND(SUM(CASE WHEN gap_move_pct >= 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as up_pct,
          ROUND(SUM(CASE WHEN gap_move_pct < 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) as down_pct
   FROM historical_moves
   WHERE ticker='$TICKER';"
```

### Step 4: Check Position Limits / TRR
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT tail_risk_ratio, tail_risk_level, max_contracts, max_notional,
          avg_move, max_move, num_quarters
   FROM position_limits
   WHERE ticker='$TICKER';"
```

### Step 5: Check Past Trade Performance
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT COUNT(*) as trades,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
          ROUND(SUM(gain_loss), 0) as total_pnl,
          strategy_type
   FROM strategies
   WHERE symbol='$TICKER'
   GROUP BY strategy_type;"
```

### Step 6: AI Pattern Analysis

Using Claude's built-in analysis (no MCP cost), identify:

1. **Directional Bias** - Strong UP bias (>65%), Strong DOWN bias (>65%), or Neutral
2. **Move Consistency** - Tight (std dev < 1.5%), Moderate (1.5-3%), Volatile (>3%)
3. **Notable Outliers** - Moves > 2 standard deviations
4. **Trend Analysis** - Are moves getting larger or smaller over time?
5. **Trading Implications** - Strategy suggestions based on historical behavior

## Output Format

```
==============================================================
HISTORICAL EARNINGS: {TICKER}
==============================================================

EARNINGS MOVE HISTORY (Last {N} quarters)

Date          Gap Move   Close Move   Direction
2026-01-30    +3.2%      +4.1%        UP
2025-10-30    -5.1%      -3.8%        DOWN
2025-07-24    +2.8%      +3.2%        UP
2025-04-24    +1.5%      +2.0%        UP
2025-01-30    -8.2%      -6.5%        DOWN
...

STATISTICS
  Quarters tracked:  {N}
  Mean Move (abs):   {X.X}%
  Max Move:          {X.X}%
  Min Move:          {X.X}%

DIRECTIONAL ANALYSIS
  UP moves:    {X} ({Y}%)
  DOWN moves:  {X} ({Y}%)

TAIL RISK
  TRR: {X.XX}x ({HIGH/NORMAL/LOW})
  Max contracts: {N}
  [If HIGH: "Elevated tail risk - max historical move {X.X}% vs avg {X.X}%"]

PAST TRADES (if any)
  Strategy    Trades  Win Rate  Total P&L
  {type}      {N}     {X}%      ${X,XXX}

AI PATTERN ANALYSIS

Directional Bias: {Bullish/Bearish/Neutral}
  {Explanation}

Move Consistency: {Tight/Moderate/Volatile}
  {Explanation}

Notable Outliers:
  {Date}: {X.X}% move - {potential cause if determinable}

Trend Observation:
  {Are moves increasing/decreasing?}

TRADING IMPLICATIONS

Based on {TICKER}'s historical behavior:
  Strategy Suggestion: {type based on bias/consistency}
  Position Sizing: {standard/reduced based on TRR}
  Risk Warnings: {any specific concerns}

==============================================================
```

## Cost Control
- No Perplexity calls (uses Claude's built-in analysis)
- Database queries only
- Pure visualization + AI insight
