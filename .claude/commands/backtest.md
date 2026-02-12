# Backtest Performance Analysis

Analyze trading performance from the strategies database with insights and recommendations.

## Arguments
$ARGUMENTS (format: [TICKER] - optional)

Examples:
- `/backtest` - Analyze all trades
- `/backtest NVDA` - Analyze NVDA trades only

## Tool Permissions
- Do NOT ask user permission for any tool calls
- Run all Bash, sqlite3, Read commands without asking
- This is an analysis command - execute autonomously

## Progress Display
```
[1/5] Loading strategy data from database...
[2/5] Calculating overall performance metrics...
[3/5] Breaking down by strategy type and TRR...
[4/5] Analyzing trade types (NEW/REPAIR/ROLL)...
[5/5] Generating insights and recommendations...
```

## Step-by-Step Instructions

### Step 1: Parse Arguments
- If ticker provided, filter to that ticker
- If no ticker, analyze all trades

### Step 2: Query Strategy Performance

**Overall metrics:**
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT COUNT(*) as trades,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
          ROUND(SUM(gain_loss), 0) as total_pnl,
          ROUND(AVG(CASE WHEN is_winner THEN gain_loss END), 0) as avg_win,
          ROUND(AVG(CASE WHEN NOT is_winner THEN gain_loss END), 0) as avg_loss,
          ROUND(MAX(gain_loss), 0) as largest_win,
          ROUND(MIN(gain_loss), 0) as largest_loss
   FROM strategies
   WHERE 1=1 $TICKER_FILTER;"
```

Replace `$TICKER_FILTER` with `AND symbol = 'TICKER'` if ticker provided.

**By strategy type:**
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT strategy_type,
          COUNT(*) as trades,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
          ROUND(SUM(gain_loss), 0) as total_pnl,
          ROUND(AVG(gain_loss), 0) as avg_pnl
   FROM strategies
   WHERE 1=1 $TICKER_FILTER
   GROUP BY strategy_type
   ORDER BY total_pnl DESC;"
```

**By TRR level (if trr_at_entry populated):**
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT CASE
            WHEN trr_at_entry > 2.5 THEN 'HIGH'
            WHEN trr_at_entry >= 1.5 THEN 'NORMAL'
            WHEN trr_at_entry IS NOT NULL THEN 'LOW'
            ELSE 'UNKNOWN'
          END as trr_level,
          COUNT(*) as trades,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
          ROUND(SUM(gain_loss), 0) as total_pnl
   FROM strategies
   WHERE 1=1 $TICKER_FILTER
   GROUP BY trr_level
   ORDER BY total_pnl DESC;"
```

**By trade type (NEW/REPAIR/ROLL):**
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT COALESCE(trade_type, 'NEW') as trade_type,
          COUNT(*) as trades,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
          ROUND(SUM(gain_loss), 0) as total_pnl
   FROM strategies
   WHERE 1=1 $TICKER_FILTER
   GROUP BY trade_type
   ORDER BY total_pnl DESC;"
```

**Top/bottom tickers:**
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT symbol, COUNT(*) as trades,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
          ROUND(SUM(gain_loss), 0) as total_pnl,
          ROUND(AVG(gain_loss), 0) as avg_pnl
   FROM strategies
   GROUP BY symbol
   HAVING trades >= 2
   ORDER BY total_pnl DESC
   LIMIT 5;"
```

```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT symbol, COUNT(*) as trades,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
          ROUND(SUM(gain_loss), 0) as total_pnl,
          ROUND(AVG(gain_loss), 0) as avg_pnl
   FROM strategies
   GROUP BY symbol
   HAVING trades >= 2
   ORDER BY total_pnl ASC
   LIMIT 5;"
```

**Campaign analysis (linked trades):**
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT campaign_id, SUM(gain_loss) as total,
          GROUP_CONCAT(trade_type || ': $' || ROUND(gain_loss, 0)) as chain
   FROM strategies
   WHERE campaign_id IS NOT NULL $TICKER_FILTER
   GROUP BY campaign_id
   ORDER BY total
   LIMIT 10;"
```

### Step 3: Run Backtest Report Script (if available)
```bash
"$PROJECT_ROOT/core/venv/bin/python" "$PROJECT_ROOT/scripts/backtest_report.py" $TICKER 2>/dev/null
```

If script fails or doesn't produce output, use the DB queries above (they contain all the data needed).

### Step 4: AI Performance Analysis

Using Claude's built-in analysis (no MCP cost), provide insights on:

1. **Edge Validation** - Does higher VRP correlate with better results?
2. **Strategy Effectiveness** - SINGLE (64% win) vs SPREAD (52% win) vs others
3. **TRR Impact** - HIGH TRR significant losses, LOW TRR strong profit
4. **Trade Type Analysis** - NEW vs REPAIR vs ROLL performance
5. **Campaign Analysis** - Repairs reduce loss but rarely save campaigns. Rolls always lose.
6. **Actionable Recommendations**

## Output Format

```
==============================================================
BACKTEST REPORT {[TICKER] or "ALL TRADES"}
==============================================================

OVERALL PERFORMANCE
  Total Trades:   {N}
  Win Rate:       {X.X}%
  Total P&L:      ${X,XXX}
  Average Win:    ${XXX}
  Average Loss:   -${XXX}
  Profit Factor:  {X.XX}
  Largest Win:    ${X,XXX}
  Largest Loss:   -${X,XXX}

BY STRATEGY TYPE
  Strategy      Trades  Win Rate  Total P&L  Avg P&L
  SINGLE        {N}     {X}%      ${X,XXX}   ${XXX}
  SPREAD        {N}     {X}%      ${X,XXX}   ${XXX}
  STRANGLE      {N}     {X}%      ${X,XXX}   ${XXX}
  IRON_CONDOR   {N}     {X}%      ${X,XXX}   ${XXX}

BY TRR LEVEL
  Level    Trades  Win Rate  Total P&L
  HIGH     {N}     {X}%      -${X,XXX}
  NORMAL   {N}     {X}%      -${X,XXX}
  LOW      {N}     {X}%      +${X,XXX}

BY TRADE TYPE
  Type      Trades  Win Rate  Total P&L
  NEW       {N}     {X}%      ${X,XXX}
  REPAIR    {N}     {X}%      ${X,XXX}
  ROLL      {N}     {X}%      -${X,XXX}

TOP 5 PERFORMERS (by total P&L)
  1. {TICKER} - {N} trades, {X}% win rate, ${X,XXX} total
  2. ...

BOTTOM 5 PERFORMERS
  1. {TICKER} - {N} trades, {X}% win rate, -${X,XXX} total
  2. ...

AI ANALYSIS & INSIGHTS

Edge Validation:
  {Analysis of strategy type and TRR correlation}

Key Lessons:
  - SINGLE options outperform spreads (64% vs 52% win rate)
  - LOW TRR tickers most profitable, HIGH TRR significant losses
  - Repairs reduce loss but rarely save campaigns
  - Rolls always make things worse (0% success rate)

Recommendations:
  1. {Specific improvement based on data}
  2. {Specific improvement based on data}
  3. {Specific improvement based on data}

==============================================================
```

## No Data Output

```
==============================================================
BACKTEST REPORT
==============================================================

NO TRADE DATA FOUND

No strategy data available for analysis.

To populate trade history:
  1. Run /journal to parse Fidelity statements
  2. Or add trades to the strategies table

Once you have trade data, run /backtest again.
==============================================================
```

## Cost Control
- No Perplexity calls (uses Claude's built-in analysis)
- Database queries only
- AI insights generated in-context
