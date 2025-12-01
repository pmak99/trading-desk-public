# Profit Zone vs Implied Move Fix

**Date:** December 2025
**Issue:** Iron Butterfly recommended despite narrow profit zone vs large implied move
**Solution:** Apply penalty multiplier for strategies with profit zones narrower than implied move

---

## Problem Statement

Iron Butterflies and tight Iron Condors were being recommended even when their profit zones were far too narrow for the implied move.

### Example: MDB Earnings

**Setup:**
- Stock: MDB at ~$335
- Implied Move: 12.98% (~$43 move)
- Iron Butterfly profit zone: $330.05 - $339.95 (~$10 wide, 2.96% of stock price)

**Problem:**
- Profit zone (2.96%) is only 23% of implied move (12.98%)
- Stock extremely unlikely to stay within $10 range with $43 expected move
- Yet Iron Butterfly scored 75.8/100 vs Bull Put Spread 65.5/100

**User feedback:** "why is iron butterfly the top strategy recommended here? the implied move is so big that i don't think it'll stay close to ATM"

---

## Root Cause

The scoring algorithm evaluated:
- POP (Probability of Profit)
- VRP edge
- Kelly edge
- Liquidity
- Greeks

But **never checked if the profit zone width made sense relative to expected price movement**.

A 49.6% POP for an Iron Butterfly with a 2.96% profit zone when expecting a 12.98% move is nonsensical - the real POP is much lower.

---

## Solution: Profit Zone Multiplier

Added a **penalty multiplier** that reduces scores for strategies with narrow profit zones relative to implied move.

### Implementation

**Formula:**
```python
zone_to_move_ratio = profit_zone_pct / implied_move_pct
```

**Penalty Schedule:**

| Ratio | Profit Zone | Multiplier | Severity |
|-------|-------------|------------|----------|
| ≥ 1.0 | ≥ 100% of implied move | 1.00x | No penalty |
| 0.70-1.0 | 70-100% of implied move | 0.90-1.00x | Slight |
| 0.40-0.70 | 40-70% of implied move | 0.70-0.90x | Moderate |
| 0.20-0.40 | 20-40% of implied move | 0.50-0.70x | Heavy |
| < 0.20 | < 20% of implied move | 0.30x | Severe |

**Application:**
```python
final_score = base_score × profit_zone_multiplier
```

---

## Results

### MDB Iron Butterfly (Original Issue)

**Profit Zone Analysis:**
- Breakevens: $330.05 - $339.95
- Width: $9.90
- Stock price: $335.00
- **Profit zone: 2.96%** of stock price
- **Implied move: 12.98%**
- **Ratio: 0.23** (profit zone is only 23% of implied move)

**Scoring:**
- Base score: 75.8/100
- **Multiplier: 0.53x** (heavy penalty for 23% ratio)
- **Final score: 40.0/100** (-35.8 points)

**Result:** ✅ Iron Butterfly no longer recommended

---

### Bull Put Spread (Credit Spread)

**Single Breakeven:**
- Credit spreads have one-sided risk
- Better suited for large moves
- **Multiplier: 1.00x** (no penalty - exempted)

**Scoring:**
- Base score: 65.5/100
- Multiplier: 1.00x
- **Final score: 65.5/100**

**Result:** ✅ Credit spread now wins by **25.5 points**

---

## Impact on Different Strategies

### Test Cases

| Strategy | Profit Zone | Implied Move | Ratio | Multiplier | Score Impact |
|----------|-------------|--------------|-------|------------|--------------|
| Wide Iron Condor | 12.98% | 12.98% | 1.00 | 1.00x | 75.8 → 75.8 |
| Moderate IC | 10.38% | 12.98% | 0.80 | 0.93x | 75.8 → 70.7 |
| Tight IC | 6.49% | 12.98% | 0.50 | 0.77x | 75.8 → 58.1 |
| **Iron Butterfly** | **2.96%** | **12.98%** | **0.23** | **0.53x** | **75.8 → 40.2** |
| Credit Spread | N/A (single BE) | 12.98% | N/A | 1.00x | 65.5 → 65.5 |

**Key Insights:**
- Iron Butterflies (narrowest zones) penalized most heavily
- Wide Iron Condors (zones matching implied move) not penalized
- Credit spreads exempted (one-sided risk, handle large moves better)

---

## Why Credit Spreads Are Exempted

Credit spreads (Bull Put, Bear Call) have:
1. **Single breakeven** - profit as long as stock doesn't cross one level
2. **One-sided risk** - only lose if stock moves strongly in one direction
3. **Better handling of large moves** - far OTM spreads can accommodate wide moves

Iron Butterflies/Condors:
1. **Two breakevens** - need stock to stay in narrow range
2. **Two-sided risk** - lose if stock moves significantly in either direction
3. **Poor handling of large moves** - narrow profit zones get violated easily

---

## Implementation Details

### Modified Files

**`src/domain/scoring/strategy_scorer.py`:**
- Added `_calculate_profit_zone_multiplier()` method
- Modified `_score_with_greeks()` to apply multiplier
- Modified `_score_without_greeks()` to apply multiplier

### Code

```python
def _calculate_profit_zone_multiplier(self, strategy: Strategy, vrp: VRPResult) -> float:
    """Calculate penalty for narrow profit zones vs implied move."""

    # For two-breakeven strategies (IC, IB)
    if len(strategy.breakeven) >= 2:
        breakevens_sorted = sorted([float(be.amount) for be in strategy.breakeven])
        profit_zone_width = breakevens_sorted[-1] - breakevens_sorted[0]
        stock_price_estimate = (breakevens_sorted[-1] + breakevens_sorted[0]) / 2.0

        profit_zone_pct = (profit_zone_width / stock_price_estimate) * 100
        zone_to_move_ratio = profit_zone_pct / vrp.implied_move_pct

        # Apply graduated penalty based on ratio
        if zone_to_move_ratio >= 1.0:
            return 1.0  # No penalty
        elif zone_to_move_ratio >= 0.70:
            return 0.9 + (zone_to_move_ratio - 0.70) * (0.1 / 0.30)
        elif zone_to_move_ratio >= 0.40:
            return 0.7 + (zone_to_move_ratio - 0.40) * (0.2 / 0.30)
        elif zone_to_move_ratio >= 0.20:
            return 0.5 + (zone_to_move_ratio - 0.20) * (0.2 / 0.20)
        else:
            return 0.3  # Severe penalty

    # Single-breakeven strategies (credit spreads) - no penalty
    return 1.0
```

---

## Expected Behavior After Fix

### High Implied Move (>10%)
- **Iron Butterflies:** Heavily penalized (0.3-0.6x multiplier)
- **Tight Iron Condors:** Moderately penalized (0.6-0.8x multiplier)
- **Wide Iron Condors:** Slightly penalized or no penalty (0.9-1.0x multiplier)
- **Credit Spreads:** No penalty (1.0x multiplier)

**Result:** Credit spreads preferred

### Moderate Implied Move (5-10%)
- **Iron Butterflies:** Moderately penalized (0.5-0.7x)
- **Wide Iron Condors:** Slight penalty (0.85-0.95x)
- **Credit Spreads:** No penalty (1.0x)

**Result:** Wide Iron Condors competitive with credit spreads

### Low Implied Move (<5%)
- **Iron Butterflies:** Slight penalty (0.8-0.9x)
- **Wide Iron Condors:** No penalty (1.0x)
- **Credit Spreads:** No penalty (1.0x)

**Result:** All strategies competitive (as they should be for small moves)

---

## Compatibility

✅ **No Breaking Changes**
- Weights unchanged (POP 40%, Liquidity 22%, etc.)
- Output format unchanged
- Existing strategies still generated
- Pure scoring penalty (doesn't reject strategies)

✅ **Works with Kelly Edge Fix**
- Multiplier applied to final score after all factor scoring
- Kelly edge still prevents negative EV trades
- Both fixes work together to improve recommendations

---

## Monitoring

After this fix, monitor:
1. **Iron Butterfly frequency:** Should drop significantly for high IV events
2. **Iron Condor selection:** Should favor wider strikes when implied move is large
3. **Credit spread preference:** Should increase for earnings plays
4. **User satisfaction:** Fewer "why is this recommended?" questions

---

## Testing

**Validation Test (MDB Example):**
```
Iron Butterfly:
  Profit zone: 2.96% (only 23% of 12.98% implied move)
  Base score: 75.8
  Multiplier: 0.53x
  Final score: 40.0 ✅

Bull Put Spread:
  Single breakeven (exempt from penalty)
  Base score: 65.5
  Multiplier: 1.0x
  Final score: 65.5 ✅

Result: Credit spread wins by 25.5 points ✅
```

---

## Summary

**Problem:** Iron Butterfly with 2.96% profit zone recommended when implied move is 12.98%

**Solution:** Apply 0.3-1.0x penalty multiplier based on profit zone / implied move ratio

**Impact:**
- Iron Butterfly: 75.8 → 40.0 (-47% penalty)
- Bull Put Spread: 65.5 → 65.5 (no penalty)
- Credit spread now correctly recommended

**Status:** ✅ Implemented and Validated
