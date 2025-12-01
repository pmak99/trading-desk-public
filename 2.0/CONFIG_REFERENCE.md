# Configuration Reference - New Parameters

## Kelly Criterion Position Sizing

### USE_KELLY_SIZING
- **Type**: Boolean
- **Default**: `true`
- **Description**: Enable Kelly Criterion position sizing instead of fixed risk budget
- **Values**: `true` (use Kelly), `false` (use old method)

### KELLY_FRACTION
- **Type**: Float (0.0 - 1.0)
- **Default**: `0.25`
- **Description**: Fraction of full Kelly to use (for safety)
- **Recommended**: 0.20-0.30 (20-30% of full Kelly)
- **Aggressive**: 0.40-0.50
- **Conservative**: 0.10-0.20

### KELLY_MIN_EDGE
- **Type**: Float
- **Default**: `0.05`
- **Description**: Minimum edge required to trade (5% expected return)
- **Range**: 0.03-0.10
- **Impact**: Higher = fewer trades, only best setups

### KELLY_MIN_CONTRACTS
- **Type**: Integer
- **Default**: `1`
- **Description**: Minimum position size even if Kelly suggests 0
- **Range**: 1-5
- **Impact**: Safety floor for low-edge trades

---

## VRP Threshold Profiles

### VRP_THRESHOLD_MODE
- **Type**: String (enum)
- **Default**: `BALANCED`
- **Description**: Select VRP threshold profile
- **Values**:
  - `CONSERVATIVE`: 2.0x/1.5x/1.2x (fewer trades, stronger edge)
  - `BALANCED`: 1.8x/1.4x/1.2x (moderate, RECOMMENDED)
  - `AGGRESSIVE`: 1.5x/1.3x/1.1x (more trades, acceptable edge)
  - `LEGACY`: 7.0x/4.0x/1.5x (overfitted, NOT recommended)

### VRP_EXCELLENT
- **Type**: Float
- **Default**: `1.8` (from BALANCED profile)
- **Description**: Threshold for EXCELLENT rating
- **Override**: Can override profile default
- **Range**: 1.5-2.5

### VRP_GOOD
- **Type**: Float
- **Default**: `1.4` (from BALANCED profile)
- **Description**: Threshold for GOOD rating
- **Override**: Can override profile default
- **Range**: 1.3-2.0

### VRP_MARGINAL
- **Type**: Float
- **Default**: `1.2` (from BALANCED profile)
- **Description**: Minimum threshold for MARGINAL (tradeable)
- **Override**: Can override profile default
- **Range**: 1.1-1.5

---

## Example Configurations

### Conservative Trader (Low Risk Tolerance)
```bash
VRP_THRESHOLD_MODE=CONSERVATIVE
USE_KELLY_SIZING=true
KELLY_FRACTION=0.20
KELLY_MIN_EDGE=0.08
```

**Expected:**
- 5-10 trades per quarter
- Smaller position sizes
- Higher win rate
- Lower P&L variance

### Balanced Trader (Default, Recommended)
```bash
VRP_THRESHOLD_MODE=BALANCED
USE_KELLY_SIZING=true
KELLY_FRACTION=0.25
KELLY_MIN_EDGE=0.05
```

**Expected:**
- 10-15 trades per quarter
- Moderate position sizes
- Good edge/frequency balance
- Statistically robust sample size

### Aggressive Trader (Active Management)
```bash
VRP_THRESHOLD_MODE=AGGRESSIVE
USE_KELLY_SIZING=true
KELLY_FRACTION=0.30
KELLY_MIN_EDGE=0.03
```

**Expected:**
- 20-30 trades per quarter
- More variable position sizes
- More opportunities, lower per-trade edge
- Requires active monitoring

### Research/Backtesting Mode
```bash
VRP_THRESHOLD_MODE=LEGACY
USE_KELLY_SIZING=false
```

**Use Case:**
- Compare with historical performance
- Understand old system behavior
- NOT for live trading

---

## Profile Comparison

| Metric | CONSERVATIVE | BALANCED | AGGRESSIVE | LEGACY |
|--------|--------------|----------|------------|--------|
| **VRP Excellent** | 2.0x | 1.8x | 1.5x | 7.0x |
| **VRP Good** | 1.5x | 1.4x | 1.3x | 4.0x |
| **VRP Marginal** | 1.2x | 1.2x | 1.1x | 1.5x |
| **Trades/Quarter** | 5-10 | 10-15 | 20-30 | 1-2 |
| **Avg Edge** | ~15-20% | ~10-15% | ~5-10% | ~20-30% |
| **Win Rate** | ~75-80% | ~70-75% | ~65-70% | ~80-85% |
| **Sample Time** | 2-3 qtrs | 1-2 qtrs | 1 qtr | 2-5 years |
| **Risk Level** | Low | Medium | High | Very Low |

---

## Kelly Sizing Examples

### High Edge Setup (75% POP, 0.67 R/R)
```
Edge: 0.75 × 0.67 - 0.25 = 0.25 (25%)
Full Kelly: 0.25 / 0.67 = 37.5% of capital
Fractional (25%): 9.375% of capital
Position: $20K × 9.375% = $1,875
Max Loss: $300/contract
Contracts: 6
```

### Marginal Setup (65% POP, 0.67 R/R)
```
Edge: 0.65 × 0.67 - 0.35 = 0.083 (8.3%)
Full Kelly: 0.083 / 0.67 = 12.5% of capital
Fractional (25%): 3.125% of capital
Position: $20K × 3.125% = $625
Max Loss: $300/contract
Contracts: 2
```

### Negative Edge Setup (60% POP, 0.11 R/R)
```
Edge: 0.60 × 0.11 - 0.40 = -0.333 (NEGATIVE!)
Kelly suggests: NO TRADE
Actual: Minimum 1 contract (safety floor)
```

---

## Migration Checklist

### Before Deployment
- [ ] Review current VRP thresholds
- [ ] Decide on profile (CONSERVATIVE, BALANCED, or AGGRESSIVE)
- [ ] Review Kelly fraction (start with 0.25)
- [ ] Update .env file with new parameters
- [ ] Test on paper trades for 1-2 weeks
- [ ] Monitor position sizing behavior
- [ ] Verify trade frequency aligns with expectations

### After Deployment
- [ ] Log first 10 trades with new sizing
- [ ] Compare position sizes to old method
- [ ] Verify edge calculation in logs
- [ ] Check trade frequency (should match profile)
- [ ] Monitor capital allocation
- [ ] Adjust kelly_fraction if needed (0.20-0.30 range)

### Red Flags
- ⚠️ All trades getting 1 contract → Edge too low, check VRP profile
- ⚠️ Trades consistently >50 contracts → Check max_contracts setting
- ⚠️ <5 trades per quarter with BALANCED → Consider AGGRESSIVE
- ⚠️ >30 trades per quarter with CONSERVATIVE → Consider BALANCED
- ⚠️ Negative edge trades being sized up → Kelly sizing not working

---

## Quick Start

### Minimal .env Changes (Use Defaults)
```bash
# Nothing needed - defaults are good!
# USE_KELLY_SIZING=true (default)
# VRP_THRESHOLD_MODE=BALANCED (default)
# KELLY_FRACTION=0.25 (default)
```

### Recommended .env (Explicit)
```bash
# Position sizing
USE_KELLY_SIZING=true
KELLY_FRACTION=0.25
KELLY_MIN_EDGE=0.05
KELLY_MIN_CONTRACTS=1

# VRP thresholds
VRP_THRESHOLD_MODE=BALANCED

# Risk budget (unchanged)
RISK_BUDGET_PER_TRADE=20000.0
MAX_CONTRACTS=100
```

### Conservative .env (Lower Risk)
```bash
USE_KELLY_SIZING=true
KELLY_FRACTION=0.20
KELLY_MIN_EDGE=0.08
VRP_THRESHOLD_MODE=CONSERVATIVE
```

---

## Troubleshooting

### Position Sizes Too Small
- Increase `KELLY_FRACTION` (0.25 → 0.30)
- Lower `KELLY_MIN_EDGE` (0.05 → 0.03)
- Use AGGRESSIVE profile

### Position Sizes Too Large
- Decrease `KELLY_FRACTION` (0.25 → 0.20)
- Increase `KELLY_MIN_EDGE` (0.05 → 0.08)
- Use CONSERVATIVE profile
- Lower `MAX_CONTRACTS`

### Too Few Trades
- Use AGGRESSIVE profile
- Lower VRP thresholds manually
- Check historical data (need 4+ quarters)

### Too Many Trades
- Use CONSERVATIVE profile
- Increase VRP thresholds manually
- Increase `KELLY_MIN_EDGE`

### Negative Edge Trades
- Kelly sizing should give 1 contract minimum
- Check VRP calculation (might be data issue)
- Review POP estimation
- Consider increasing `VRP_MARGINAL` threshold

---

## Support

For questions or issues:
1. Review FIXES_SUMMARY.md for detailed rationale
2. Check logs for Kelly sizing debug output
3. Verify .env configuration matches intended profile
4. Compare position sizes before/after for same setups
