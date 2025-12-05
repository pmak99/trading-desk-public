# Earnings Calendar Sync - Setup Guide

## TL;DR

**Problem**: Oracle's December 10 earnings date was missing because the system is **reactive** (only fetches when you run scan/whisper), with no proactive discovery.

**Solution**: New `./trade.sh sync` command to manually discover and validate upcoming earnings dates whenever you want fresh data.

---

## Quick Start

### 1. Preview what would be updated (dry-run)

```bash
./trade.sh sync --dry-run
```

This will:
- ✓ Fetch full Alpha Vantage earnings calendar (3-month horizon)
- ✓ Compare with your database
- ✓ Cross-validate any new/changed dates with Yahoo Finance
- ✓ Show what would be updated (but doesn't actually update)

### 2. Run live sync (updates database)

```bash
./trade.sh sync
```

### 3. Check for stale data

```bash
./trade.sh sync --check-staleness
```

Returns exit code 1 if any upcoming earnings data is >7 days old.

**Recommended**: Run `./trade.sh sync` weekly or before important trading sessions to ensure fresh data.

---

## What Was Wrong

### The Problem

Your validation system was **reactive, not proactive**:

```
❌ Current workflow:
   User manually runs ./trade.sh whisper
        ↓
   System fetches earnings calendar
        ↓
   System validates dates
        ↓
   System updates database

Problem: No one ran whisper for Dec 8 week → Oracle's Dec 10 date never discovered
```

### The Root Cause

1. **No Automated Discovery**
   - Oracle announced Dec 10 earnings on Dec 2, 2025
   - System never noticed (no one ran scan/whisper for that week)
   - Database remained stale (last update: Nov 21)

2. **Validation Script Limitation**
   - `validate_earnings_dates.py` validates EXISTING dates
   - Does NOT discover NEW earnings announcements
   - Only validates tickers you explicitly provide or whisper list

3. **Manual Process Fails**
   - Relies on you remembering to run validation
   - Easy to miss newly announced earnings
   - No alerts for stale data

---

## What's Fixed

### New Manual Workflow

```
✅ New workflow:
   ./trade.sh sync (manual trigger)
        ↓
   Fetch full Alpha Vantage calendar (3-month horizon)
        ↓
   For each earnings date:
     • Check if NEW or CHANGED
     • Cross-validate with Yahoo Finance
     • Detect conflicts → use consensus
     • Update database
        ↓
   Log summary: X new, Y updated, Z conflicts
```

### Features

#### 1. **Proactive Discovery** ⭐
- Automatically discovers new earnings announcements
- Runs daily without manual intervention
- Fetches 3-month calendar from Alpha Vantage

#### 2. **Change Detection**
- Detects when earnings dates change (company reschedules)
- Logs all changes: `ORCL: 2025-12-12 → 2025-12-10`
- Alerts on conflicts between sources

#### 3. **Cross-Validation**
- Every new/changed date validated against Yahoo Finance
- Uses consensus (Yahoo Finance priority, like existing system)
- Logs conflicts for manual review

#### 4. **Staleness Monitoring**
- Re-validates data that's >7 days old
- Can check staleness on-demand
- Exit code 1 if stale data found (useful for pre-trading checks)

#### 5. **Comprehensive Logging**
- Summary stats: new dates, updates, conflicts, errors
- Detailed change log
- Daily log files: `logs/earnings_sync_YYYYMMDD.log`

---

## Usage

### Manual Sync via trade.sh (Recommended)

```bash
# Dry run (see what would change)
./trade.sh sync --dry-run

# Live sync (updates database)
./trade.sh sync

# Check for stale data (returns exit code 1 if stale)
./trade.sh sync --check-staleness
```

### Advanced: Direct Python Script

```bash
# Custom horizon (6 months instead of 3)
./venv/bin/python3 scripts/sync_earnings_calendar.py --horizon 6month

# Debug mode
./venv/bin/python3 scripts/sync_earnings_calendar.py --log-level DEBUG

# Custom staleness threshold (14 days)
./venv/bin/python3 scripts/sync_earnings_calendar.py --check-staleness --threshold 14
```

### Check for Stale Data

```bash
# Check if any upcoming earnings data is >7 days stale
./venv/bin/python3 scripts/sync_earnings_calendar.py --check-staleness

# Custom threshold (14 days)
./venv/bin/python3 scripts/sync_earnings_calendar.py --check-staleness --threshold 14

# Exit code 1 if stale data found, 0 if all fresh
# Useful for pre-trading checklist!
```

---

## Example Output

### Dry Run

```
════════════════════════════════════════════════════════════════════════════════
EARNINGS CALENDAR SYNC
════════════════════════════════════════════════════════════════════════════════
Mode: DRY RUN
Horizon: 3month
Database: data/ivcrush.db
Time: 2025-12-05 20:00:00
════════════════════════════════════════════════════════════════════════════════

Loading current database state...
Found 1247 existing earnings dates in database

Fetching earnings calendar from Alpha Vantage (horizon=3month)...
✓ Fetched 2143 earnings events from Alpha Vantage

Processing 876 unique tickers...

══════════════════════════════════════════════════════════════════
NEW EARNINGS: ORCL
  Alpha Vantage: 2025-12-10 (AMC)
══════════════════════════════════════════════════════════════════
2025-12-05 20:01:15 - [abc-123] - src.application.services.earnings_date_validator - INFO - Cross-referencing earnings date for ORCL...
ORCL: Yahoo Finance earnings date = 2025-12-10 (AMC)
ORCL: Alpha Vantage earnings date = 2025-12-10 (AMC)
ORCL: Consensus date = 2025-12-10 (AMC)
  ✓ Consensus: 2025-12-10 (AMC)
  🔍 DRY RUN - Would save to database

══════════════════════════════════════════════════════════════════
CHANGE DETECTED: AVGO
  Database: 2025-12-12 (AMC) [updated 5d ago]
  Alpha Vantage: 2025-12-11 (AMC)
══════════════════════════════════════════════════════════════════
2025-12-05 20:01:17 - [abc-123] - src.application.services.earnings_date_validator - INFO - Cross-referencing earnings date for AVGO...
AVGO: Yahoo Finance earnings date = 2025-12-11 (AMC)
AVGO: Alpha Vantage earnings date = 2025-12-11 (AMC)
AVGO: Consensus date = 2025-12-11 (AMC)
  ✓ Consensus: 2025-12-11 (AMC)
  🔍 DRY RUN - Would update database

...

════════════════════════════════════════════════════════════════════════════════
SYNC SUMMARY
════════════════════════════════════════════════════════════════════════════════
Tickers processed: 876
  ✓ New earnings dates: 15
  ↻ Updated dates: 3
  = Unchanged: 858
  ⚠️  Conflicts detected: 1
  ✗ Errors: 2

CHANGES DETECTED:
  ORCL: None → 2025-12-10 (AMC) Date changed
  AVGO: 2025-12-12 → 2025-12-11 (AMC) Date changed
  SNOW: 2025-12-05 → 2025-12-05 (BMO) Timing changed

🔍 DRY RUN - No changes made to database
```

### Check Staleness

```bash
$ ./venv/bin/python3 scripts/sync_earnings_calendar.py --check-staleness

Checking for stale earnings data...

⚠️  WARNING: 3 tickers with data >7 days stale:
  - ORCL: 2025-12-10 (AMC) - 14.5 days stale (last updated: 2025-11-21 02:38:56)
  - TSLA: 2025-12-18 (AMC) - 9.2 days stale (last updated: 2025-11-26 14:23:11)
  - AMD: 2025-12-15 (AMC) - 8.1 days stale (last updated: 2025-11-27 09:45:32)

# Exit code: 1 (can be used in scripts)
```

---

## Integration with Existing Workflow

### Pre-Trading Routine

Add sync check to your pre-trading routine:

```bash
# Before running scan/whisper, refresh earnings calendar
./trade.sh sync --check-staleness

# If stale, sync it
if [ $? -ne 0 ]; then
    echo "Refreshing earnings calendar..."
    ./trade.sh sync
fi

# Then run your scan/whisper
./trade.sh whisper 2025-12-08
```

### Weekly Sync Recommendation

Run sync weekly to discover new earnings announcements:

```bash
# Every Monday morning
./trade.sh sync

# Then check what's upcoming
./trade.sh whisper
```

### Manual Validation Still Works

The existing validation script still works and is useful for:
- Validating specific tickers: `python scripts/validate_earnings_dates.py ORCL AVGO`
- Validating whisper list: `python scripts/validate_earnings_dates.py --whisper-week`
- Parallel validation: `python scripts/validate_earnings_dates.py --whisper-week --parallel`

**Difference**:
- **Validation script**: Validates tickers you provide (reactive)
- **Sync script**: Discovers ALL upcoming earnings (proactive)

Use both:
- **Daily**: Automated sync (discovers new dates)
- **Ad-hoc**: Manual validation (double-check specific tickers)

---

## Monitoring

### Check Data Freshness

```bash
# Check if any data is stale (>7 days old)
./trade.sh sync --check-staleness

# Custom threshold (14 days)
./venv/bin/python3 scripts/sync_earnings_calendar.py --check-staleness --threshold 14
```

### Verify Database Status

```bash
# Check database update times
sqlite3 data/ivcrush.db << EOF
SELECT
    COUNT(*) as total,
    MAX(updated_at) as last_update,
    julianday('now') - julianday(MAX(updated_at)) as days_since_update
FROM earnings_calendar
WHERE earnings_date >= date('now');
EOF
```

### Review Sync Results

After running `./trade.sh sync`, you'll see:
- ✓ New earnings discovered
- ↻ Dates that changed
- ⚠️ Conflicts detected
- ✗ Errors encountered

---

## Troubleshooting

### Sync Script Errors

```bash
# Test manually with debug output
cd "/Users/prashant/PycharmProjects/Trading Desk/2.0"
./venv/bin/python3 scripts/sync_earnings_calendar.py --dry-run --log-level DEBUG

# Check environment variables
./venv/bin/python3 -c "import os; print(os.getenv('ALPHA_VANTAGE_KEY'))"

# Verify database access
sqlite3 data/ivcrush.db "SELECT COUNT(*) FROM earnings_calendar;"
```

### Rate Limiting Errors

The sync script uses the rate limiter in **blocking mode**, so it will automatically wait:

```
Rate limit: waiting 12.00s for token
```

This is normal - the script will pace itself to stay within Alpha Vantage limits (5/min, 500/day).

### Conflicts Detected

Conflicts are logged but don't stop the sync:

```
⚠️  CONFLICT: Dates differ by 2 days: Yahoo Finance: 2025-12-10 (AMC) | Alpha Vantage: 2025-12-12 (AMC)
✓ Consensus: 2025-12-10 (AMC)  [Yahoo Finance priority]
```

The system uses **Yahoo Finance as the highest priority** (most reliable).

### Database Locked Error

If you see "database is locked":
- Another script is accessing the database
- Wait a few seconds and retry
- The sync script has retry logic built in

---

## Success Metrics

With regular sync usage (weekly recommended), you should achieve:

- ✅ **100% coverage**: All upcoming earnings detected when you run sync
- ✅ **Fresh data**: Run sync weekly = all data <7 days old
- ✅ **Manual control**: You decide when to refresh, no surprises
- ✅ **Conflict awareness**: Immediate notification of any date discrepancies

---

## Next Steps

### Phase 1: Get Started (Today)
1. ✅ Test sync in dry-run mode: `./trade.sh sync --dry-run`
2. ✅ Run live sync once: `./trade.sh sync`
3. ✅ Check for stale data: `./trade.sh sync --check-staleness`

### Phase 2: Build Habits (This Week)
4. Run sync every Monday morning before trading
5. Add staleness check to your pre-trading routine
6. Use `./trade.sh whisper` after sync for fresh data

### Phase 3: Optional Enhancements
7. Create alias: `alias syncearnings='cd ~/path && ./trade.sh sync'`
8. Set calendar reminder: "Monday 9 AM - Sync earnings calendar"
9. Track sync history in notes/trading journal

---

## Questions?

Check the full analysis: `EARNINGS_VALIDATION_FAILURE_ANALYSIS.md`

Test the sync:
```bash
./trade.sh sync --dry-run
```

Run sync:
```bash
./trade.sh sync
```

Check for stale data:
```bash
./trade.sh sync --check-staleness
```
