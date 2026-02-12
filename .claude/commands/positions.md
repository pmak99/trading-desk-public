# Open Positions Dashboard

Show current open positions from the strategies database with P&L and risk exposure.

## Arguments
$ARGUMENTS (optional: TICKER)

Examples:
- `/positions` - Show all open/recent positions
- `/positions NVDA` - Show only NVDA positions

## Tool Permissions
- Do NOT ask user permission for any tool calls
- Run all Bash, sqlite3, Read commands without asking
- This is a read-only dashboard - execute autonomously

## Progress Display
```
[1/4] Loading open positions...
[2/4] Checking campaign chains...
[3/4] Calculating exposure summary...
[4/4] Generating dashboard...
```

## Step-by-Step Instructions

### Step 1: Parse Arguments
- If ticker provided, filter to that ticker only
- If no ticker, show all positions

### Step 2: Load Recent Strategies (Open/Recent Positions)
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT s.symbol, s.strategy_type, s.acquired_date, s.sale_date, s.days_held,
          s.quantity, s.net_credit, s.net_debit, s.gain_loss, s.is_winner,
          s.trade_type, s.campaign_id, s.trr_at_entry, s.position_limit_at_entry,
          s.expiration, s.earnings_date
   FROM strategies s
   WHERE 1=1 $TICKER_FILTER
   ORDER BY s.sale_date DESC
   LIMIT 30;"
```

Replace `$TICKER_FILTER` with `AND s.symbol = 'TICKER'` if ticker provided.

### Step 3: Load Campaign Chains (Linked Trades)
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT campaign_id, symbol,
          COUNT(*) as legs,
          SUM(gain_loss) as campaign_pnl,
          GROUP_CONCAT(trade_type || ': $' || ROUND(gain_loss, 0), ' -> ') as chain,
          MIN(acquired_date) as opened,
          MAX(sale_date) as closed
   FROM strategies
   WHERE campaign_id IS NOT NULL $TICKER_FILTER
   GROUP BY campaign_id
   ORDER BY MAX(sale_date) DESC
   LIMIT 10;"
```

### Step 4: Exposure Summary
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT symbol,
          COUNT(*) as total_trades,
          SUM(CASE WHEN trade_type = 'NEW' THEN 1 ELSE 0 END) as new_trades,
          SUM(CASE WHEN trade_type = 'REPAIR' THEN 1 ELSE 0 END) as repairs,
          SUM(CASE WHEN trade_type = 'ROLL' THEN 1 ELSE 0 END) as rolls,
          ROUND(SUM(gain_loss), 0) as total_pnl,
          ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate
   FROM strategies
   WHERE sale_date >= date('now', '-30 days') $TICKER_FILTER
   GROUP BY symbol
   ORDER BY total_pnl DESC;"
```

### Step 5: Check Position Limits
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT p.ticker, p.tail_risk_ratio, p.tail_risk_level,
          p.max_contracts, p.max_notional
   FROM position_limits p
   WHERE p.ticker IN (
     SELECT DISTINCT symbol FROM strategies
     WHERE sale_date >= date('now', '-30 days') $TICKER_FILTER
   )
   ORDER BY p.tail_risk_ratio DESC;"
```

### Step 6: Trade Journal Details (if ticker specified)
If a specific ticker was requested:
```bash
sqlite3 "$PROJECT_ROOT/core/data/ivcrush.db" \
  "SELECT tj.symbol, tj.option_type, tj.strike, tj.expiration,
          tj.quantity, tj.cost_basis, tj.proceeds, tj.gain_loss,
          tj.acquired_date, tj.sale_date
   FROM trade_journal tj
   WHERE tj.symbol = '$TICKER'
   ORDER BY tj.sale_date DESC
   LIMIT 20;"
```

## Output Format

```
==============================================================
POSITIONS DASHBOARD {[TICKER] or "ALL"}
==============================================================

RECENT TRADES (Last 30 Days)

  Symbol  Type     Strategy    Date        P&L      TRR    Campaign
  NVDA    NEW      SINGLE      2026-02-03  +$1,250  LOW
  AMD     NEW      SPREAD      2026-02-01  -$450    NORM
  MU      NEW      SINGLE      2026-01-28  +$890    HIGH   camp_001
  MU      REPAIR   SPREAD      2026-01-30  -$200    HIGH   camp_001

CAMPAIGN CHAINS (if any):
  camp_001 (MU): NEW: $890 -> REPAIR: -$200 = Net: +$690

30-DAY EXPOSURE BY TICKER

  Symbol  Trades  New  Repair  Roll  Total P&L  Win Rate
  NVDA    5       4    1       0     +$3,200    80.0%
  AMD     3       3    0       0     -$450      ~33%
  MU      4       2    2       0     +$1,200    50.0%

POSITION LIMITS (HIGH TRR Only):
  MU: TRR 2.8x (HIGH) -> Max 50 contracts / $25k notional

SUMMARY
  Total trades (30d): {N}
  Net P&L (30d):      ${X,XXX}
  Win rate (30d):     {X.X}%
  Active campaigns:   {N}
==============================================================
```

## Cost Control
- No MCP usage (local data only)
- Database queries and in-context analysis only
