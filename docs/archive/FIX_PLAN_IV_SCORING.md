# Fix Plan: Self-Healing IV Expansion Scoring

**Goal**: Fix the 35% IV Expansion scorer without requiring daily cron jobs

**Strategy**: Make the system self-healing - backfill on-demand when data is missing

---

## Fix #1: Expand Lookback Window (QUICK WIN - 5 min)

**Problem**: Looks for data EXACTLY 7-9 days ago, misses Nov 4 data (6 days ago)

**Solution**: Add tolerance parameter to find nearest data within ±2 days

**File**: `src/options/iv_history_tracker.py`

**Change**:
```python
def get_weekly_iv_change(self, ticker: str, current_iv: float,
                         tolerance_days: int = 2) -> Optional[float]:
    """
    Calculate weekly IV percentage change with flexible lookback.

    Args:
        ticker: Ticker symbol
        current_iv: Current IV percentage
        tolerance_days: Expand search window by ±N days (default: 2)

    Returns:
        Weekly IV % change, or None if no data within tolerance
    """
    if current_iv <= 0:
        return None

    conn = self._get_connection()

    # Ideal target: 7 days ago
    # Actual search: 5-9 days ago (7 ± 2 days)
    ideal_lookback = 7
    lookback_start = (datetime.now() - timedelta(days=ideal_lookback + tolerance_days)).strftime('%Y-%m-%d')
    lookback_end = (datetime.now() - timedelta(days=ideal_lookback - tolerance_days)).strftime('%Y-%m-%d')

    # Find closest date to ideal within tolerance window
    cursor = conn.execute(
        """SELECT iv_value, date FROM iv_history
           WHERE ticker = ? AND date >= ? AND date <= ?
           ORDER BY date DESC LIMIT 1""",
        (ticker, lookback_start, lookback_end)
    )

    row = cursor.fetchone()

    if not row:
        logger.debug(f"{ticker}: No IV data in {ideal_lookback}±{tolerance_days} day window")
        return None

    old_iv = row['iv_value']
    actual_date = row['date']

    if old_iv <= 0:
        return None

    # Calculate percentage change
    pct_change = ((current_iv - old_iv) / old_iv) * 100

    # Log actual days difference for transparency
    old_date_obj = datetime.strptime(actual_date, '%Y-%m-%d')
    days_diff = (datetime.now() - old_date_obj).days

    logger.debug(
        f"{ticker}: Weekly IV change = {pct_change:+.1f}% "
        f"({old_iv:.1f}% {days_diff} days ago → {current_iv:.1f}% now)"
    )

    return round(pct_change, 1)
```

**Impact**: Would fix ALL cases in Nov 10 reports (finds Nov 4 data at 6 days)

---

## Fix #2: On-Demand Backfill (SELF-HEALING - 20 min)

**Problem**: Even with tolerance, weekend/holiday gaps can still cause misses

**Solution**: Auto-backfill last 14 days when weekly_change returns None

**File**: `src/options/iv_history_backfill.py`

**Add new method**:
```python
def backfill_recent(self, ticker: str, days: int = 14) -> Dict:
    """
    Backfill recent IV data (lightweight - just last 2 weeks).

    Used when weekly IV change calculation fails due to missing data.
    Much faster than full 365-day backfill.

    Args:
        ticker: Stock ticker symbol
        days: Days to backfill (default: 14 for 2 weeks)

    Returns:
        Dict with success, data_points, message
    """
    logger.info(f"{ticker}: Backfilling recent {days} days for weekly IV change")

    try:
        # Use existing backfill_ticker with shorter window
        return self.backfill_ticker(
            ticker,
            lookback_days=days,
            sample_interval_days=1  # Daily samples for accuracy
        )
    except Exception as e:
        logger.warning(f"{ticker}: Recent backfill failed: {e}")
        return {
            'success': False,
            'data_points': 0,
            'message': str(e)
        }
```

**File**: `src/analysis/scorers.py` - Update `IVExpansionScorer.score()`

**Change**:
```python
def score(self, data: TickerData) -> float:
    """Score based on weekly IV percentage change."""
    from src.options.iv_history_tracker import IVHistoryTracker

    options_data = data.get('options_data', {})
    ticker = data.get('ticker', 'UNKNOWN')
    current_iv = options_data.get('current_iv')

    if current_iv is None or current_iv <= 0:
        return 50.0

    # Calculate weekly IV % change
    tracker = IVHistoryTracker()
    try:
        weekly_change = tracker.get_weekly_iv_change(ticker, current_iv)

        # NEW: Auto-backfill if no data (self-healing)
        if weekly_change is None:
            logger.info(f"{ticker}: No weekly IV data, attempting backfill...")
            from src.options.iv_history_backfill import IVHistoryBackfill

            backfiller = IVHistoryBackfill(iv_tracker=tracker)
            result = backfiller.backfill_recent(ticker, days=14)

            if result['success'] and result['data_points'] > 0:
                # Retry calculation with backfilled data
                weekly_change = tracker.get_weekly_iv_change(ticker, current_iv)
                logger.info(f"{ticker}: Backfilled {result['data_points']} points, weekly change = {weekly_change}")
            else:
                logger.warning(f"{ticker}: Backfill failed, using neutral score")
    finally:
        tracker.close()

    if weekly_change is None:
        # No historical data and backfill failed - return neutral score
        logger.debug(f"{ticker}: No weekly IV data available after backfill attempt")
        return 50.0

    # Score based on IV expansion velocity
    if weekly_change >= self.excellent:
        return 100.0
    elif weekly_change >= self.good:
        return 80.0
    elif weekly_change >= self.moderate:
        return 60.0
    elif weekly_change >= self.minimum:
        return 40.0
    else:
        return 20.0
```

**Impact**:
- First run: Backfills on-demand when needed
- Subsequent runs: Uses backfilled data (fast)
- No cron jobs required!

---

## Fix #3: Add Diagnostics (VISIBILITY - 10 min)

**File**: `src/analysis/report_formatter.py`

**Change** (around line 136):
```python
weekly_change = options.get('weekly_iv_change')
if weekly_change is not None:
    if weekly_change >= 40:
        change_note = "(STRONG expansion - premium building fast!)"
        icon = "🚀"
    elif weekly_change >= 20:
        change_note = "(Good expansion - moderate buildup)"
        icon = "📈"
    elif weekly_change >= 0:
        change_note = "(Weak expansion)"
        icon = "→"
    else:
        change_note = "⚠️  (LEAKING - premium falling, risky!)"
        icon = "⚠️"
    lines.append(f"  Weekly IV Change: {weekly_change:+.1f}% {icon} {change_note}")
else:
    # Data missing even after backfill attempt
    lines.append(f"  Weekly IV Change: N/A (insufficient historical data)")
    lines.append(f"                    → Using neutral score (50.0) for IV Expansion")
    lines.append(f"                    → 35% of total score may be inaccurate")
```

**Impact**: Users immediately see data quality issues

---

## Implementation Order

1. **Fix #1** (5 min) - Expand window, immediate improvement
2. **Fix #2** (20 min) - On-demand backfill, self-healing
3. **Fix #3** (10 min) - User visibility

**Total time**: 35 minutes

**Benefits**:
- ✅ No cron jobs required
- ✅ Self-healing (backfills on first use)
- ✅ Fast (only backfills 14 days, not 365)
- ✅ Transparent (shows when data is missing)
- ✅ Backward compatible

---

## Testing Plan

### Test Case 1: Nov 10 Report Replay

Run analyzer on same tickers with fixes:

```bash
python -m src.analysis.earnings_analyzer --tickers "CRWV,EOSE,AMC" 2025-11-10 --yes
```

**Expected**:
- Fix #1 finds Nov 4 data (6 days ago) immediately
- All three tickers get correct weekly IV change
- Scores match corrected values from bug analysis

### Test Case 2: Fresh Ticker (No History)

```bash
python -m src.analysis.earnings_analyzer --tickers "NVDA" 2025-11-12 --yes
```

**Expected**:
- First run: Triggers backfill_recent(14 days)
- Populates IV history
- Calculates weekly change
- Subsequent runs: Fast (uses cached data)

---

## Validation

After implementation, verify:

```python
from src.options.iv_history_tracker import IVHistoryTracker

tracker = IVHistoryTracker()

# Should now return actual change, not None
result = tracker.get_weekly_iv_change('AMC', 85.10)
print(f"AMC weekly change: {result}%")  # Expected: ~-16.2%

tracker.close()
```
