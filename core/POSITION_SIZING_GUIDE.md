# Position Sizing Guide - $XXX Portfolio

**Target:** $20-25K max loss per trade
**Portfolio:** $XXX,XXX
**Risk per Trade:** 3.33% - 4.17% of portfolio

---

## Current Configuration

Your `.env` file is configured for $20-25K max loss per trade:

```bash
# Kelly Criterion Settings
USE_KELLY_SIZING=true
KELLY_FRACTION=0.25              # 25% of full Kelly (conservative)
KELLY_MIN_EDGE=0.02              # 2% minimum edge required
KELLY_MIN_CONTRACTS=1

# Risk Management
RISK_BUDGET_PER_TRADE=250000     # Kelly capital base
MAX_CONTRACTS=50                 # Hard cap on position size

# VRP Profile
VRP_THRESHOLD_MODE=BALANCED      # Quality filter for trades
```

---

## How Kelly Criterion Position Sizing Works

### The Kelly Formula

```
f* = (p × b - q) / b
```

Where:
- **p** = Probability of profit (POP) from skew analysis
- **q** = 1 - p (probability of loss)
- **b** = Win/loss ratio (max_profit / max_loss)
- **f*** = Fraction of capital to risk (full Kelly)

### Applied to Your Configuration

**Step 1: Calculate Edge**
```
edge = p × b - q
```
Example: 70% POP, $2 credit / $3 max loss
- b = 2/3 = 0.667
- edge = 0.70 × 0.667 - 0.30 = 0.167 (16.7% edge)

**Step 2: Calculate Full Kelly**
```
kelly_full = edge / b = 0.167 / 0.667 = 0.25 (25% of capital)
```

**Step 3: Apply Fractional Kelly (Safety Factor)**
```
kelly_frac = kelly_full × KELLY_FRACTION
            = 0.25 × 0.25
            = 0.0625 (6.25% of capital)
```

**Step 4: Calculate Position Size**
```
position_size = kelly_frac × RISK_BUDGET_PER_TRADE
              = 0.0625 × $250,000
              = $15,625
```

**Step 5: Convert to Contracts**
```
contracts = position_size / max_loss_per_spread
          = $15,625 / $500
          = 31 contracts (rounded down)
```

**Step 6: Apply MAX_CONTRACTS Cap**
```
final_contracts = min(31, MAX_CONTRACTS)
                = min(31, 50)
                = 31 contracts
```

**Actual Max Loss:**
```
max_loss_total = 31 contracts × $500
               = $15,500
```

---

## Typical Position Sizes

With your configuration, here's what to expect:

### Excellent Edge (75% POP, 0.50 R/R)
- Kelly allocation: ~12-15% of risk budget
- Position size: $30,000 - $37,500
- Contracts (if $500 max loss): 60-75 contracts
- **Capped at 50 contracts = $25,000 max loss**

### Good Edge (70% POP, 0.40 R/R)
- Kelly allocation: ~8-10% of risk budget
- Position size: $20,000 - $25,000
- Contracts (if $500 max loss): 40-50 contracts
- **Likely 40-50 contracts = $20,000-$25,000 max loss**

### Moderate Edge (65% POP, 0.30 R/R)
- Kelly allocation: ~4-6% of risk budget
- Position size: $10,000 - $15,000
- Contracts (if $500 max loss): 20-30 contracts
- **Likely 20-30 contracts = $10,000-$15,000 max loss**

### Marginal Edge (60% POP, 0.25 R/R)
- Kelly allocation: ~2-4% of risk budget
- Position size: $5,000 - $10,000
- Contracts (if $500 max loss): 10-20 contracts
- **Likely 10-20 contracts = $5,000-$10,000 max loss**

---

## Parameter Explanation

### RISK_BUDGET_PER_TRADE ($250,000)

**Purpose:** Capital base for Kelly Criterion formula

**Not** the max loss per trade. This is the "account equity" Kelly uses to calculate optimal position sizing.

**Why $250K for $XXX portfolio:**
- Represents ~42% of total portfolio
- Kelly uses this to calculate position as % of this capital
- With 25% fractional Kelly, typical positions use 5-15% of this value
- Results in $12,500 - $37,500 position sizes
- Capped by MAX_CONTRACTS to stay under $25K

**Adjustment:**
- **Increase** to make positions larger
- **Decrease** to make positions smaller
- Rule of thumb: Set to 40-50% of portfolio for 3-5% risk per trade

### MAX_CONTRACTS (50)

**Purpose:** Hard safety cap on position size

**Critical Safety Feature:** Prevents any single trade from exceeding a maximum dollar risk.

**Calculation:**
```
max_loss_worst_case = MAX_CONTRACTS × max_loss_per_spread
```

With $500 max loss per spread:
```
50 contracts × $500 = $25,000 maximum possible loss
```

**Adjustment:**
- Set to: `desired_max_loss / typical_max_loss_per_spread`
- For $20K cap: `20,000 / 500 = 40 contracts`
- For $25K cap: `25,000 / 500 = 50 contracts`

### KELLY_FRACTION (0.25)

**Purpose:** Fractional Kelly for conservative sizing

**25% of full Kelly** means we use 1/4 of the optimal Kelly allocation.

**Why fractional:**
- Full Kelly maximizes long-term growth but has high variance
- 25% Kelly reduces variance significantly while keeping 90%+ of growth
- Industry standard for conservative professional trading

**Adjustment:**
- **0.20 (20%):** More conservative, smaller positions
- **0.25 (25%):** Balanced (recommended)
- **0.30 (30%):** More aggressive, larger positions
- **Never exceed 0.50 (50%)** - variance becomes too high

### KELLY_MIN_EDGE (0.02)

**Purpose:** Minimum expected value required to trade

**2% minimum edge** means Kelly won't allocate capital unless EV > 2%.

**Why 2% instead of 5%:**
- Most credit spreads have edges in the 2-4% range
- 5% edge is extremely rare in options selling
- 2% still filters out negative or marginal trades
- Allows Kelly to properly size positions with typical edges

**Example:**
- If edge = 1% → trade rejected (1 contract minimum)
- If edge = 3% → Kelly allocates capital properly
- If edge = 5% → Kelly allocates more aggressively

**Adjustment:**
- **Increase (0.03-0.04):** More selective, fewer trades
- **Decrease (0.01):** Less selective, more trades
- **Do not use 0.05+:** Filters out most viable credit spreads

---

## Adjusting for Different Max Loss Targets

### Target: $15-20K per trade (more conservative)

```bash
RISK_BUDGET_PER_TRADE=200000     # Lower Kelly capital base
MAX_CONTRACTS=40                 # Cap at 40 contracts × $500 = $20K
```

### Target: $25-30K per trade (more aggressive)

```bash
RISK_BUDGET_PER_TRADE=300000     # Higher Kelly capital base
MAX_CONTRACTS=60                 # Cap at 60 contracts × $500 = $30K
```

### Target: $10-15K per trade (very conservative)

```bash
RISK_BUDGET_PER_TRADE=150000     # Much lower Kelly capital base
MAX_CONTRACTS=30                 # Cap at 30 contracts × $500 = $15K
KELLY_FRACTION=0.20              # Also reduce Kelly fraction
```

---

## Max Loss Varies by Spread Width

**Important:** Actual max loss depends on spread strike selection.

### Wide Spreads ($7-10 width)
- Max loss per spread: $650-$950
- With 50 contract cap: $32,500-$47,500 max loss
- **Exceeds $25K target!**
- **Action:** Lower MAX_CONTRACTS to 25-35

### Standard Spreads ($5 width)
- Max loss per spread: $450-$500
- With 50 contract cap: $22,500-$25,000 max loss
- **Within target range ✓**

### Narrow Spreads ($3 width)
- Max loss per spread: $250-$300
- With 50 contract cap: $12,500-$15,000 max loss
- **Below target** (conservative)

**Recommendation:** Monitor actual spread widths in trade.sh output. Adjust MAX_CONTRACTS if spreads are consistently wider/narrower than $5.

---

## VRP Profile Impact on Position Sizing

VRP Profile affects **trade selection**, not position sizing directly.

### CONSERVATIVE (VRP ≥ 2.0x)
- Fewer trades (higher quality)
- Typically better POP → higher Kelly allocation
- Result: Larger positions on fewer, better trades

### BALANCED (VRP ≥ 1.8x)
- Moderate trade frequency
- Good POP on selected trades
- Result: Balanced positions and frequency

### AGGRESSIVE (VRP ≥ 1.5x)
- More trades (lower threshold)
- Lower average POP → lower Kelly allocation
- Result: More trades with smaller avg positions

---

## Safety Checklist

Before executing a trade recommended by `./trade.sh`:

1. **Check Max Loss:**
   ```
   contracts × max_loss_per_spread ≤ $25,000
   ```

2. **Verify Contracts:**
   ```
   contracts ≤ MAX_CONTRACTS (50)
   ```

3. **Review Kelly Log:**
   ```
   Look for: "Kelly sizing: POP=X%, edge=Y, contracts=Z"
   ```

4. **Confirm Liquidity:**
   ```
   Tier should be EXCELLENT or WARNING (not REJECT)
   ```

5. **Check Spread Width:**
   ```
   Typical: $5 wide = $450-500 max loss per contract
   Wide: $7-10 wide = $650-950 max loss per contract
   ```

---

## Example Trade Analysis

**Command:**
```bash
./trade.sh NVDA 2025-12-15
```

**Output:**
```
★ RECOMMENDED: BULL PUT SPREAD
  Short $180P / Long $175P
  Net Credit: $2.35 | Max Loss: $2.65 per contract
  Contracts: 42
  Max Profit: $9,870 | Max Loss: $11,130
  Probability of Profit: 72.3%
```

**Analysis:**
- Max loss per contract: $265 (narrow spread!)
- Contracts: 42 (under 50 cap ✓)
- Total max loss: $11,130 (under $25K ✓)
- POP: 72.3% (good edge)
- Kelly allocated ~4.2% of $250K risk budget

**Decision:** ✅ Trade within risk parameters

---

## Monitoring Position Sizing

### After Each Trade

Log the following for analysis:

```
Ticker: ___________
Trade Date: ___________
Contracts: ___________
Max Loss per Contract: $___________
Total Max Loss: $___________
POP: ___________%
VRP Ratio: ___________x
Kelly Edge: ___________%
```

### Monthly Review

- Average position size: $___________
- Largest position: $___________
- Smallest position: $___________
- # of trades capped by MAX_CONTRACTS: ___________
- Average max loss per trade: $___________

**Action Items:**
- If avg position < $15K: Increase RISK_BUDGET_PER_TRADE
- If avg position > $23K: Decrease RISK_BUDGET_PER_TRADE or MAX_CONTRACTS
- If hitting MAX_CONTRACTS cap frequently: Reduce RISK_BUDGET_PER_TRADE

---

## Advanced: Adjusting for Market Conditions

### High Volatility (VIX > 25)
```bash
# More trades available, but more uncertainty
KELLY_FRACTION=0.20              # More conservative
MAX_CONTRACTS=40                 # Lower cap
VRP_THRESHOLD_MODE=BALANCED      # Don't chase low VRP
```

### Normal Volatility (VIX 15-25)
```bash
# Standard settings
KELLY_FRACTION=0.25
MAX_CONTRACTS=50
VRP_THRESHOLD_MODE=BALANCED
```

### Low Volatility (VIX < 15)
```bash
# Fewer high-VRP opportunities
KELLY_FRACTION=0.25
MAX_CONTRACTS=50
VRP_THRESHOLD_MODE=AGGRESSIVE    # Accept lower VRP
# OR use CONSERVATIVE and trade less frequently
```

---

## FAQ

**Q: Why not just set a fixed number of contracts per trade?**

A: Kelly Criterion dynamically adjusts position size based on edge. Higher probability, better reward/risk = larger position. This optimizes long-term growth while managing risk.

**Q: What if Kelly wants to trade 60+ contracts?**

A: MAX_CONTRACTS (50) caps it at $25K max loss. Kelly will size up to the cap but never exceed it.

**Q: How do I make positions smaller across the board?**

A: Lower RISK_BUDGET_PER_TRADE or KELLY_FRACTION. Both will scale all positions down proportionally.

**Q: Can I override Kelly for a specific trade?**

A: Yes. The system shows recommended contracts. You can manually reduce size in your broker before execution.

**Q: What if my actual results differ from backtests?**

A: Monitor actual POP vs predicted POP. If consistently lower, increase KELLY_MIN_EDGE to be more selective. If higher, you can decrease it.

**Q: Should I adjust for winners vs losers?**

A: No. Kelly automatically accounts for win rate via POP. Don't increase size after wins or decrease after losses (gambler's fallacy).

**Q: Why was I seeing only 1 contract per trade?**

A: If KELLY_MIN_EDGE is set too high (e.g., 0.05 or 5%), Kelly filters out most credit spreads which typically have 2-4% edges. Lower to 0.02 (2%) to allow proper position sizing for typical options selling strategies.

---

## Summary

**Your Configuration (Current):**
- Portfolio: $XXX,XXX
- Target max loss: $20-25K (3.33%-4.17%)
- RISK_BUDGET_PER_TRADE: $250,000
- MAX_CONTRACTS: 50
- KELLY_FRACTION: 0.25 (25% fractional Kelly)
- KELLY_MIN_EDGE: 0.02 (2% minimum)

**Expected Behavior:**
- Most trades: 20-45 contracts
- Typical max loss: $10K-$22.5K (assuming $500 max loss per contract)
- Excellent trades: Capped at 50 contracts = $25K max loss
- Marginal trades: 5-15 contracts = $2.5K-$7.5K max loss

**Safety Features:**
✅ MAX_CONTRACTS hard cap prevents > $25K loss
✅ KELLY_MIN_EDGE filters out low-edge trades
✅ 25% fractional Kelly reduces variance
✅ VRP_THRESHOLD_MODE=BALANCED filters quality

**Action Required:**
- Monitor first 5-10 trades to verify position sizes
- Adjust RISK_BUDGET_PER_TRADE if needed
- Log actual max loss per trade and compare to target

---

**Questions or adjustments needed? See CONFIG_REFERENCE.md or contact support.**
