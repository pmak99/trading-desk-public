# CRITICAL BUG: IV Expansion Scoring Failure

**Date**: 2025-11-10
**Severity**: CRITICAL
**Component**: IV Expansion Velocity Scorer (35% of total score)

---

## Executive Summary

The IV Expansion Velocity scorer (35% weight - PRIMARY metric for 1-2 day pre-earnings entries) is **consistently returning neutral scores (50.0)** instead of calculating actual IV changes. This causes:

1. **Inflated scores** for tickers with falling IV (premium leaking - BAD setups)
2. **Deflated scores** for tickers with rising IV (premium building - GOOD setups)
3. **Unreliable trade selection** - contradicts core IV crush strategy

---

## Evidence from Production Reports

### Report 1: Nov 10, 2025 @ 2:14 PM

| Ticker | Reported Score | Weekly IV Change | Correct Score | Actual Score | Impact |
|--------|----------------|------------------|---------------|--------------|--------|
| **CRWV** | 96.6/100 | **+20.7%** (moderate expansion) | 60.0 | 50.0 (neutral) | **+3.5 pts** (inflated) |
| **EOSE** | 94.3/100 | **+0.8%** (weak expansion) | 40.0 | 50.0 (neutral) | **-3.5 pts** (deflated) |
| **AMC** | 90.6/100 | **-16.2%** ⚠️ (LEAKING!) | 20.0 | 50.0 (neutral) | **-10.5 pts** (INFLATED) |

### Report 2: Nov 11 earnings (created Nov 10 @ 1:27 PM)

| Ticker | Reported Score | Weekly IV Change | Correct Score | Actual Score | Impact |
|--------|----------------|------------------|---------------|--------------|--------|
| **AMC** | 71.9/100 | **-16.2%** ⚠️ (LEAKING!) | 20.0 | 50.0 (neutral) | **-10.5 pts** (INFLATED) |
| **EOSE** | 77.0/100 | **+0.8%** (weak expansion) | 40.0 | 50.0 (neutral) | **-3.5 pts** (deflated) |

---

## The Most Egregious Case: AMC

**AMC appeared in BOTH reports with FALLING IV:**

```
Nov 4:  101.56% IV
Nov 10:  85.10% IV
Change: -16.2% (PREMIUM LEAKING - DO NOT ENTER!)

Reported Score: 90.6/100 (Nov 10), 71.9/100 (Nov 11)
Actual Score:   80.1/100 (Nov 10), 61.4/100 (Nov 11)

System Signal: "Top candidate - excellent setup"
Reality: Premium leaking - BAD setup for IV crush strategy
```

**This directly contradicts the IV crush strategy** which requires IV expansion (premium building) for optimal entries.

---

## Root Cause

### 1. Sporadic IV Recording

IV is recorded ONLY when the analyzer runs:
```python
# tradier_client.py:245
self.iv_tracker.record_iv(ticker, current_iv)
```

**Actual recording pattern** (from database):
```
Oct 28: ✓ (analyzer run)
Oct 29: ✓ (analyzer run)
Nov 1-3: ✗ GAP (no analyzer runs)  ← PROBLEM
Nov 4: ✓ (analyzer run)
Nov 5-6: ✗ GAP
Nov 7-10: ✓ (daily runs)
```

### 2. Narrow 2-Day Window

Weekly IV Change function looks for data in **EXACTLY 7-9 days ago**:
```python
# iv_history_tracker.py:246
lookback_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')

cursor = conn.execute(
    """SELECT iv_value, date FROM iv_history
       WHERE ticker = ? AND date <= ? AND date >= ?
       ORDER BY date DESC LIMIT 1""",
    (ticker, lookback_date, (datetime.now() - timedelta(days=9)).strftime('%Y-%m-%d'))
)
```

**Result**: Nov 1-3 gap → No data found → Returns `None`

### 3. Neutral Fallback Score

When no data available, returns neutral score:
```python
# scorers.py:180-182
if weekly_change is None:
    # No historical data yet - return neutral score (don't filter out)
    return 50.0  # ❌ PRIMARY SCORER (35% weight) gives neutral score!
```

---

## Impact Analysis

### On Trade Selection

**35% of the score is unreliable or wrong**, making trade selection fundamentally broken:

| Scenario | Reality | System Behavior | Trader Impact |
|----------|---------|-----------------|---------------|
| IV falling (-16%) | BAD setup (avoid) | Neutral score (50.0) | Enters bad trades |
| IV weak (+1%) | Poor setup | Neutral score (50.0) | Overvalues poor setups |
| IV rising (+21%) | Good setup | Neutral score (50.0) | Undervalues good setups |

### On Strategy Alignment

The IV Crush Strategy explicitly states:
> "Entry the open session immediately prior to earnings event"

With scoring weights:
- **35% IV Expansion Velocity** - Is premium building NOW?
- 30% Options Liquidity
- 25% IV Crush Edge
- 25% Current IV Level
- 5% Fundamentals

**When 35% of your score is wrong, your entire analysis is unreliable.**

---

## Recommended Fixes

### Fix #1: Expand Lookback Window (QUICK - 5 min)

**Modify `get_weekly_iv_change` to use flexible tolerance:**

```python
def get_weekly_iv_change(self, ticker: str, current_iv: float,
                         tolerance_days: int = 2) -> Optional[float]:
    """
    Calculate weekly IV percentage change with flexible lookback.

    Instead of requiring data EXACTLY 7-9 days ago,
    expands to 5-9 days (with tolerance_days=2)
    """
    ideal_lookback = 7

    # Expand window: look for data between (7-tolerance) and (7+tolerance) days ago
    lookback_start = (datetime.now() - timedelta(days=ideal_lookback + tolerance_days)).strftime('%Y-%m-%d')
    lookback_end = (datetime.now() - timedelta(days=ideal_lookback - tolerance_days)).strftime('%Y-%m-%d')

    cursor = conn.execute(
        """SELECT iv_value, date FROM iv_history
           WHERE ticker = ? AND date >= ? AND date <= ?
           ORDER BY date DESC LIMIT 1""",
        (ticker, lookback_start, lookback_end)
    )
    # ... rest of function
```

**Impact**: Would have found data for ALL tickers in both reports.

---

### Fix #2: Daily IV Snapshot (SYSTEMATIC - 30 min)

**Create automated daily recording** independent of analyzer runs:

```bash
# Cron job: Run at 3:30 PM ET daily (before market close)
30 15 * * 1-5 cd /path/to/project && python -m src.options.daily_iv_snapshot
```

**Benefits**:
- Continuous IV data for all earnings candidates
- No gaps in 7-day lookback window
- Weekly IV Change always calculable
- Primary scorer (35%) always works

---

### Fix #3: Add Diagnostics to Reports (VISIBILITY - 10 min)

**Show data quality in reports** so users know when scoring is degraded:

```
OPTIONS METRICS:
  Current IV: 87.38%
  Weekly IV Change: N/A ⚠️  (No IV data from 7-9 days ago)
                    → Using neutral score (50.0) for IV Expansion
                    → Score may be inaccurate!
```

---

## Validation

### Test Case 1: Nov 10 Report with Fix #1

```
CRWV: Would find Nov 4 data (6 days) → +20.7% → Score 60.0 ✓
EOSE:  Would find Nov 4 data (6 days) → +0.8% → Score 40.0 ✓
AMC:   Would find Nov 4 data (6 days) → -16.2% → Score 20.0 ✓
```

All three tickers would have correct scores instead of neutral 50.0.

### Test Case 2: AMC Correct Ranking

```
Current (broken):
  Score: 90.6/100 (3rd place)
  Signal: "Top candidate"

Fixed:
  Score: 80.1/100 (still ranked, but accurate)
  Signal: "Decent candidate, but IV leaking - watch closely"
```

---

## Recommended Implementation Order

1. **Fix #1** (5 min) - Immediate improvement with zero risk
2. **Fix #3** (10 min) - User visibility into data quality
3. **Fix #2** (30 min) - Long-term systematic solution

All three fixes are non-breaking and backward compatible.

---

## Conclusion

This bug causes the PRIMARY scoring metric (35% weight) to fail silently, returning neutral scores instead of detecting IV expansion/contraction. This directly undermines the core IV crush strategy and leads to:

- Inflated scores for bad setups (falling IV)
- Deflated scores for good setups (rising IV)
- Unreliable trade selection

**Severity: CRITICAL** - Affects every analysis run and contradicts fundamental strategy.
