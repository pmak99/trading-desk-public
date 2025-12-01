# POP Weight Increase to 40%

**Date:** December 2025
**Change:** Increased POP (Probability of Profit) weight from 30% to 40%
**Reason:** User preference to prioritize high-probability trades

---

## Weight Changes

### Before (Kelly Edge Fix)
| Factor | Weight | Purpose |
|--------|--------|---------|
| POP | 30.0% | Probability of profit |
| Liquidity | 25.0% | Trade execution quality |
| VRP | 20.0% | Volatility risk premium edge |
| Kelly Edge | 15.0% | Expected value (POP + R/R combined) |
| Greeks | 10.0% | Theta/vega quality |
| **Total** | **100%** | |

### After (POP Prioritized)
| Factor | Weight | Change | Purpose |
|--------|--------|--------|---------|
| **POP** | **40.0%** | **+10.0** | Probability of profit |
| Liquidity | 22.0% | -3.0 | Trade execution quality |
| VRP | 17.0% | -3.0 | Volatility risk premium edge |
| Kelly Edge | 13.0% | -2.0 | Expected value |
| Greeks | 8.0% | -2.0 | Theta/vega quality |
| **Total** | **100%** | | |

**Key Change:** POP now represents 40% of total score (+33% increase in weight)

---

## Impact on Strategy Scoring

### Example: Bull Put Spread (84.6% POP)

**Old Weights:**
- POP: 30.00 points (84.6% POP, capped at target)
- Liquidity: 12.50 points (WARNING tier)
- VRP: 20.00 points (excellent)
- Edge: 3.55 points (2.37% edge)
- Greeks: 10.00 points (assumed)
- **Total: 76.05/100**

**New Weights:**
- **POP: 40.00 points** (+10.00)
- Liquidity: 11.00 points (-1.50)
- VRP: 17.00 points (-3.00)
- Edge: 3.08 points (-0.47)
- Greeks: 8.00 points (-2.00)
- **Total: 79.08/100** (+3.03)

**Result:** High POP strategies gain ~3 points

---

### Example: Iron Condor (59.5% POP)

**Old Weights:**
- POP: 27.46 points (59.5% POP, below target)
- Liquidity: 12.50 points (WARNING tier)
- VRP: 20.00 points (excellent)
- Edge: 0.00 points (negative edge)
- Greeks: 10.00 points (assumed)
- **Total: 69.96/100**

**New Weights:**
- **POP: 36.62 points** (+9.16)
- Liquidity: 11.00 points (-1.50)
- VRP: 17.00 points (-3.00)
- Edge: 0.00 points (0.00)
- Greeks: 8.00 points (-2.00)
- **Total: 72.62/100** (+2.65)

**Result:** Lower POP strategies gain less (~2.7 points)

---

## Relative Advantage

**Gap Between Strategies:**

| Scenario | Old Gap | New Gap | Change |
|----------|---------|---------|--------|
| BPS (84.6%) vs IC (59.5%) | 6.09 pts | 6.46 pts | +0.37 pts |

**Impact:**
- High POP strategies (80%+) now have **bigger advantage** over low POP strategies
- The gap widens by 0.37 points, making high-probability trades even more favorable
- Strategies with POP near target (65%) benefit most from weight increase

---

## Strategy Recommendations Impact

### Strategies That Will Rank Higher
✅ **High-probability credit spreads** (80%+ POP)
- Bull put spreads with far OTM strikes
- Bear call spreads with far OTM strikes
- Wide iron condors with high POP

### Strategies That Will Rank Lower
⚠️ **Lower-probability strategies** (<65% POP)
- Tight iron condors
- Aggressive iron butterflies
- Closer-to-money credit spreads

### What This Means for Trade Selection
- System will prefer **safer, higher-probability** setups
- Lower R/R but higher POP strategies will score better
- Example: 85% POP with 0.20 R/R may beat 70% POP with 0.40 R/R
- Aligns with conservative, high-win-rate approach

---

## Mathematical Analysis

### POP Score Formula
```
pop_score = min(strategy_pop / target_pop, 1.0) × pop_weight
```

**Target POP:** 65% (unchanged)

### Score at Different POP Levels

| POP | Old Score (30%) | New Score (40%) | Gain |
|-----|-----------------|-----------------|------|
| 50% | 23.08 | 30.77 | +7.69 |
| 60% | 27.69 | 36.92 | +9.23 |
| **65%** (target) | 30.00 | 40.00 | +10.00 |
| 70% | 30.00 | 40.00 | +10.00 |
| 80% | 30.00 | 40.00 | +10.00 |
| 90% | 30.00 | 40.00 | +10.00 |

**Note:** Scores cap at target (65%), so strategies with 65%+ POP all get full POP points.

**Key Insight:** Below-target POP strategies gain less than full +10 points, creating stronger preference for >65% POP setups.

---

## Implementation

### Files Modified
- `src/config/config.py`: Updated `ScoringWeights` class
  - `pop_weight: 30.0` → `pop_weight: 40.0`
  - `liquidity_weight: 25.0` → `liquidity_weight: 22.0`
  - `vrp_weight: 20.0` → `vrp_weight: 17.0`
  - `reward_risk_weight: 15.0` → `reward_risk_weight: 13.0`
  - `greeks_weight: 10.0` → `greeks_weight: 8.0`

### No Code Changes Required
The scoring algorithm automatically uses the weights from config. No changes to:
- `src/domain/scoring/strategy_scorer.py`
- Strategy generation logic
- Output formatting

---

## Expected Behavior

### When Running `./trade.sh`

**Before:**
```
★ RECOMMENDED: BULL PUT SPREAD
  Score: 76.1/100

  Strategy 2: IRON CONDOR
  Score: 70.0/100
```

**After:**
```
★ RECOMMENDED: BULL PUT SPREAD
  Score: 79.1/100  (+3.0)

  Strategy 2: IRON CONDOR
  Score: 72.6/100  (+2.7)
```

**Result:** Same recommendation, but with **increased score gap** (6.5 vs 6.1 points)

---

## Compatibility

✅ **Backward Compatible:** All existing functionality works
✅ **No Breaking Changes:** Output format unchanged
✅ **Config Only:** Pure configuration change, no algorithmic modifications
✅ **Preserves Kelly Edge Fix:** Still uses Kelly edge scoring (not raw R/R)

---

## Monitoring

After this change, monitor:
1. **Win rate:** Should increase (favoring higher POP trades)
2. **Average profit per trade:** May decrease (lower R/R on high POP trades)
3. **Sharpe ratio:** Should improve or stay similar (more consistent wins)
4. **Strategy distribution:** More credit spreads, fewer iron condors/butterflies

---

## Reverting (If Needed)

To revert to original weights:

```python
# In src/config/config.py
pop_weight: float = 30.0          # Revert from 40.0
liquidity_weight: float = 25.0    # Revert from 22.0
vrp_weight: float = 20.0          # Revert from 17.0
reward_risk_weight: float = 15.0  # Revert from 13.0
greeks_weight: float = 10.0       # Revert from 8.0
```

---

## Summary

- **POP weight:** 30% → **40%** (+33% increase)
- **Impact:** High POP strategies score 3+ points higher
- **Goal:** Prioritize high-probability, safer trades
- **Alignment:** Matches conservative trading philosophy
- **Status:** ✅ Implemented and tested

**User preference fulfilled:** POP is now the **dominant scoring factor** at 40% of total score.
