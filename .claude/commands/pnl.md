# P&L Summary

Quick profit & loss summary for any time period with breakdowns by strategy, ticker, and month.

## Arguments
$ARGUMENTS (optional: PERIOD)

Examples:
- `/pnl` - Current month P&L
- `/pnl week` - This week's P&L
- `/pnl month` - Current month
- `/pnl ytd` - Year to date
- `/pnl 2025` - Full year 2025
- `/pnl 2025-Q4` - Q4 2025
- `/pnl 30` - Last 30 days

## Tool Permissions
- Do NOT ask user permission for any tool calls
- Run all Bash, sqlite3, Read commands without asking
- This is a read-only dashboard - execute autonomously

## Progress Display
```
[1/4] Parsing period...
[2/4] Loading trade data...
[3/4] Calculating breakdowns...
[4/4] Generating P&L report...
```

## Step-by-Step Instructions

### Step 1: Parse Period Argument
Convert argument to date range:
- `week` or no argument: `date('now', 'weekday 0', '-6 days')` to now
- `month`: first of current month to now
- `ytd`: January 1 of current year to now
- `2025`: `2025-01-01` to `2025-12-31`
- `2025-Q1`: `2025-01-01` to `2025-03-31` (Q2: Apr-Jun, Q3: Jul-Sep, Q4: Oct-Dec)
- Number (e.g. `30`): `date('now', '-30 days')` to now
- Default (no argument): current month

### Step 2: Overall P&L
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT COUNT(*) as trades,
          SUM(is_winner) as wins,
          COUNT(*) - SUM(is_winner) as losses,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
          ROUND(SUM(gain_loss), 0) as total_pnl,
          ROUND(AVG(gain_loss), 0) as avg_pnl,
          ROUND(AVG(CASE WHEN is_winner THEN gain_loss END), 0) as avg_win,
          ROUND(AVG(CASE WHEN NOT is_winner THEN gain_loss END), 0) as avg_loss,
          ROUND(MAX(gain_loss), 0) as best_trade,
          ROUND(MIN(gain_loss), 0) as worst_trade
   FROM strategies
   WHERE sale_date BETWEEN '$START_DATE' AND '$END_DATE';"
```

### Step 3: By Strategy Type
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT strategy_type,
          COUNT(*) as trades,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
          ROUND(SUM(gain_loss), 0) as total_pnl
   FROM strategies
   WHERE sale_date BETWEEN '$START_DATE' AND '$END_DATE'
   GROUP BY strategy_type
   ORDER BY total_pnl DESC;"
```

### Step 4: By Ticker (Top 5 Winners / Bottom 5 Losers)
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT symbol,
          COUNT(*) as trades,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
          ROUND(SUM(gain_loss), 0) as total_pnl
   FROM strategies
   WHERE sale_date BETWEEN '$START_DATE' AND '$END_DATE'
   GROUP BY symbol
   ORDER BY total_pnl DESC
   LIMIT 5;"
```

```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT symbol,
          COUNT(*) as trades,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
          ROUND(SUM(gain_loss), 0) as total_pnl
   FROM strategies
   WHERE sale_date BETWEEN '$START_DATE' AND '$END_DATE'
   GROUP BY symbol
   ORDER BY total_pnl ASC
   LIMIT 5;"
```

### Step 5: Monthly Breakdown (if period > 30 days)
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT strftime('%Y-%m', sale_date) as month,
          COUNT(*) as trades,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
          ROUND(SUM(gain_loss), 0) as month_pnl,
          ROUND(SUM(SUM(gain_loss)) OVER (ORDER BY strftime('%Y-%m', sale_date)), 0) as cumulative
   FROM strategies
   WHERE sale_date BETWEEN '$START_DATE' AND '$END_DATE'
   GROUP BY month
   ORDER BY month;"
```

### Step 6: Trade Type Breakdown
```bash
sqlite3 "$PROJECT_ROOT/2.0/data/ivcrush.db" \
  "SELECT COALESCE(trade_type, 'NEW') as type,
          COUNT(*) as trades,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
          ROUND(SUM(gain_loss), 0) as total_pnl
   FROM strategies
   WHERE sale_date BETWEEN '$START_DATE' AND '$END_DATE'
   GROUP BY trade_type
   ORDER BY total_pnl DESC;"
```

## Output Format

```
==============================================================
P&L SUMMARY: {PERIOD DESCRIPTION}
==============================================================

{START_DATE} to {END_DATE}

OVERALL
  Trades:      {N} ({W}W / {L}L)
  Win Rate:    {X.X}%
  Total P&L:   ${X,XXX}
  Avg Trade:   ${XXX}
  Avg Win:     +${XXX}
  Avg Loss:    -${XXX}
  Best Trade:  +${X,XXX}
  Worst Trade: -${X,XXX}

BY STRATEGY
  Type           Trades  Win Rate  P&L
  SINGLE         {N}     {X}%      ${X,XXX}
  SPREAD         {N}     {X}%      ${X,XXX}
  STRANGLE       {N}     {X}%      ${X,XXX}
  IRON_CONDOR    {N}     {X}%      ${X,XXX}

BY TRADE TYPE
  Type     Trades  Win Rate  P&L
  NEW      {N}     {X}%      ${X,XXX}
  REPAIR   {N}     {X}%      ${X,XXX}
  ROLL     {N}     {X}%      ${X,XXX}

TOP 5 WINNERS
  1. {TICKER}: {N} trades, {X}% win, +${X,XXX}
  2. ...

BOTTOM 5 LOSERS
  1. {TICKER}: {N} trades, {X}% win, -${X,XXX}
  2. ...

MONTHLY BREAKDOWN (if applicable)
  Month      Trades  Win Rate  P&L       Cumulative
  2025-10    {N}     {X}%      +${X,XXX}  +${X,XXX}
  2025-11    {N}     {X}%      -${X,XXX}  +${X,XXX}
  2025-12    {N}     {X}%      +${X,XXX}  +${X,XXX}
==============================================================
```

## No Data Output
```
==============================================================
P&L SUMMARY: {PERIOD DESCRIPTION}
==============================================================

No trades found for this period.

To populate: /journal FILE to parse Fidelity statements.
==============================================================
```

## Cost Control
- No MCP usage (local data only)
- Database queries only
