# Portfolio Risk Dashboard

Assess overall portfolio risk: sector concentration, TRR exposure, strategy mix, and loss patterns.

## Arguments
$ARGUMENTS (optional: period in days, default 90)

Examples:
- `/risk` - Risk assessment for last 90 days
- `/risk 30` - Risk assessment for last 30 days
- `/risk 365` - Full year risk assessment

## Tool Permissions
- Do NOT ask user permission for any tool calls
- Run all Bash, sqlite3, Read commands without asking
- This is a read-only analysis - execute autonomously

## Progress Display
```
[1/6] Loading strategy data...
[2/6] Analyzing TRR exposure...
[3/6] Checking strategy concentration...
[4/6] Analyzing loss patterns...
[5/6] Calculating drawdown metrics...
[6/6] Generating risk report...
```

## Step-by-Step Instructions

### Step 1: Parse Period Argument
- Default: 90 days
- If argument provided, use as number of days

### Step 2: TRR Exposure Analysis
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT CASE
            WHEN trr_at_entry > 2.5 THEN 'HIGH'
            WHEN trr_at_entry >= 1.5 THEN 'NORMAL'
            WHEN trr_at_entry IS NOT NULL THEN 'LOW'
            ELSE 'UNKNOWN'
          END as trr_level,
          COUNT(*) as trades,
          ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM strategies WHERE sale_date >= date('now', '-$DAYS days')), 1) as pct_of_trades,
          ROUND(SUM(gain_loss), 0) as total_pnl,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
          ROUND(AVG(gain_loss), 0) as avg_pnl
   FROM strategies
   WHERE sale_date >= date('now', '-$DAYS days')
   GROUP BY trr_level
   ORDER BY CASE trr_level WHEN 'HIGH' THEN 1 WHEN 'NORMAL' THEN 2 WHEN 'LOW' THEN 3 ELSE 4 END;"
```

### Step 3: Strategy Type Concentration
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT strategy_type,
          COUNT(*) as trades,
          ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM strategies WHERE sale_date >= date('now', '-$DAYS days')), 1) as pct,
          ROUND(SUM(gain_loss), 0) as total_pnl,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate
   FROM strategies
   WHERE sale_date >= date('now', '-$DAYS days')
   GROUP BY strategy_type
   ORDER BY trades DESC;"
```

### Step 4: Ticker Concentration (Top Exposure)
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT symbol,
          COUNT(*) as trades,
          ROUND(100.0 * COUNT(*) / (SELECT COUNT(*) FROM strategies WHERE sale_date >= date('now', '-$DAYS days')), 1) as pct_of_trades,
          ROUND(SUM(gain_loss), 0) as total_pnl,
          ROUND(SUM(CASE WHEN NOT is_winner THEN ABS(gain_loss) ELSE 0 END), 0) as total_losses
   FROM strategies
   WHERE sale_date >= date('now', '-$DAYS days')
   GROUP BY symbol
   ORDER BY total_losses DESC
   LIMIT 10;"
```

### Step 5: Loss Pattern Analysis
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT symbol, strategy_type, gain_loss, trade_type, campaign_id,
          acquired_date, sale_date
   FROM strategies
   WHERE sale_date >= date('now', '-$DAYS days')
     AND NOT is_winner
   ORDER BY gain_loss ASC
   LIMIT 10;"
```

### Step 6: Trade Type Risk (NEW vs REPAIR vs ROLL)
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT COALESCE(trade_type, 'NEW') as trade_type,
          COUNT(*) as trades,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
          ROUND(SUM(gain_loss), 0) as total_pnl,
          ROUND(AVG(CASE WHEN NOT is_winner THEN gain_loss END), 0) as avg_loss
   FROM strategies
   WHERE sale_date >= date('now', '-$DAYS days')
   GROUP BY trade_type
   ORDER BY total_pnl DESC;"
```

### Step 7: Drawdown Analysis
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "WITH running AS (
     SELECT sale_date,
            SUM(gain_loss) OVER (ORDER BY sale_date) as cumulative_pnl
     FROM strategies
     WHERE sale_date >= date('now', '-$DAYS days')
   )
   SELECT MIN(cumulative_pnl) as max_drawdown,
          MAX(cumulative_pnl) as peak_pnl,
          (SELECT cumulative_pnl FROM running ORDER BY sale_date DESC LIMIT 1) as current_pnl
   FROM running;"
```

### Step 8: Consecutive Losses
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "WITH numbered AS (
     SELECT symbol, sale_date, is_winner, gain_loss,
            ROW_NUMBER() OVER (ORDER BY sale_date) as rn,
            SUM(CASE WHEN is_winner THEN 1 ELSE 0 END) OVER (ORDER BY sale_date) as win_group
     FROM strategies
     WHERE sale_date >= date('now', '-$DAYS days')
   )
   SELECT COUNT(*) as streak, MIN(sale_date) as from_date, MAX(sale_date) as to_date,
          ROUND(SUM(gain_loss), 0) as streak_loss
   FROM numbered
   WHERE NOT is_winner
   GROUP BY rn - win_group
   ORDER BY streak DESC
   LIMIT 3;"
```

### Step 9: HIGH TRR Tickers Currently in Position Limits
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT ticker, tail_risk_ratio, tail_risk_level, max_contracts, max_notional,
          max_move, avg_move
   FROM position_limits
   WHERE tail_risk_level = 'HIGH'
   ORDER BY tail_risk_ratio DESC;"
```

## Output Format

```
==============================================================
PORTFOLIO RISK DASHBOARD ({N}-Day Window)
==============================================================

TRR EXPOSURE
  Level    Trades  % of Total  Total P&L  Win Rate  Avg P&L
  HIGH     {N}     {X}%        -${X,XXX}  {X}%      -${XXX}
  NORMAL   {N}     {X}%        -${X,XXX}  {X}%      -${XXX}
  LOW      {N}     {X}%        +${X,XXX}  {X}%      +${XXX}

  [WARNING] if HIGH TRR > 30% of trades
  [OK] if HIGH TRR <= 30% of trades

STRATEGY CONCENTRATION
  Type           Trades  %      Total P&L  Win Rate
  SINGLE         {N}     {X}%   +${X,XXX}  {X}%
  SPREAD         {N}     {X}%   +${X,XXX}  {X}%
  STRANGLE       {N}     {X}%   -${X,XXX}  {X}%
  IRON_CONDOR    {N}     {X}%   -${X,XXX}  {X}%

  [OK] if SINGLE >= 50% of trades (preferred strategy)
  [WARNING] if SINGLE < 50%

TICKER CONCENTRATION (Top Losers)
  Symbol  Trades  % of Total  Total P&L  Total Losses
  {TICK}  {N}     {X}%        -${X,XXX}  ${X,XXX}
  ...

  [WARNING] if any ticker > 20% of trades (over-concentrated)

TRADE TYPE RISK
  Type     Trades  Win Rate  Total P&L  Avg Loss
  NEW      {N}     {X}%      +${X,XXX}  -${XXX}
  REPAIR   {N}     {X}%      -${X,XXX}  -${XXX}
  ROLL     {N}     {X}%      -${X,XXX}  -${XXX}

  [CRITICAL] if any ROLL trades exist (0% historical success rate)
  [WARNING] if REPAIR win rate < 25%

WORST LOSSES
  1. {TICKER} {STRATEGY} on {DATE}: -${X,XXX} ({TRADE_TYPE})
  2. ...
  (Top 5 losses)

DRAWDOWN
  Peak P&L:      +${X,XXX}
  Max Drawdown:  -${X,XXX}
  Current P&L:   +${X,XXX}

LONGEST LOSING STREAKS
  1. {N} trades ({DATE} to {DATE}): -${X,XXX}
  2. ...

HIGH TRR WATCHLIST
  {TICKER}: TRR {X.XX}x -> Max {N} contracts / ${X,XXX} notional
  ...

RISK SCORE: {LOW/MODERATE/HIGH/CRITICAL}

Factors:
  [check/warning/critical] TRR exposure: {assessment}
  [check/warning/critical] Strategy mix: {assessment}
  [check/warning/critical] Ticker concentration: {assessment}
  [check/warning/critical] Trade type discipline: {assessment}
  [check/warning/critical] Drawdown: {assessment}

RECOMMENDATIONS
  1. {Specific action based on data}
  2. {Specific action based on data}
  3. {Specific action based on data}
==============================================================
```

## Risk Score Calculation
- **LOW**: All checks pass, no warnings
- **MODERATE**: 1-2 warnings
- **HIGH**: 3+ warnings or any CRITICAL
- **CRITICAL**: ROLL trades present OR max drawdown > 50% of peak OR HIGH TRR > 50% of trades

## Cost Control
- No MCP usage (local data only)
- Database queries and in-context analysis only
