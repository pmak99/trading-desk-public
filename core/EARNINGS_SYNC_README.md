# Earnings Calendar Sync - Quick Reference

## The Problem You Had

Oracle's December 10 earnings date was missing from your database because:
1. Your system only discovers earnings when you manually run `./trade.sh scan` or `./trade.sh whisper`
2. No one ran whisper for the week of Dec 8, so Oracle's date (announced Dec 2) was never discovered
3. The validation script validates existing dates but doesn't discover new ones

**Root cause**: Reactive system (manual triggers) with no proactive discovery mechanism.

---

## The Solution

New `./trade.sh sync` command that proactively discovers and validates earnings dates.

### What it does:
1. ✅ Fetches full 3-month earnings calendar from Alpha Vantage
2. ✅ Discovers NEW earnings announcements (like Oracle Dec 10)
3. ✅ Cross-validates with Yahoo Finance (highest priority source)
4. ✅ Detects date changes and conflicts
5. ✅ Updates database automatically

### No Cron, Manual Control Only:
- ✅ You trigger sync when you want fresh data
- ✅ No background jobs running
- ✅ Full control over when data refreshes
- ✅ Recommended: Run weekly (every Monday)

---

## Usage

### Dry Run (Preview Changes)
```bash
./trade.sh sync --dry-run
```

Shows what would be updated without making changes.

### Live Sync (Update Database)
```bash
./trade.sh sync
```

Discovers and updates earnings dates.

### Check for Stale Data
```bash
./trade.sh sync --check-staleness
```

Returns exit code 1 if any data is >7 days old.

---

## Recommended Workflow

### Weekly Sync (Every Monday)
```bash
# 1. Sync earnings calendar
./trade.sh sync

# 2. Check what's coming up
./trade.sh whisper
```

### Before Trading
```bash
# Check if data is fresh
./trade.sh sync --check-staleness

# If stale, refresh it
if [ $? -ne 0 ]; then
    ./trade.sh sync
fi

# Then scan/whisper
./trade.sh whisper 2025-12-08
```

---

## What This Fixes

| Before | After |
|--------|-------|
| ❌ Oracle Dec 10 missed for 3 days | ✅ Discovered when you run sync |
| ❌ Only fetches when running scan/whisper | ✅ Sync fetches full calendar |
| ❌ Relies on you remembering | ✅ Manual control, run when needed |
| ❌ No alerts for stale data | ✅ `--check-staleness` monitoring |
| ❌ Silent failures | ✅ Comprehensive output |

---

## Example Output

```
═══════════════════════════════════════
  Earnings Calendar Sync
═══════════════════════════════════════

Loading current database state...
Found 47 existing earnings dates in database

Fetching earnings calendar from Alpha Vantage (horizon=3month)...
✓ Fetched 6424 earnings events from Alpha Vantage

Processing 6424 unique tickers...

══════════════════════════════════════════════════════════════════
NEW EARNINGS: ORCL
  Alpha Vantage: 2025-12-10 (AMC)
══════════════════════════════════════════════════════════════════
ORCL: Yahoo Finance earnings date = 2025-12-10 (AMC)
ORCL: Consensus date = 2025-12-10 (AMC)
  ✓ Consensus: 2025-12-10 (AMC)
  💾 Saved to database

... (processing other tickers) ...

════════════════════════════════════════════════════════════════
SYNC SUMMARY
════════════════════════════════════════════════════════════════
Tickers processed: 6424
  ✓ New earnings dates: 15
  ↻ Updated dates: 3
  = Unchanged: 6406
  ⚠️  Conflicts detected: 1
  ✗ Errors: 0

CHANGES DETECTED:
  ORCL: None → 2025-12-10 (AMC) Date changed
  AVGO: 2025-12-12 → 2025-12-11 (AMC) Date changed

✓ Earnings calendar synced
```

---

## Documentation

- **Full guide**: `EARNINGS_SYNC_GUIDE.md`
- **Root cause analysis**: `EARNINGS_VALIDATION_FAILURE_ANALYSIS.md`
- **Help**: `./trade.sh help` (see "sync" section)

---

## Quick Commands

```bash
# Sync earnings (recommended weekly)
./trade.sh sync

# Preview before updating
./trade.sh sync --dry-run

# Check staleness
./trade.sh sync --check-staleness

# View help
./trade.sh help
```

---

## Remember

**Run `./trade.sh sync` weekly to ensure fresh earnings data!**

Add to your Monday routine:
1. Run sync
2. Check whisper list
3. Plan trades for the week
