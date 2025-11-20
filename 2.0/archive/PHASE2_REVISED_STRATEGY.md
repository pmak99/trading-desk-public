# Phase 2: Revised Strategy - Dual Configuration Testing

**Date:** November 20, 2025
**Decision:** Option C - Hybrid Approach
**Timeline:** 4-8 weeks (Nov 20 - Jan 15, 2026)

---

## 🎯 Revised Approach: Test Both Configurations

### Strategy A: Consistency-Heavy (Original Winner)
**Target:** 2-5 trades on major names with historical data

| Week | Dates | Opportunities | Tickers |
|------|-------|---------------|---------|
| 1 | Nov 20-26 | ❌ Missed | NVDA, TGT, WMT (already reported) |
| 2-3 | Nov 27-Dec 10 | None | No major earnings with data |
| 4 | Dec 11-17 | ✅ 2 trades | ADBE (Dec 10), AVGO (Dec 11) |
| 5-6 | Dec 18-31 | Holiday break | - |
| 7-8 | Jan 1-15 | ✅ 3+ trades | AAPL (Jan 29*), AMZN (Jan 29*), AMD (Feb 3*) |

*Note: These dates fall outside 4-week window but within 8-week extended timeline

**Expected Results:**
- Trades: 2-5 (ADBE, AVGO confirmed + optional Jan extension)
- Win Rate Target: 90-100% (matching historical 100%)
- Sharpe Target: >1.5 (historical 1.71)
- Quality over quantity approach

### Strategy B: VRP-Dominant (Runner-up)
**Target:** 6-10 trades on broader candidate pool

**Configuration:**
- VRP Weight: 50% (vs 35% for Consistency-Heavy)
- Consistency Weight: 20% (vs 45%)
- Skew Weight: 15%
- Liquidity Weight: 15%
- Max Positions: 10 (vs 8 for Consistency-Heavy)
- Min Score: 60.0 (vs 65.0)

**Historical Performance:**
- Sharpe: 1.41 (excellent, just below 1.71)
- Win Rate: 100% (same as Consistency-Heavy)
- Trades: 10 (vs 8)
- Avg P&L: 3.90% per trade (vs 4.58%)

**Advantages:**
- Less dependent on historical consistency data
- More candidates eligible (broader pool)
- Still had 100% win rate in backtest
- Can trade throughout Nov-Dec window

**Timeline:**
- Week 1-2: 2-3 trades
- Week 3-4: 2-3 trades
- Week 5-6: 2-3 trades (optional)
- **Total: 6-10 trades**

---

## 📊 Dual-Configuration Tracking

### Tracking Spreadsheet Format:

| Date | Ticker | Config | Score | VRP | Consistency | Strategy | Entry | Exit | P&L | Win? | Notes |
|------|--------|--------|-------|-----|-------------|----------|-------|------|-----|------|-------|
| 12/10 | ADBE | CH | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | Consistency-Heavy |
| 12/11 | AVGO | CH | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | Consistency-Heavy |
| 11/25 | TICKER | VRP | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | VRP-Dominant |
| ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... | ... |

**Configs:**
- CH = Consistency-Heavy
- VRP = VRP-Dominant

### Performance Comparison Table:

| Metric | Consistency-Heavy | VRP-Dominant | Historical CH | Historical VRP |
|--------|-------------------|--------------|---------------|----------------|
| **Trades** | 2-5 | 6-10 | 8 | 10 |
| **Win Rate** | TBD | TBD | 100% | 100% |
| **Avg P&L** | TBD | TBD | 4.58% | 3.90% |
| **Sharpe** | TBD | TBD | 1.71 | 1.41 |
| **Max DD** | TBD | TBD | 0% | 0% |

---

## 🔄 Week-by-Week Execution Plan

### Week 1 (Nov 20-26, 2025) ✅ CURRENT

**Consistency-Heavy:**
- ❌ No opportunities (NVDA/TGT/WMT already reported)
- Status: Waiting for Week 4 opportunities

**VRP-Dominant:**
- 🔍 Scan upcoming earnings (Nov 21-26)
- 🎯 Select 2-3 candidates with VRP >1.2x
- 📊 Place paper trades via Alpaca
- Target: 2-3 trades

**Action Items:**
- [ ] Run VRP-Dominant scan on Nov 21-26 earnings
- [ ] Analyze top candidates (score ≥60, VRP ≥1.2x)
- [ ] Place 2-3 paper trades for Week 1
- [ ] Begin daily monitoring

### Week 2 (Nov 27 - Dec 3, 2025)

**Consistency-Heavy:**
- Status: Continue waiting for Week 4

**VRP-Dominant:**
- Scan weekly earnings calendar
- Select 2-3 new opportunities
- Monitor Week 1 positions
- Calculate P&L for closed trades

**Target:** 2-3 VRP trades (cumulative: 4-6)

### Week 3 (Dec 4-10, 2025)

**Consistency-Heavy:**
- 🎯 ADBE analysis (Dec 10 earnings)
- Prepare for Week 4 trades

**VRP-Dominant:**
- Continue weekly trading
- Monitor cumulative performance
- Compare to historical expectations

**Target:** 2-3 VRP trades (cumulative: 6-9)

### Week 4 (Dec 11-17, 2025)

**Consistency-Heavy:**
- ✅ **Place ADBE trade** (Dec 10 earnings)
- ✅ **Place AVGO trade** (Dec 11 earnings)
- Monitor both positions
- Calculate P&L

**VRP-Dominant:**
- Final week of 4-week window
- Close remaining positions
- Calculate cumulative metrics

**Target:** 2 CH trades + 2-3 VRP trades (cumulative: 8-12 total)

### Week 5-8 (Dec 18 - Jan 15, 2026) - OPTIONAL EXTENSION

**Decision Point:** Evaluate at end of Week 4
- If VRP-Dominant performing well: Continue
- If Consistency-Heavy needs more data: Extend to capture AAPL/AMZN/AMD

**Potential Additional Trades:**
- AAPL (Jan 29)
- AMZN (Jan 29)
- AMD (Feb 3)

---

## 📈 Success Criteria (Revised)

### Minimum Viable Sample (4 weeks):
- **Combined:** 8-12 trades total
  - Consistency-Heavy: 2 trades (ADBE, AVGO)
  - VRP-Dominant: 6-10 trades
- **Win Rate:** 80%+ combined
- **Sharpe:** >1.0 combined
- **Execution Quality:** Slippage <5%, fills >90%

### Extended Sample (8 weeks):
- **Combined:** 12-15 trades total
  - Consistency-Heavy: 5 trades (ADBE, AVGO, AAPL, AMZN, AMD)
  - VRP-Dominant: 7-10 trades
- **Win Rate:** 90%+ combined
- **Sharpe:** >1.2 combined

### Comparison Criteria:

**Consistency-Heavy validates if:**
- Win rate within ±10% of historical 100%
- Sharpe >1.5
- Quality of 2-5 trades matches historical selectivity

**VRP-Dominant validates if:**
- Win rate within ±10% of historical 100%
- Sharpe >1.0
- Broader candidate pool generates more opportunities
- Execution quality acceptable

**Final Decision (End of Phase 2):**
- **Use Consistency-Heavy** if both validate equally (prefer Phase 1 winner)
- **Use VRP-Dominant** if it significantly outperforms in opportunity count
- **Use Hybrid** if both show different strengths (Consistency for major names, VRP for others)

---

## 🔧 Technical Implementation

### Run VRP-Dominant Analysis:

```bash
# Scan next week's earnings with VRP-Dominant config
cd 2.0
./trade.sh scan 2025-11-25 --config vrp_dominant

# Analyze specific ticker
./trade.sh TICKER EARNINGS_DATE --config vrp_dominant
```

### Compare Configurations Side-by-Side:

```bash
# Run both configs on same ticker
./trade.sh ADBE 2025-12-10 --config consistency_heavy
./trade.sh ADBE 2025-12-10 --config vrp_dominant

# Compare scores and recommendations
```

### Track Performance:

```bash
# Monitor Alpaca positions
./trade.sh positions

# Calculate cumulative metrics (manual tracking in spreadsheet)
```

---

## 🚨 Key Learnings from Week 1

### Finding 1: Timing is Critical
- Major earnings with historical data clustered in mid-November
- Starting Phase 2 on Nov 20 missed the prime window
- **Lesson:** Phase 2 should start 1-2 weeks before major earnings season

### Finding 2: Data Coverage is Limited
- Only 92 tickers have sufficient historical data
- Backfill system failing for many mid-cap names
- **Impact:** Consistency-Heavy strategy limited to ~15-20 major names per year

### Finding 3: Need for Flexibility
- 4-week window too rigid for sparse data
- Multiple configurations provide hedge against data gaps
- **Solution:** Dual-config testing validates both approaches

### Finding 4: VRP Environment May Have Shifted
- BIDU VRP 0.58x (rejected) vs historical assumption of higher VRP
- Need to analyze if Q4 2025 has compressed IV vs Q2-Q4 2024
- **Action:** Monitor VRP distribution across upcoming earnings

---

## 📝 Next Steps (Immediate)

### Tomorrow (Nov 21):

1. **Run VRP-Dominant Scan:**
   ```bash
   ./trade.sh scan 2025-11-21 --config vrp_dominant
   ./trade.sh scan 2025-11-22 --config vrp_dominant
   ./trade.sh scan 2025-11-25 --config vrp_dominant
   ./trade.sh scan 2025-11-26 --config vrp_dominant
   ```

2. **Analyze Top Candidates:**
   - Filter: Score ≥60, VRP ≥1.2x, Market cap >$1B, Liquidity >1000 contracts
   - Select top 2-3 opportunities
   - Review strategy recommendations

3. **Place First VRP-Dominant Trades:**
   - Use Alpaca paper account
   - Follow strategy recommendations (Iron Condor / Credit Spreads)
   - Log trade details in tracking spreadsheet

4. **Set Up Monitoring:**
   - Daily P&L checks
   - Execution quality assessment
   - VRP environment tracking

### This Week (Nov 20-26):

- [ ] Complete 2-3 VRP-Dominant paper trades
- [ ] Create tracking spreadsheet (Google Sheets or Excel)
- [ ] Document execution quality (slippage, fills)
- [ ] Prepare for Week 2 scanning

### Week 4 (Dec 11-17):

- [ ] Analyze ADBE (Dec 10) with Consistency-Heavy
- [ ] Analyze AVGO (Dec 11) with Consistency-Heavy
- [ ] Place 2 Consistency-Heavy paper trades
- [ ] Compare both configs' performance
- [ ] Make GO/NO-GO decision for Week 5-8 extension

---

## ✅ Decision Points

### After Week 2 (Dec 3):
- **Evaluate:** VRP-Dominant trade count (4-6 target)
- **Decide:** Continue or adjust strategy
- **Action:** If <4 trades, investigate why (market conditions? scoring?)

### After Week 4 (Dec 17):
- **Evaluate:** Combined 8-12 trades achieved?
- **Decide:** Extend to Week 5-8 or stop?
- **Action:** If extending, wait for AAPL/AMZN/AMD (Jan)

### Final (End of Phase 2):
- **Evaluate:** Which config performed better?
- **Decide:** Which to use for live trading?
- **Action:** Document learnings, generate final report

---

## 🎯 Summary: Why Dual-Config Testing?

### Pros:
1. **Maximizes Learning:** Tests both top-performing configs from Phase 1
2. **Hedge Against Data Gaps:** VRP trades while CH waits for major names
3. **Real-World Validation:** Both configs tested in current market conditions
4. **Flexibility:** Can choose best performer for live trading
5. **More Trades:** 8-12 combined vs 2-5 for CH alone

### Cons:
1. **More Complex:** Tracking two strategies simultaneously
2. **Diluted Focus:** Not purely validating Phase 1 winner
3. **Different Risk Profiles:** CH = 8 max positions, VRP = 10 max

### Net Assessment:
**Benefit > Cost** - The additional complexity is worth the increased learning and trade opportunities. We validate both strategies and have flexibility to choose the best performer.

---

**Status:** ✅ Strategy Approved - Ready to Execute
**Next Action:** Run VRP-Dominant scan for Nov 21-26 earnings
**Decision Timeline:** Weekly checkpoints with final decision Jan 15, 2026
