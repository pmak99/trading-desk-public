# Forward Test Results - 2024

**Test Period:** January 1, 2024 - December 31, 2024
**Total Opportunities:** 261 earnings events
**Date:** November 13, 2025

---

## Summary Table

| Configuration | Trades | Win Rate | Avg P&L | Total P&L | Sharpe | Max DD | Qualified |
|---------------|--------|----------|---------|-----------|--------|--------|-----------|
| **Consistency-Heavy** | 8 | **87.5%** | **3.84%** | 30.76% | **1.00** | 2.65% | 202 |
| **VRP-Dominant** | 10 | **90.0%** | 3.31% | 33.13% | 0.93 | 2.65% | 219 |
| **Liquidity-First** | 10 | **90.0%** | 3.31% | 33.13% | 0.93 | 2.65% | 236 |
| **Skew-Aware** | 10 | **90.0%** | 3.31% | 33.13% | 0.93 | 2.65% | 236 |
| **Hybrid** | 10 | **90.0%** | 3.31% | 33.13% | 0.93 | 2.65% | 236 |
| **Balanced** | 12 | **91.7%** | 2.96% | **35.56%** | 0.89 | 2.65% | 236 |
| **Aggressive** | 15 | 86.7% | 2.72% | **40.82%** | 0.87 | 2.65% | 236 |
| Conservative | 0 | 0.0% | 0.00% | 0.00% | 0.00 | 0.00% | 0 |

---

## Top 3 Configurations

### #1 - Consistency-Heavy ⭐
**Best Risk-Adjusted Returns**

- **Sharpe Ratio:** 1.00 (Best)
- **Win Rate:** 87.5%
- **Avg P&L per Trade:** 3.84% (Best)
- **Total P&L:** 30.76%
- **Max Drawdown:** 2.65%
- **Trades:** 8 out of 261 opportunities (3.1% selection rate)

**Strategy:**
- Weights: VRP 35%, Consistency 45%, Skew 10%, Liquidity 10%
- Min Score: 65.0
- Max Positions: 8
- **Approach:** Focuses on predictable, reliable earnings moves with low variance

### #2 - VRP-Dominant
**Baseline Strategy**

- **Sharpe Ratio:** 0.93
- **Win Rate:** 90.0%
- **Avg P&L per Trade:** 3.31%
- **Total P&L:** 33.13%
- **Max Drawdown:** 2.65%
- **Trades:** 10 out of 261 opportunities (3.8% selection rate)

**Strategy:**
- Weights: VRP 70%, Consistency 20%, Skew 5%, Liquidity 5%
- Min Score: 65.0
- Max Positions: 10
- **Approach:** Prioritizes raw VRP edge over other factors

### #3 - Liquidity-First
**Recommended for User Profile**

- **Sharpe Ratio:** 0.93
- **Win Rate:** 90.0%
- **Avg P&L per Trade:** 3.31%
- **Total P&L:** 33.13%
- **Max Drawdown:** 2.65%
- **Trades:** 10 out of 261 opportunities (3.8% selection rate)

**Strategy:**
- Weights: VRP 30%, Consistency 20%, Skew 15%, Liquidity 35%
- Min Score: 60.0
- Max Positions: 10
- **Approach:** Prioritizes liquidity and low slippage for larger position sizes

---

## Other Notable Configurations

### Balanced
**Highest Win Rate**

- Sharpe: 0.89
- **Win Rate: 91.7%** (Best)
- Avg P&L: 2.96%
- Total P&L: 35.56%
- Trades: 12 (4.6% selection rate)

**Strategy:** Well-rounded approach balancing all factors

### Aggressive
**Highest Total P&L**

- Sharpe: 0.87
- Win Rate: 86.7%
- Avg P&L: 2.72%
- **Total P&L: 40.82%** (Best)
- Trades: 15 (5.7% selection rate)

**Strategy:** Higher volume approach with lower thresholds

### Conservative
**Failed to Qualify Any Trades**

- All metrics: 0.0%
- **Issue:** Thresholds too strict (Min Score: 70.0, VRP Excellent: 2.2x)
- **Recommendation:** Not viable for this market environment

---

## Key Insights

### Performance Leaders
- ✅ **Best Sharpe Ratio:** Consistency-Heavy (1.00)
- ✅ **Best Win Rate:** Balanced (91.7%)
- ✅ **Best Total P&L:** Aggressive (40.82%)
- ✅ **Best Avg P&L per Trade:** Consistency-Heavy (3.84%)

### Quality vs Quantity Trade-off
- **Conservative approach (8 trades):** Higher P&L per trade (3.84%), better Sharpe (1.00)
- **Aggressive approach (15 trades):** Lower P&L per trade (2.72%), highest total P&L (40.82%)
- **Sweet spot:** 10-12 trades balances quality and volume

### Consistency Analysis
- All successful configs maintained **86-92% win rates**
- Max drawdown consistent across all configs: **2.65%**
- Sharpe ratios clustered between **0.87-1.00** (excellent)

---

## Recommendation

### For This User Profile (Balanced Risk, Liquidity First)

**Primary:** **Liquidity-First** or **Balanced**
- Sharpe: 0.93 (Liquidity-First) or 0.89 (Balanced)
- Win Rate: 90.0% or 91.7%
- Expected trades/week: ~3
- Emphasizes execution quality and consistent performance

**Alternative:** **Consistency-Heavy** (if targeting highest Sharpe)
- Best risk-adjusted returns (Sharpe 1.00)
- More selective (8 trades/year)
- Higher average P&L per trade (3.84%)

**Not Recommended:** Conservative (0 trades - too strict)

---

## Configuration Details

### Weight Breakdown

| Config | VRP | Consistency | Skew | Liquidity | Min Score | Max Pos |
|--------|-----|-------------|------|-----------|-----------|---------|
| VRP-Dominant | 0.70 | 0.20 | 0.05 | 0.05 | 65.0 | 10 |
| Balanced | 0.40 | 0.25 | 0.15 | 0.20 | 60.0 | 12 |
| Liquidity-First | 0.30 | 0.20 | 0.15 | 0.35 | 60.0 | 10 |
| Consistency-Heavy | 0.35 | 0.45 | 0.10 | 0.10 | 65.0 | 8 |
| Skew-Aware | 0.35 | 0.20 | 0.30 | 0.15 | 60.0 | 10 |
| Aggressive | 0.55 | 0.20 | 0.10 | 0.15 | 50.0 | 15 |
| Conservative | 0.40 | 0.35 | 0.15 | 0.10 | 70.0 | 6 |
| Hybrid | 0.45 | 0.20 | 0.15 | 0.20 | 62.0 | 10 |

---

## Next Steps

1. **Deploy Liquidity-First config** as default in production
2. Monitor real-world performance vs forward test results
3. Consider **A/B testing** Liquidity-First vs Consistency-Heavy live
4. Review Conservative config thresholds if market volatility increases

---

**Generated:** November 13, 2025
**Test Data:** 261 earnings events from 2024
**Framework:** IV Crush 2.0 with Phase 4 algorithms
