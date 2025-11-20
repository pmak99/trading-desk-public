# Phase 2: Paper Trading Validation - Execution Plan

**Start Date:** November 20, 2025
**Configuration:** Consistency-Heavy (Sharpe 1.71, 100% Win Rate)
**Timeline:** 4-8 weeks
**Target:** 8-10 paper trades
**Account:** Alpaca Paper Trading ✅

---

## 🎯 Success Criteria

✅ **Win Rate:** 90-100% (within ±10% of historical 100%)
✅ **Sharpe Ratio:** > 1.5 (maintain historical 1.71)
✅ **Avg P&L:** 4-5% per trade (historical: 4.58%)
✅ **Max Drawdown:** < 5% (historical: 0%)
✅ **Execution Quality:** Slippage < 5%, Fill rate > 90%

---

## 📅 Week-by-Week Plan

### Week 1 (Nov 20-26, 2025) ✅ READY TO START

**Today's Earnings (Nov 20):**
- BIDU (Baidu) - Estimate: $9.95
- BBWI (Bath & Body Works) - Estimate: $0.40, pre-market
- CPRT (Copart) - Estimate: $0.40, pre-market
- BN (Brookfield Corp) - Estimate: $0.39
- ATKR (Atkore) - Estimate: $1.16, pre-market

**Action Items:**
1. ✅ Fetch full earnings calendar (5,111 events available)
2. 🔄 Score candidates using Consistency-Heavy weights
3. 🔄 Select top 2-3 opportunities for this week
4. 🔄 Place paper trades via Alpaca
5. 🔄 Monitor positions daily

**Target:** 2-3 paper trades

### Week 2 (Nov 27 - Dec 3, 2025)

**Action Items:**
1. Calculate P&L for Week 1 closed positions
2. Fetch new earnings for upcoming week
3. Score and select 2-3 new opportunities
4. Place additional paper trades
5. Compare performance to historical expectations

**Target:** 2-3 paper trades
**Cumulative:** 4-6 trades

### Week 3 (Dec 4-10, 2025)

**Action Items:**
1. Review cumulative performance (6 trades minimum)
2. Calculate interim metrics (win rate, Sharpe, avg P&L)
3. Identify execution quality issues (slippage, fills)
4. Continue with 2-3 new paper trades
5. Document any deviations from historical

**Target:** 2-3 paper trades
**Cumulative:** 6-9 trades

### Week 4 (Dec 11-17, 2025)

**Action Items:**
1. Close all remaining positions
2. Calculate final paper trading metrics
3. Generate comprehensive comparison report
4. Validate against success criteria
5. Make GO/NO-GO decision for live trading

**Target:** 2-3 paper trades
**Cumulative:** 8-12 trades (target achieved)

---

## 🔧 Technical Implementation

### Step 1: Score Upcoming Earnings

```bash
# Fetch and score next week's earnings
cd 2.0
./trade.sh scan 2025-11-20

# Or use the scan script directly
python scripts/scan.py --scan-date 2025-11-20
```

### Step 2: Place Paper Trades

**Manual Method (Current):**
1. Run analysis: `./trade.sh TICKER EARNINGS_DATE`
2. Review strategy recommendations
3. Place paper trade via Alpaca web interface
4. Log trade details in spreadsheet

**Automated Method (Future - requires integration):**
```bash
# Once paper_trading_backtest.py is fully integrated
python scripts/paper_trading_backtest.py \
    --config consistency_heavy \
    --weeks 4
```

### Step 3: Monitor Positions

```bash
# Check Alpaca positions
./trade.sh positions

# Or use Alpaca MCP directly
# (requires MCP integration in your scripts)
```

### Step 4: Calculate Performance

```bash
# After 4 weeks, analyze results
python scripts/performance.py
```

---

## 📊 Tracking Template

Create a spreadsheet to track paper trades:

| Date | Ticker | Earnings Date | Score | Strategy | Entry | Exit | P&L | Win? | Notes |
|------|--------|---------------|-------|----------|-------|------|-----|------|-------|
| 11/20 | BIDU | 11/20 | TBD | Iron Condor | TBD | TBD | TBD | TBD | |
| 11/20 | BBWI | 11/20 | TBD | Credit Spread | TBD | TBD | TBD | TBD | |
| ... | ... | ... | ... | ... | ... | ... | ... | ... | |

**Metrics to Track:**
- Composite Score (from Consistency-Heavy)
- VRP Ratio
- Consistency Score
- Strategy Type (Iron Condor, Bull Put, Bear Call)
- Entry Premium Collected
- Exit Cost (buy back)
- Net P&L
- Slippage (expected vs actual)
- Fill Quality

---

## 🎨 Consistency-Heavy Configuration

**Weights:**
- VRP: 35%
- Consistency: 45% ⭐ (highest weight)
- Skew: 10%
- Liquidity: 10%

**Thresholds:**
- Min Composite Score: 65.0
- Max Positions: 8
- VRP Excellent: ≥2.0x
- Consistency Excellent: ≥0.8

**Expected Trade Characteristics:**
- High consistency stocks (predictable moves)
- Moderate VRP (quality over extreme edge)
- Conservative position count (8 vs 15 aggressive)
- Lower variance than other configs

---

## 🚨 Decision Points

### After Week 2 (Checkpoint 1)

**If Performance is Good:**
- ✅ Win rate ≥90%, Sharpe >1.0
- **Action:** Continue to Week 3-4

**If Performance is Poor:**
- ❌ Win rate <80%, Sharpe <0.5
- **Action:** Investigate issues, consider alternative config

### After Week 4 (Final Decision)

**GO for Live Trading if:**
- ✅ Win rate: 90-100%
- ✅ Sharpe: >1.5
- ✅ Avg P&L: 4-5% per trade
- ✅ Execution quality: Acceptable
- ✅ 8+ completed trades

**NO-GO if:**
- ❌ Win rate <80%
- ❌ Sharpe <1.0
- ❌ Significant execution issues
- ❌ P&L far below expectations

---

## 📈 Today's Action (Nov 20, 2025)

### Immediate Next Steps:

1. **Analyze Today's High-Profile Earnings:**
   ```bash
   cd 2.0
   ./trade.sh BIDU 2025-11-20
   ./trade.sh BBWI 2025-11-20
   ./trade.sh CPRT 2025-11-20
   ```

2. **Score Each Ticker:**
   - Run full analysis with consistency scoring
   - Calculate VRP ratio
   - Get strategy recommendations

3. **Select Top 2-3 for Paper Trading:**
   - Filter by Consistency-Heavy criteria
   - Composite score ≥65
   - Focus on highest scores

4. **Place Paper Trades:**
   - Use Alpaca web interface or API
   - Follow strategy recommendations
   - Log all trade details

5. **Set Up Monitoring:**
   - Check positions before/after market close
   - Monitor P&L daily
   - Note any execution issues

---

## 🔄 Weekly Workflow

**Monday Morning:**
- Fetch earnings calendar for the week
- Run batch analysis on all candidates
- Score using Consistency-Heavy
- Select top 2-3 opportunities

**Daily:**
- Monitor open positions
- Check for assignment risk
- Calculate unrealized P&L
- Note any market conditions

**Friday Evening:**
- Close positions expiring this week
- Calculate realized P&L for the week
- Update tracking spreadsheet
- Review win rate and Sharpe

**Weekly Summary:**
- Compare to historical expectations
- Document lessons learned
- Adjust process if needed

---

## 🎯 Key Metrics Dashboard

Track these weekly:

| Week | Trades | Wins | Win% | Avg P&L | Total P&L | Sharpe | Max DD |
|------|--------|------|------|---------|-----------|--------|--------|
| 1 | 2-3 | TBD | TBD | TBD | TBD | TBD | TBD |
| 2 | 2-3 | TBD | TBD | TBD | TBD | TBD | TBD |
| 3 | 2-3 | TBD | TBD | TBD | TBD | TBD | TBD |
| 4 | 2-3 | TBD | TBD | TBD | TBD | TBD | TBD |
| **Total** | **8-12** | **TBD** | **90%+** | **4.5%** | **36%+** | **>1.5** | **<5%** |

**Comparison to Historical:**
- Historical: 8 trades, 100% win rate, 4.58% avg P&L, Sharpe 1.71
- Target Paper: 8-12 trades, 90%+ win rate, 4.5% avg P&L, Sharpe >1.5

---

## 📝 Notes & Observations

**Things to Watch For:**

1. **Execution Quality:**
   - Are fills at bid/ask or better?
   - Is slippage within acceptable range (<5%)?
   - Can we get filled on complex strategies?

2. **Market Conditions:**
   - How does current VIX compare to backtest period?
   - Are earnings moves compressed or expanded?
   - Any regime changes vs Q2-Q4 2024?

3. **Strategy Performance:**
   - Which strategies work best (Iron Condor vs Credit Spreads)?
   - Are any adjustments needed?
   - How does position management work in practice?

4. **Scoring Validation:**
   - Do high scores (>75) still predict winners?
   - Is Consistency-Heavy selecting the right trades?
   - Any false positives/negatives?

---

## ✅ Checklist for Today (Nov 20)

- [ ] Analyze BIDU earnings (9.95 estimate)
- [ ] Analyze BBWI earnings (0.40 estimate, pre-market)
- [ ] Analyze CPRT earnings (0.40 estimate, pre-market)
- [ ] Score using Consistency-Heavy weights
- [ ] Select top 2 candidates (score ≥65)
- [ ] Place paper trades via Alpaca
- [ ] Document trade details in tracking sheet
- [ ] Set calendar reminders for daily monitoring

---

**Status:** ✅ Ready to begin Phase 2 paper trading validation
**Next Action:** Run analysis on today's earnings candidates
**Timeline:** Complete by December 17, 2025 (4 weeks)
