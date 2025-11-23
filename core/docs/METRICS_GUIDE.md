# IV Crush 2.0 - Metrics Guide

**Understanding Your Trading Signals**

This guide explains the key metrics used to identify high-probability IV crush opportunities.

---

## 📊 Core Metrics

### 1. **Implied Move** (Implied Volatility)
```
Example: Implied 10.73%
```

**What it is:**
- The market's prediction of how much the stock will move after earnings
- Derived from options prices (straddle cost / stock price)
- Represents what options sellers are charging for risk

**How we calculate it:**
```
Implied Move = (ATM Call Premium + ATM Put Premium) / Stock Price
```

**Real example:**
```
Stock Price: $324.19
ATM Straddle Cost: $34.77
Implied Move: $34.77 / $324.19 = 10.73%
```

**What it means:**
- Market expects stock to move **±10.73%** after earnings
- Higher implied move = more expensive options premium
- This is what we're selling (the inflated expectation)

---

### 2. **Historical Mean Move**
```
Example: Historical 0.94%
```

**What it is:**
- Average actual price movement from past earnings (last 12 quarters)
- Measured from close before earnings to close after earnings
- Based on empirical data, not predictions

**How we calculate it:**
```python
# For each past earnings:
move = abs(close_after_earnings - close_before_earnings) / close_before_earnings

# Then average:
historical_mean = mean(last_12_moves)
```

**Real example:**
```
Last 12 earnings moves: [1.2%, 0.8%, 1.5%, 0.6%, 0.9%, 1.1%, 0.7%, 1.0%, 0.8%, 0.9%, 1.2%, 0.6%]
Historical Mean: 0.94%
```

**What it means:**
- Stock **actually moved** on average **0.94%** in past earnings
- Reality vs. Expectation
- Lower historical = more consistent, predictable behavior

---

### 3. **VRP Ratio** (Volatility Risk Premium)
```
Example: VRP 11.37x → EXCELLENT
```

**What it is:**
- The edge: How much market overprices volatility
- Ratio of implied move to historical move
- **The primary signal** for trade selection

**Formula:**
```
VRP Ratio = Implied Move % / Historical Mean Move %
```

**Real example:**
```
VRP = 10.73% / 0.94% = 11.37x
```

**Interpretation:**

| VRP Ratio | Rating      | Meaning                                    |
|-----------|-------------|-------------------------------------------|
| < 1.5x    | SKIP        | No edge (market fairly priced)            |
| 1.5-2.5x  | MARGINAL    | Small edge (risky, selective)             |
| 2.5-4.0x  | GOOD        | Solid edge (tradeable)                    |
| 4.0-7.0x  | EXCELLENT   | Strong edge (high priority)               |
| > 7.0x    | EXCELLENT   | Exceptional edge (rare, highest priority) |

**What it means:**
- **11.37x**: Market prices options as if stock will move **11 times more** than it historically does
- This is our profit opportunity
- We sell the overpriced insurance and collect premium

---

### 4. **Edge Score**
```
Example: Edge 6.26
```

**What it is:**
- Normalized confidence metric
- Combines VRP ratio with statistical significance
- Accounts for consistency of historical moves

**Formula:**
```python
# Simplified version:
edge_score = (vrp_ratio - 1.0) * consistency_factor

# Consistency factor considers:
# - Standard deviation of historical moves
# - Number of data points
# - Recency weighting
```

**Interpretation:**

| Edge Score | Trade Quality                             |
|------------|------------------------------------------|
| < 0.5      | Skip (no statistical edge)               |
| 0.5-2.0    | Marginal (proceed with caution)          |
| 2.0-4.0    | Good (tradeable opportunity)             |
| 4.0-7.0    | Excellent (high confidence)              |
| > 7.0      | Exceptional (rare, highest priority)     |

**What it means:**
- **6.26**: High confidence in VRP signal
- Higher edge = more reliable, consistent historical pattern
- Factors in both magnitude (VRP) and reliability (consistency)

---

### 5. **Recommendation**
```
Example: EXCELLENT
```

**What it is:**
- Trading decision based on VRP thresholds
- Pre-configured based on backtested performance

**Levels:**

#### 🔴 **SKIP**
- VRP < 1.5x
- No statistical edge
- **Action:** Pass on this trade

#### 🟡 **MARGINAL**
- VRP 1.5-2.5x
- Small edge, higher risk
- **Action:** Consider only if:
  - High liquidity (tight spreads)
  - Strong directional conviction
  - Portfolio diversification

#### 🟢 **GOOD**
- VRP 2.5-4.0x
- Solid edge
- **Action:** Trade with standard position size

#### 🟩 **EXCELLENT**
- VRP > 4.0x
- Strong edge
- **Action:** Prioritize this trade
  - May increase position size (within risk limits)
  - Highest probability of profit

---

## 🎯 Example Analysis Walkthrough

### Ticker: ADBE (Adobe)
```
================================================================================
Analyzing ADBE
================================================================================
Earnings Date: 2025-12-10
Expiration: 2025-12-12

📊 Calculating Implied Move...
✓ Implied Move: 10.73%
  Stock Price: $324.19
  ATM Strike: $320.00
  Straddle Cost: $34.77

📊 Fetching Historical Moves...
✓ Found 12 historical moves

📊 Calculating VRP...
✓ VRP Ratio: 11.37x
  Implied Move: 10.73%
  Historical Mean: 0.94%
  Edge Score: 6.26
  Recommendation: EXCELLENT

✅ TRADEABLE OPPORTUNITY
```

### What This Tells Us:

1. **Market Expectation (Implied)**
   - Options market prices in a **10.73% move** after earnings
   - Straddle costs **$34.77** (expensive insurance)

2. **Reality Check (Historical)**
   - Stock historically only moved **0.94%** on earnings
   - Very consistent, predictable behavior

3. **The Edge (VRP)**
   - Market overprices by **11.37x**
   - Options are **massively overpriced**
   - Strong statistical edge for sellers

4. **Confidence (Edge Score)**
   - **6.26** = Very high confidence
   - Historical pattern is consistent
   - Signal is reliable

5. **Decision (Recommendation)**
   - **EXCELLENT** = High-priority trade
   - Strong probability of profit
   - Prioritize this opportunity

---

## 💡 How to Use These Metrics

### Step 1: Filter by Recommendation
```bash
# Only trade EXCELLENT and GOOD opportunities
VRP > 2.5x (GOOD or better)
```

### Step 2: Rank by VRP Ratio
```
Higher VRP = Higher Edge = Better Trade
```

**Example Rankings:**
```
1. AKAM:  VRP 15.78x  ← Trade first
2. AIG:   VRP 10.33x  ← Trade second
3. ADBE:  VRP 11.37x  ← Trade third
4. AVGO:  VRP 5.49x   ← Trade fourth
5. AEP:   VRP 3.72x   ← Consider (lower priority)
```

### Step 3: Validate with Edge Score
```
Edge Score > 4.0 = High confidence
Edge Score 2.0-4.0 = Moderate confidence
Edge Score < 2.0 = Lower confidence
```

### Step 4: Check Implied Move
```
Higher Implied Move = More premium collected
BUT also = Market expects bigger move
```

**Balance:**
- **High VRP + High Implied** = Maximum profit potential
- **High VRP + Low Implied** = Lower premium, but safer

---

## 🚨 Common Misconceptions

### ❌ "Higher Implied Move is Always Better"
**Wrong!** Higher implied move means:
- ✅ More premium collected
- ❌ Market expects bigger actual move
- ❌ Higher risk if wrong

**What matters:** The **gap** between implied and historical (VRP Ratio)

---

### ❌ "VRP is the Only Thing That Matters"
**Wrong!** Also consider:
- Edge Score (confidence in signal)
- Liquidity (bid-ask spreads)
- Historical consistency
- Earnings timing (BMO vs AMC)
- Sector concentration in portfolio

---

### ❌ "EXCELLENT Always Wins"
**Wrong!** EXCELLENT means:
- ✅ Strong statistical edge
- ✅ High probability of profit
- ❌ Not guaranteed (markets can surprise)
- ❌ Still need risk management

**Example:**
- VRP 11.37x ≠ 100% win rate
- Means: Market overprices by 11x **on average**
- Some trades will still lose (that's options trading)

---

## 📈 Real Output Examples

### Scan Mode Output:
```
🎯 Ranked by VRP Ratio:
   1. AKAM  : VRP 15.78x | Implied 15.15% | Edge 10.56 | EXCELLENT
   2. AIG   : VRP 10.33x | Implied 10.30% | Edge 7.46  | EXCELLENT
   3. ADBE  : VRP 11.37x | Implied 10.73% | Edge 6.26  | EXCELLENT
   4. AVGO  : VRP 5.49x  | Implied 12.72% | Edge 3.63  | GOOD
```

**Reading this:**
- **AKAM**: Best trade (highest VRP + highest edge)
- **AIG**: Second best (high VRP + high edge)
- **ADBE**: Third (high VRP, good edge)
- **AVGO**: Fourth (moderate VRP, still tradeable)

### Whisper Mode Output:
```
🎯 Most Anticipated + High VRP (Ranked by VRP Ratio):
   1. NVDA  : VRP 8.21x | Implied 7.5% | Edge 5.12 | EXCELLENT | Earnings 2025-11-20
   2. META  : VRP 6.94x | Implied 8.2% | Edge 4.33 | EXCELLENT | Earnings 2025-11-21
   3. TSLA  : VRP 3.15x | Implied 9.8% | Edge 2.01 | GOOD     | Earnings 2025-11-22
```

**Reading this:**
- **NVDA**: Highest edge + market attention = Best opportunity
- **META**: Strong edge + anticipated
- **TSLA**: Lower edge but still tradeable (high volatility stock)

---

## 🎓 Advanced Concepts

### VRP Across Different Stock Types

**Tech Stocks (NVDA, META):**
- Higher baseline volatility
- VRP 3-5x can be excellent
- Historical moves more variable

**Utilities (AEP, D):**
- Lower baseline volatility
- VRP 3-5x is exceptional
- Historical moves very consistent

**Bio/Pharma (BIIB, GILD):**
- Binary events (FDA approvals)
- VRP can be misleading
- Use caution even with high VRP

### Consistency Factor

The edge score considers:

1. **Standard Deviation**
   - Lower std dev = higher consistency = higher edge score
   - Example: Mean 1%, Std Dev 0.2% → Consistent

2. **Sample Size**
   - More historical data = more confidence
   - Minimum 8 quarters required
   - Ideal: 12+ quarters

3. **Recency Weighting**
   - Recent earnings weighted more heavily
   - Company behavior can change over time
   - Adapts to new patterns

---

## 🔗 Next Steps

1. **Try the Scanner:**
   ```bash
   ./trade.sh scan 2025-12-20
   ```

2. **Analyze Specific Ticker:**
   ```bash
   ./trade.sh NVDA 2025-11-20
   ```

3. **Check Most Anticipated:**
   ```bash
   ./trade.sh whisper
   ```

4. **Review Strategy Details:**
   - See LIVE_TRADING_GUIDE.md for execution
   - See README.md for system overview
   - See POSITION_SIZING_DEPLOYMENT.md for risk management

---

## 📚 Quick Reference Card

```
┌─────────────────────────────────────────────────────────────┐
│                     METRIC CHEAT SHEET                       │
├─────────────────────────────────────────────────────────────┤
│ Implied Move     │ Market's expected move (from options)    │
│ Historical Mean  │ Actual average move (past 12 earnings)   │
│ VRP Ratio        │ Implied / Historical (THE EDGE)          │
│ Edge Score       │ Confidence + consistency metric          │
│ Recommendation   │ Trade decision (SKIP/MARGINAL/GOOD/EXC)  │
├─────────────────────────────────────────────────────────────┤
│ VRP > 4.0x       → EXCELLENT (high priority)                │
│ VRP 2.5-4.0x     → GOOD (tradeable)                         │
│ VRP 1.5-2.5x     → MARGINAL (selective)                     │
│ VRP < 1.5x       → SKIP (no edge)                           │
├─────────────────────────────────────────────────────────────┤
│ Edge > 4.0       → High confidence                          │
│ Edge 2.0-4.0     → Moderate confidence                      │
│ Edge < 2.0       → Low confidence                           │
└─────────────────────────────────────────────────────────────┘
```

---

**Remember:** These metrics identify high-probability opportunities, not guarantees. Always use proper position sizing, risk management, and never risk more than you can afford to lose.
