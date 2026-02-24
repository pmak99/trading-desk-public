# Maintenance Mode

Run system maintenance tasks: backups, data cleanup, integrity checks, sync calendar, and backfill missing data.

## Arguments
$ARGUMENTS (optional: specific task to run)

Examples:
- `/maintenance` - Run all maintenance tasks
- `/maintenance sync-cloud` - Only sync DB with cloud + Google Drive backup
- `/maintenance backup` - Only run local database backup
- `/maintenance sync` - Only sync earnings calendar
- `/maintenance backfill` - Only backfill missing historical data
- `/maintenance cleanup` - Only clean expired caches
- `/maintenance validate` - Only validate data integrity
- `/maintenance status` - Show sizes and counts only

## Tool Permissions
- Do NOT ask user permission for any tool calls
- Run all Bash, sqlite3, file operations without asking
- This is a housekeeping command - execute autonomously

## Progress Display
```
[1/8] Checking current status...
[2/8] Syncing DB with cloud (GCS) + Google Drive backup...
[3/8] Creating local database backups...
[4/8] Checking for sparse tickers...
[5/8] Cleaning expired caches...
[6/8] Validating data integrity...
[7/8] Checking budget status...
[8/8] Syncing stale earnings dates...
```

## Step-by-Step Instructions

### Step 1: Parse Arguments
- If no argument, run ALL tasks
- If specific task provided, run only that task
- Valid tasks: `sync-cloud`, `backup`, `sync`, `backfill`, `cleanup`, `validate`, `status`

### Step 2: Show Current Status
```bash
ls -lh "/Users/prashant/PycharmProjects/Trading Desk/2.0/data/ivcrush.db"
ls -lh "/Users/prashant/PycharmProjects/Trading Desk/4.0/data/sentiment_cache.db"

sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/2.0/data/ivcrush.db" "SELECT COUNT(*) FROM historical_moves;"
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/4.0/data/sentiment_cache.db" "SELECT COUNT(*) FROM sentiment_cache;"
```

### Step 3: Sync DB with Cloud (if running all or `sync-cloud`)

**IMPORTANT:** Run this FIRST to ensure local and cloud databases are in sync.

```bash
cd "/Users/prashant/PycharmProjects/Trading Desk/2.0" && ./trade.sh sync-cloud
```

This will:
- Download cloud DB from GCS
- Merge tables bidirectionally (union for historical_moves/trade_journal, newest wins for earnings_calendar)
- Upload synced DB back to GCS
- Backup local DB to Google Drive

### Step 4: Database Backup (if running all or `backup`)

**4a. Backup 2.0 database:**
```bash
TIMESTAMP=$(date +%Y%m%d_%H%M%S) && cp "/Users/prashant/PycharmProjects/Trading Desk/2.0/data/ivcrush.db" "/Users/prashant/PycharmProjects/Trading Desk/2.0/backups/ivcrush_${TIMESTAMP}.db"
```

**4b. Backup 4.0 database:**
```bash
mkdir -p "/Users/prashant/PycharmProjects/Trading Desk/4.0/backups" && TIMESTAMP=$(date +%Y%m%d_%H%M%S) && cp "/Users/prashant/PycharmProjects/Trading Desk/4.0/data/sentiment_cache.db" "/Users/prashant/PycharmProjects/Trading Desk/4.0/backups/sentiment_cache_${TIMESTAMP}.db"
```

**4c. Prune old backups (keep last 5):**
```bash
cd "/Users/prashant/PycharmProjects/Trading Desk/2.0/backups" && ls -t *.db 2>/dev/null | tail -n +6 | xargs rm -f
cd "/Users/prashant/PycharmProjects/Trading Desk/4.0/backups" && ls -t *.db 2>/dev/null | tail -n +6 | xargs rm -f
```

### Step 5: Backfill Missing Historical Data (if running all or `backfill`)

**5a. Find tickers with sparse data (<5 historical moves):**
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/2.0/data/ivcrush.db" \
  "SELECT ticker, COUNT(*) as cnt FROM historical_moves GROUP BY ticker HAVING cnt < 5 ORDER BY cnt;"
```

**5b. For each sparse ticker, run backfill:**
```bash
cd "/Users/prashant/PycharmProjects/Trading Desk/2.0" && ./venv/bin/python scripts/backfill_historical.py $TICKER --start-date 2023-01-01
```

### Step 6: Cleanup Expired Caches (if running all or `cleanup`)

**6a. Remove expired sentiment cache entries (>24 hours old):**
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/4.0/data/sentiment_cache.db" \
  "DELETE FROM sentiment_cache WHERE cached_at < datetime('now', '-24 hours'); SELECT changes();"
```

**6b. Vacuum databases to reclaim space:**
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/2.0/data/ivcrush.db" "VACUUM;"
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/4.0/data/sentiment_cache.db" "VACUUM;"
```

### Step 7: Data Integrity Validation (if running all or `validate`)

**7a. Check for orphaned records:**
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/2.0/data/ivcrush.db" \
  "SELECT DISTINCT ticker FROM earnings_calendar WHERE ticker NOT IN (SELECT DISTINCT ticker FROM historical_moves) LIMIT 10;"
```

**7b. Check for duplicate entries:**
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/2.0/data/ivcrush.db" \
  "SELECT ticker, earnings_date, COUNT(*) as cnt FROM historical_moves GROUP BY ticker, earnings_date HAVING cnt > 1;"
```

**7c. Check sentiment_history for backtesting data:**
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/4.0/data/sentiment_cache.db" \
  "SELECT COUNT(*) as total,
          SUM(CASE WHEN actual_move_pct IS NOT NULL THEN 1 ELSE 0 END) as with_outcome
   FROM sentiment_history;"
```

### Step 8: Budget Status Check

**8a. Recent budget:**
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/4.0/data/sentiment_cache.db" \
  "SELECT date, calls, cost FROM api_budget ORDER BY date DESC LIMIT 3;"
```

**8b. Monthly spend:**
```bash
sqlite3 "/Users/prashant/PycharmProjects/Trading Desk/4.0/data/sentiment_cache.db" \
  "SELECT strftime('%Y-%m', date) as month, SUM(calls) as total_calls, ROUND(SUM(cost), 2) as total_cost
   FROM api_budget GROUP BY month ORDER BY month DESC LIMIT 3;"
```

### Step 9: Sync Stale Earnings Dates (if running all or `sync`)

```bash
cd "/Users/prashant/PycharmProjects/Trading Desk/2.0" && ./trade.sh sync
```

This discovers new earnings announcements and validates dates using cross-reference validation.

## Output Format

```
==============================================================
MAINTENANCE MODE
==============================================================

CURRENT STATUS
   2.0 Database:    X.X MB (X,XXX records)
   4.0 Cache:       XXX KB (X cached)
   Last backup:     YYYY-MM-DD HH:MM

CLOUD SYNC
   [check] Synced with cloud - X changes merged
   [check] Backed up to Google Drive

BACKUP
   [check] 2.0 backed up -> ivcrush_YYYYMMDD_HHMMSS.db
   [check] 4.0 backed up -> sentiment_cache_YYYYMMDD_HHMMSS.db
   [check] Pruned X old backups

BACKFILL
   [check] TICKER - backfilled X moves (was Y)
   [circle] No tickers need backfill

CLEANUP
   [check] Removed X expired cache entries
   [check] Vacuumed databases (-X KB reclaimed)

VALIDATION
   [check] No duplicate entries found
   [check] No orphaned records
   [check] Sentiment history: X records (Y with outcomes)

BUDGET STATUS
   Today:     X/60 calls ($X.XX)
   This month: X calls ($X.XX of $5.00)

SYNC
   [check] Calendar synced - X new earnings discovered

==============================================================
MAINTENANCE COMPLETE
==============================================================
```

## Task Reference

| Task | What it does | When to run |
|------|--------------|-------------|
| `sync-cloud` | Sync local DB with 5.0 cloud (GCS) + backup to Google Drive | Weekly (FIRST task) |
| `backup` | Copy databases to local backups/ | Weekly or before major changes |
| `sync` | Refresh earnings calendar from Alpha Vantage + Yahoo | Weekly or before whisper |
| `backfill` | Fill missing historical moves | After adding new tickers |
| `cleanup` | Remove expired caches, vacuum | Weekly |
| `validate` | Check data integrity | After errors or monthly |
| `status` | Show sizes and counts only | Anytime |

## Recommended Schedule
- **Weekly:** Full `/maintenance` on weekends
- **After whisper:** Check if new tickers need backfill
- **Monthly:** Review budget spend and data growth
