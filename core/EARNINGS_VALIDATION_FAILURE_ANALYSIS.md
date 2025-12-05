# Earnings Validation Failure - Root Cause Analysis

**Date**: December 5, 2025
**Incident**: Oracle (ORCL) December 10, 2025 earnings date was missing from database

---

## What Happened

Oracle announced their Q2 FY2026 earnings date (December 10, 2025) on **December 2, 2025**.

When running validation on **December 5**, the database still showed Oracle's last earnings as **September 9, 2025** (updated November 21).

The December 10 date was never discovered or validated by the system.

---

## Root Cause Analysis

### System Architecture (Current)

```
User runs scan/whisper → Fetch Alpha Vantage calendar → Match tickers → Validate with Yahoo Finance → Save to DB
     ↑                                                                                                    ↓
     └────────────────────────────── MANUAL / ON-DEMAND ──────────────────────────────────────────────────┘
```

**The system is entirely REACTIVE**:
1. ✅ Earnings dates are only fetched when you run `./trade.sh whisper` or `./trade.sh scan <date>`
2. ✅ The validation script (`validate_earnings_dates.py`) **validates existing dates**, but doesn't **discover new ones**
3. ❌ **No proactive discovery** of newly announced earnings dates
4. ❌ **No automated daily/weekly refresh** of upcoming earnings calendar

### Specific Failure Points

#### 1. **No Proactive Discovery**
- Oracle announced Dec 10 earnings on Dec 2
- System never noticed because no one ran whisper/scan for the week of Dec 8-14
- Database remained stale (last update: Nov 21)

#### 2. **Validation Script Limitation** (`scripts/validate_earnings_dates.py`)
```python
# Current behavior:
def validate_earnings_date(ticker: str):
    # Validates by cross-referencing Yahoo Finance vs Alpha Vantage
    # BUT: Only works if ticker is already in validation list
    # Does NOT discover new upcoming earnings
```

The `--whisper-week` flag:
- Fetches tickers from Earnings Whispers (Reddit)
- Validates those tickers
- **Problem**: If ticker isn't in the "most anticipated" list, it won't be validated

#### 3. **Data Staleness**
```sql
-- Current staleness of upcoming earnings:
AVGO  | 2025-12-05 | 4.7 days stale
CHWY  | 2025-12-05 | 4.7 days stale
COST  | 2025-12-05 | 4.7 days stale
LULU  | 2025-12-05 | 4.7 days stale
MNY   | 2025-12-10 | 1.6 days stale
ORCL  | 2025-12-10 | MISSING (14.5 days stale)
```

---

## What Should Have Happened

### Expected Workflow

```
Daily automated job:
  ↓
Fetch full Alpha Vantage calendar (3-month horizon)
  ↓
Cross-validate NEW dates with Yahoo Finance
  ↓
Detect conflicts/changes
  ↓
Alert user + Auto-update database
```

### Cross-Reference Validation Should Have Caught This

On **December 2** (when Oracle announced):
1. ✅ Alpha Vantage would have Dec 10 in their calendar
2. ✅ Yahoo Finance would have Dec 10
3. ✅ Cross-validation would confirm Dec 10 (AMC)
4. ❌ **BUT**: No automated process to run this check

---

## Gaps in Current System

### Gap 1: No Automated Discovery
**Missing**: Daily/weekly job to refresh earnings calendar
**Impact**: New earnings announcements go undetected
**Severity**: 🔴 Critical

### Gap 2: Validation is Manual
**Missing**: Automated cross-validation on calendar refresh
**Impact**: User must remember to run validation
**Severity**: 🟡 High

### Gap 3: No Staleness Monitoring
**Missing**: Alert when earnings data is >N days stale
**Impact**: Silent data degradation
**Severity**: 🟡 High

### Gap 4: No Change Detection
**Missing**: Alert when earnings date changes (company reschedules)
**Impact**: Trading on wrong date
**Severity**: 🔴 Critical

---

## Recommended Solutions

### Solution 1: Daily Earnings Discovery Job ⭐ PRIORITY
**Script**: `scripts/sync_earnings_calendar.py` (NEW)

```python
# Automated daily job (run via cron)
1. Fetch full Alpha Vantage calendar (3-month horizon)
2. For each ticker in calendar:
   a. Check if date exists in database
   b. If NEW or CHANGED → cross-validate with Yahoo Finance
   c. If conflict → log warning + use consensus
   d. Update database
3. Report: X new earnings, Y changed, Z conflicts
```

**Schedule**: Daily at 8 PM ET (after market close, before pre-market prep)

### Solution 2: Enhanced Validation Script
**Update**: `scripts/validate_earnings_dates.py`

Add `--discover` mode:
```bash
# Current: Validate specific tickers
python scripts/validate_earnings_dates.py ORCL AVGO

# New: Discover + validate ALL upcoming earnings
python scripts/validate_earnings_dates.py --discover --horizon 30
```

### Solution 3: Staleness Monitoring
**Update**: `scripts/validate_earnings_dates.py`

Add `--check-staleness` mode:
```bash
# Check for stale data
python scripts/validate_earnings_dates.py --check-staleness --threshold 7

# Output:
# ⚠️  WARNING: 5 tickers with data >7 days stale:
#    - ORCL: 14.5 days (last: 2025-11-21)
#    - ...
```

### Solution 4: Change Detection & Alerts
**Update**: Database schema

Add `previous_date` column to track changes:
```sql
ALTER TABLE earnings_calendar ADD COLUMN previous_date TEXT;
ALTER TABLE earnings_calendar ADD COLUMN date_changed_at TEXT;
```

When updating:
- If date changed → log to `previous_date` + set `date_changed_at`
- Alert user: "⚠️  ORCL earnings moved: Dec 12 → Dec 10"

---

## Implementation Priority

### Phase 1: Critical Fixes (Today)
1. ✅ Fix rate limiter in validation script
2. ⭐ **Create `scripts/sync_earnings_calendar.py`** - Automated discovery
3. ⭐ Set up daily cron job (8 PM ET)

### Phase 2: Enhanced Monitoring (This Week)
4. Add `--discover` mode to validation script
5. Add `--check-staleness` monitoring
6. Add change detection to database

### Phase 3: Alerting (Next Week)
7. Email/Slack alerts for conflicts
8. Daily report: "Earnings Calendar Update: 5 new, 2 changed, 0 conflicts"

---

## Immediate Action Items

- [x] Fix rate limiter (completed)
- [ ] Create `sync_earnings_calendar.py` script
- [ ] Test sync script with dry-run
- [ ] Set up cron job: `0 20 * * * /path/to/sync_earnings_calendar.py`
- [ ] Add staleness check to pre-trading checklist

---

## Lessons Learned

1. **Reactive systems have blind spots** - Need proactive monitoring
2. **Validation ≠ Discovery** - Cross-reference only works on known data
3. **Manual processes fail** - Automation is critical for data integrity
4. **Staleness is silent** - Need active monitoring & alerts

---

## Success Metrics

After implementing fixes, we should achieve:
- 🎯 **100% coverage**: All upcoming earnings detected within 24 hours of announcement
- 🎯 **<1% stale data**: >99% of upcoming earnings updated within 24 hours
- 🎯 **0 manual interventions**: Fully automated discovery & validation
- 🎯 **Conflict alerts**: User notified of any date discrepancies within 24 hours
