# Multi-Leg Strategy Tracking Design

**Date:** 2026-01-08
**Status:** Approved

## Problem

Current `trade_journal` schema tracks individual legs without linking them. This causes:
- Inaccurate win rates (50% for a winning spread with 2 legs)
- No strategy-level performance analysis
- Cannot reconstruct original positions

## Goals

1. **Accurate win rate tracking** - Know if the overall strategy won, not individual legs
2. **Strategy performance analysis** - Compare spreads vs iron condors vs naked options
3. **Position reconstruction** - Recreate exactly what legs made up each trade

## Schema Changes

### New Table: `strategies`

```sql
CREATE TABLE strategies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    strategy_type TEXT NOT NULL,  -- SINGLE, SPREAD, IRON_CONDOR
    acquired_date DATE NOT NULL,
    sale_date DATE NOT NULL,
    days_held INTEGER,
    expiration DATE,
    quantity INTEGER,             -- contracts (normalized across legs)
    net_credit REAL,              -- positive = credit received
    net_debit REAL,               -- positive = debit paid
    gain_loss REAL NOT NULL,      -- combined P&L
    is_winner BOOLEAN NOT NULL,
    earnings_date DATE,
    actual_move REAL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

### Modified: `trade_journal`

Add foreign key column:

```sql
ALTER TABLE trade_journal ADD COLUMN strategy_id INTEGER REFERENCES strategies(id);
```

### How It Works

- Each leg stays in `trade_journal` with full detail (strike, option_type, individual P&L)
- Legs link to parent `strategies` row via `strategy_id`
- `strategies` table has combined P&L and is_winner for accurate win rate
- NULL `strategy_id` = legacy/unlinked trades (should not exist after backfill)

## Strategy Types

| Type | Legs | Description |
|------|------|-------------|
| SINGLE | 1 | Naked put or call |
| SPREAD | 2 | Bull put, bear call, etc. |
| IRON_CONDOR | 4 | 4-leg neutral strategy |

## Auto-Detection Logic

Applied during CSV import with hybrid approach (auto-detect + manual review).

### Grouping Criteria

Legs belong together if ALL match:
1. Same symbol
2. Same acquired_date
3. Same sale_date
4. Same expiration

### Classification

| Matched Legs | Classification |
|--------------|----------------|
| 1 | SINGLE |
| 2 | SPREAD |
| 4 | IRON_CONDOR |
| 3 or 5+ | Flag for manual review |

### Confidence Scoring

- **HIGH:** All 4 criteria match, standard leg count → auto-accept
- **MEDIUM:** Dates match but different expirations → flag for review
- **LOW:** Only symbol matches → don't auto-group

### Import Output Example

```
Detected 3 strategies from 7 legs:
  ✓ APLD SPREAD (2 legs) - HIGH confidence
  ✓ NVDA IRON_CONDOR (4 legs) - HIGH confidence
  ? AAPL (1 leg) - unmatched, treating as SINGLE
```

## Manual Override Mechanism

### Commands

```bash
./trade.sh journal review          # Show pending groupings
./trade.sh journal link 565 566    # Manually link legs into strategy
./trade.sh journal unlink 42       # Break apart a strategy
./trade.sh journal retype 42 IRON_CONDOR  # Change strategy type
```

### Interactive Review

```
Pending review (2 items):

[1] Strategy #42 - SPREAD (auto-detected)
    Leg 565: AAPL $180 PUT  +$1,200
    Leg 566: AAPL $175 PUT  -$400
    Combined: +$800 WINNER
    Action: [a]ccept / [e]dit / [s]plit

[2] Unmatched leg:
    Leg 567: MSFT $400 CALL +$300
    Action: [s]ingle / [l]ink to existing
```

## Querying & Stats

### Strategy-Level Win Rate

```sql
SELECT
    strategy_type,
    COUNT(*) as trades,
    ROUND(100.0 * SUM(is_winner) / COUNT(*), 1) as win_rate,
    ROUND(SUM(gain_loss), 2) as total_pnl
FROM strategies
GROUP BY strategy_type;
```

### Position Reconstruction

```sql
SELECT s.strategy_type, s.gain_loss as combined_pnl,
       t.option_type, t.strike, t.quantity, t.gain_loss as leg_pnl
FROM strategies s
JOIN trade_journal t ON t.strategy_id = s.id
WHERE s.id = 42;
```

### Monthly P&L by Strategy Type

```sql
SELECT strftime('%Y-%m', sale_date) as month,
       strategy_type,
       SUM(gain_loss) as pnl
FROM strategies
GROUP BY month, strategy_type;
```

## Migration Plan

Migration is mandatory and includes backfill of all existing data.

### Steps

1. Create `strategies` table
2. Add `strategy_id` column to `trade_journal`
3. Run backfill to group existing trades into strategies
4. Update CSV parser to use new detection logic
5. Verify no orphan legs remain (all trades linked to strategies)

### Backfill Command

```bash
./trade.sh journal backfill --dry-run  # Preview groupings
./trade.sh journal backfill             # Apply groupings (mandatory)
```

### Current APLD Trade

The existing APLD trade (currently stored as combined spread) will be:
1. Split back into individual legs in `trade_journal`
2. Linked to a new SPREAD strategy in `strategies` table
