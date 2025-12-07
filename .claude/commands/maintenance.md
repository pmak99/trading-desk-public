# Maintenance Mode

Run system maintenance tasks: backups, data cleanup, integrity checks, and backfill missing data.

## Arguments
$ARGUMENTS (optional: specific task to run)

Examples:
- `/maintenance` - Run all maintenance tasks
- `/maintenance backup` - Only run database backup
- `/maintenance backfill` - Only backfill missing historical data
- `/maintenance cleanup` - Only clean expired caches

## Purpose
Run `/maintenance` weekly (or after heavy usage):
- Backup databases before they grow too large
- Backfill missing historical moves for new tickers
- Clean expired sentiment cache entries
- Validate data integrity
- Report storage usage

## Step-by-Step Instructions

### Step 1: Parse Arguments
- If no argument, run ALL tasks
- If specific task provided, run only that task
- Valid tasks: `backup`, `backfill`, `cleanup`, `validate`, `status`

### Step 2: Show Current Status
```bash
# Database sizes
ls -lh $PROJECT_ROOT/2.0/data/ivcrush.db
ls -lh $PROJECT_ROOT/4.0/data/sentiment_cache.db

# Record counts
sqlite3 $PROJECT_ROOT/2.0/data/ivcrush.db "SELECT COUNT(*) FROM historical_moves;"
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db "SELECT COUNT(*) FROM sentiment_cache;"
```

### Step 3: Database Backup (if running all or `backup`)

**3a. Backup 2.0 database:**
```bash
BACKUP_DIR="$PROJECT_ROOT/2.0/backups"
DB_FILE="$PROJECT_ROOT/2.0/data/ivcrush.db"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
cp "$DB_FILE" "$BACKUP_DIR/ivcrush_${TIMESTAMP}.db"
```

**3b. Backup 4.0 database:**
```bash
BACKUP_DIR="$PROJECT_ROOT/4.0/backups"
mkdir -p "$BACKUP_DIR"
DB_FILE="$PROJECT_ROOT/4.0/data/sentiment_cache.db"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
cp "$DB_FILE" "$BACKUP_DIR/sentiment_cache_${TIMESTAMP}.db"
```

**3c. Prune old backups (keep last 5):**
```bash
cd "$BACKUP_DIR" && ls -t *.db 2>/dev/null | tail -n +6 | xargs rm -f
```

### Step 4: Backfill Missing Historical Data (if running all or `backfill`)

**4a. Find tickers with sparse data (<5 historical moves):**
```bash
sqlite3 $PROJECT_ROOT/2.0/data/ivcrush.db \
  "SELECT ticker, COUNT(*) as cnt FROM historical_moves GROUP BY ticker HAVING cnt < 5 ORDER BY cnt;"
```

**4b. For each sparse ticker, run backfill:**
```bash
cd $PROJECT_ROOT/2.0 && \
  ./venv/bin/python scripts/backfill_yfinance.py $TICKER --start-date 2023-01-01
```

Display progress:
```
  ✓ SAIL - backfilled 10 moves (was 2)
  ✓ BLSH - backfilled 8 moves (was 1)
  ○ No other tickers need backfill
```

### Step 5: Cleanup Expired Caches (if running all or `cleanup`)

**5a. Remove expired sentiment cache entries (>24 hours old):**
```bash
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "DELETE FROM sentiment_cache WHERE cached_at < datetime('now', '-24 hours');"
```

**5b. Report deleted entries:**
```bash
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "SELECT changes();"
```

**5c. Vacuum databases to reclaim space:**
```bash
sqlite3 $PROJECT_ROOT/2.0/data/ivcrush.db "VACUUM;"
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db "VACUUM;"
```

### Step 6: Data Integrity Validation (if running all or `validate`)

**6a. Check for orphaned records:**
```bash
# Tickers in earnings but not in historical_moves
sqlite3 $PROJECT_ROOT/2.0/data/ivcrush.db \
  "SELECT DISTINCT ticker FROM earnings_calendar WHERE ticker NOT IN (SELECT DISTINCT ticker FROM historical_moves) LIMIT 10;"
```

**6b. Check for duplicate entries:**
```bash
sqlite3 $PROJECT_ROOT/2.0/data/ivcrush.db \
  "SELECT ticker, earnings_date, COUNT(*) as cnt FROM historical_moves GROUP BY ticker, earnings_date HAVING cnt > 1;"
```

**6c. Check sentiment_history for backtesting data:**
```bash
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "SELECT COUNT(*) as total,
          SUM(CASE WHEN actual_move_pct IS NOT NULL THEN 1 ELSE 0 END) as with_outcome
   FROM sentiment_history;"
```

### Step 7: Budget Reset Check

**7a. Check if budget needs reset (new day):**
```bash
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "SELECT date, calls, cost FROM api_budget ORDER BY date DESC LIMIT 3;"
```

**7b. Show monthly spend:**
```bash
sqlite3 $PROJECT_ROOT/4.0/data/sentiment_cache.db \
  "SELECT strftime('%Y-%m', date) as month, SUM(calls) as total_calls, SUM(cost) as total_cost
   FROM api_budget GROUP BY month ORDER BY month DESC LIMIT 3;"
```

## Output Format

```
══════════════════════════════════════════════════════
🔧 MAINTENANCE MODE
══════════════════════════════════════════════════════

📊 CURRENT STATUS
   ┌────────────────────────────────────────────┐
   │ 2.0 Database:    2.1 MB (5,070 records)   │
   │ 4.0 Cache:       156 KB (7 cached)        │
   │ Last backup:     2024-12-01 09:23         │
   └────────────────────────────────────────────┘

💾 BACKUP
   ✓ 2.0 database backed up → ivcrush_20251207_143022.db
   ✓ 4.0 database backed up → sentiment_cache_20251207_143022.db
   ✓ Pruned 2 old backups

📈 BACKFILL
   ✓ SAIL - backfilled 10 moves (was 2)
   ✓ BLSH - backfilled 8 moves (was 1)
   ○ 2 tickers updated

🧹 CLEANUP
   ✓ Removed 15 expired cache entries
   ✓ Vacuumed databases (-45 KB reclaimed)

✅ VALIDATION
   ✓ No duplicate entries found
   ✓ No orphaned records
   ✓ Sentiment history: 23 records (18 with outcomes)

💰 BUDGET STATUS
   ┌────────────────────────────────────────────┐
   │ Today:     4/150 calls ($0.02)            │
   │ This week: 28 calls ($0.14)               │
   │ This month: 89 calls ($0.45 of $5.00)     │
   └────────────────────────────────────────────┘

══════════════════════════════════════════════════════
✅ MAINTENANCE COMPLETE
══════════════════════════════════════════════════════
```

## Task Reference

| Task | What it does | When to run |
|------|--------------|-------------|
| `backup` | Copy databases to backups/ | Weekly or before major changes |
| `backfill` | Fill missing historical moves | After adding new tickers |
| `cleanup` | Remove expired caches, vacuum | Weekly |
| `validate` | Check data integrity | After errors or monthly |
| `status` | Show sizes and counts only | Anytime |

## Recommended Schedule
- **Weekly:** Full `/maintenance` on weekends
- **After whisper:** Check if new tickers need backfill
- **Monthly:** Review budget spend and data growth
