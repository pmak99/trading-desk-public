# Comprehensive System Audit - IV Crush Strategy Alignment

**Date**: 2025-11-10
**Goal**: Verify every component aligns with IV crush strategy
**Method**: Critical analysis of all system layers

---

## Executive Summary

**Core Strategy**: IV Crush - Enter 1-2 days before earnings to capture IV collapse post-announcement

**Overall Assessment**: ✅ System is **well-aligned** with strategy, but found **5 issues** requiring attention

---

## Layer-by-Layer Analysis

### 1. Data Collection Layer ✅ ALIGNED

**Components**:
- Earnings calendar (Nasdaq API, Alpha Vantage)
- Options data (Tradier for real IV, yfinance fallback)
- IV history tracking (SQLite)
- Reddit sentiment scraping

**Findings**:

✅ **GOOD**: Earnings timing logic is correct
```python
# src/data/calendars/base.py:32-71
# Correctly filters out already-reported earnings
# Pre-market: Marked reported after 9:30 AM ✓
# After-hours: Marked reported after 4:00 PM ✓
```

✅ **GOOD**: IV extraction from ATM options
```python
# src/options/tradier_client.py:219-239
# Uses ATM call IV (most liquid)
# Validates 1-300% range
# Falls back to ATM put if needed
```

✅ **GOOD**: IV recorded during data fetch
```python
# src/options/tradier_client.py:245
self.iv_tracker.record_iv(ticker, current_iv)
```

⚠️ **ISSUE #1**: Wasteful 180-day backfill for IV Rank
```python
# src/options/tradier_client.py:256
backfiller.backfill_ticker(ticker, lookback_days=180)
```
- **Problem**: IV Rank no longer used in scoring (replaced by Weekly IV Change)
- **Impact**: Wasting API calls and time
- **Recommendation**: Remove IV Rank backfill, or reduce to 14 days

---

### 2. Filtering Layer ✅ MOSTLY ALIGNED

**Components**:
- Pre-filter (market cap, volume)
- IV thresholds (minimum 60%)
- Liquidity thresholds (volume, OI)
- Timing filter (already reported)

**Findings**:

✅ **GOOD**: Pre-filter reduces API usage by 80%
```python
# src/analysis/ticker_filter.py:86-112
# Filters: Market cap ≥ $500M, Volume ≥ 100K
# Typical: 265 tickers → 50 tickers
```

✅ **GOOD**: Hard filters align with strategy
```python
# config/trading_criteria.yaml
iv_thresholds:
  minimum: 60  # Need high IV to crush ✓
liquidity_thresholds:
  minimum_volume: 100      # Need liquidity ✓
  minimum_open_interest: 500
```

⚠️ **ISSUE #2**: No filter for earnings timing
- **Problem**: System doesn't filter by "how soon" earnings are
- **Current**: Analyzes all tickers with upcoming earnings (could be 30+ days out)
- **Strategy**: Optimized for **1-2 day pre-earnings entries**
- **Recommendation**: Add filter for earnings within 3-5 days

---

### 3. Scoring Layer ⚠️ ISSUES FOUND

**Components**:
- IV Expansion Velocity (35%)
- Options Liquidity (30%)
- IV Crush Edge (25%)
- Current IV Level (25%)
- Fundamentals (5%)

**Findings**:

✅ **GOOD**: Weights prioritize right metrics
```
35% IV Expansion - Is premium building NOW? ✓
30% Liquidity - Can we execute? ✓
25% IV Crush Edge - Historical edge? ✓
25% Current IV - High enough to crush? ✓
5% Fundamentals - Minor factor ✓
```

⚠️ **ISSUE #3**: Weights sum to 120%, not normalized
```python
# src/analysis/scorers.py:471-472
# Comment says "will be normalized by individual weights"
# But code just sums: total = sum(scores)  # line 511
```
- **Problem**: Scores can exceed 100 (theoretical max 120)
- **Impact**: Confusing - reports show "/100" but scores can be >100
- **Evidence**: None seen yet (CRWV scored 96.6, not >100)
- **Recommendation**: Either normalize to 100 or change reports to "/120"

⚠️ **ISSUE #4**: IVScorer still calculates IV Rank (unused)
```python
# src/analysis/scorers.py:52-124
# IVScorer only uses current_iv, not iv_rank
# But tradier_client still calculates iv_rank
```
- **Problem**: Calculating IV Rank but not using it
- **Impact**: Wasted computation, confusing code
- **Recommendation**: Remove IV Rank calculation or add it back to scoring

✅ **FIXED**: IV Expansion scoring (we fixed this today)
- Was returning neutral 50.0, now calculates correctly ✓

---

### 4. AI Analysis Layer ⚠️ NEEDS VALIDATION

**Components**:
- Sentiment analysis (Reddit + web)
- Strategy generation (trade ideas)

**Findings**:

⚠️ **ISSUE #5**: Sentiment value for IV crush unclear
```python
# src/ai/sentiment_analyzer.py
# Analyzes: bullish/bearish, headwinds/tailwinds
# Cost: ~$0.02-0.05 per ticker (expensive)
```

**Questions needing answers**:
1. Does sentiment improve IV crush trade selection?
2. Or is it redundant with IV Crush Edge metric?
3. Is the cost justified?

**For IV Crush Strategy**:
- ✅ **PRO**: Helps choose directional bias (calls vs puts vs iron condor)
- ✅ **PRO**: Identifies extreme sentiment = higher risk of large moves
- ❌ **CON**: Sentiment already priced into IV (captured by IV Crush Edge)
- ❌ **CON**: Expensive ($0.02-0.05 per ticker × 5 tickers = $0.10-0.25 per run)

**Hypothesis to test**:
> Sentiment analysis is valuable for directional bias but may not be worth the cost for pure IV crush plays where you sell both sides (iron condor)

**Recommendation**:
- A/B test: Run analysis with vs without sentiment
- Compare trade outcomes over 20+ setups
- If no improvement, make sentiment optional

---

### 5. Budget Controls ✅ WORKING CORRECTLY

**Components**:
- Usage tracking (SQLite)
- Daily limits (40 calls/day)
- Monthly limits ($5.00 total, $4.98 Perplexity)
- Auto-fallback (Perplexity → Gemini)

**Findings**:

✅ **VERIFIED**: Budget tracking is accurate
- Confirmed during previous audit (no bugs found)
- Perplexity pricing correct: $0.003/1K input, $0.015/1K output, $0.005/request

✅ **GOOD**: Override mode for important analysis
```python
# Can bypass daily limits (but not hard caps)
override_daily_limit=True
```

---

### 6. Report Generation ✅ ALIGNED

**Components**:
- Text reports
- CSV exports
- Options metrics display
- Strategy recommendations

**Findings**:

✅ **GOOD**: Reports show relevant IV crush metrics
```
Current IV: 87.38%
Weekly IV Change: +20.7% (shows expansion/contraction)
Expected Move: 7.54%
IV Crush Ratio: 5.31x (implied vs actual)
```

✅ **GOOD**: Enhanced diagnostics (we added today)
```
Weekly IV Change: N/A ⚠️  (No IV data 5-9 days ago)
→ Using neutral score (50.0) for IV Expansion (35% weight)
→ Score may be inaccurate - run again for backfill
```

---

## Critical Issues Summary

| # | Issue | Severity | Impact | Fix Time |
|---|-------|----------|--------|----------|
| 1 | Wasteful 180-day IV Rank backfill | Low | Performance | 5 min |
| 2 | No filter for earnings proximity (1-2 days) | Medium | Efficiency | 15 min |
| 3 | Scoring weights sum to 120%, not normalized | Low | Confusion | 10 min |
| 4 | IV Rank calculated but unused | Low | Performance | 5 min |
| 5 | Sentiment value unclear for IV crush | High | Budget | A/B test needed |

---

## Strategic Alignment Assessment

### What's Working ✅

1. **Data Collection**: Getting right data (IV, liquidity, timing)
2. **Entry Timing**: Correctly filters already-reported earnings
3. **Scoring Priorities**: Weights emphasize right metrics (expansion, liquidity, edge)
4. **Budget Controls**: Working correctly, prevents overruns
5. **Self-Healing**: Auto-backfill for missing IV data

### What Needs Attention ⚠️

1. **Sentiment ROI**: Need to validate if cost is justified
2. **Earnings Proximity**: Should prioritize tickers with imminent earnings (1-3 days)
3. **Score Normalization**: Clarify if 120% total is intentional
4. **IV Rank Waste**: Calculating metric that's not used

### Philosophical Questions 🤔

**Question 1: Is sentiment analysis worth $0.10-0.25 per analysis run?**

For IV crush strategy:
- If selling BOTH sides (iron condor): Sentiment is less relevant
- If selling ONE side (call or put spread): Sentiment helps choose which side
- If most trades are iron condors: Sentiment might not be worth cost

**Recommendation**: Track strategy types generated
- If >70% iron condors: Consider making sentiment optional
- If <50% iron condors: Sentiment is valuable for directional bias

---

**Question 2: Should we prioritize tickers with earnings in 1-3 days?**

Current behavior:
- Analyzes ALL upcoming earnings (could be 30 days out)
- No urgency filter

Strategy states: "1-2 day pre-earnings entries"

**Recommendation**: Add earnings proximity scoring
```python
# Boost score for imminent earnings
if days_until_earnings <= 2:
    urgency_multiplier = 1.2
elif days_until_earnings <= 5:
    urgency_multiplier = 1.0
else:
    urgency_multiplier = 0.8  # Deprioritize distant earnings
```

---

**Question 3: What is the 120% weight total design?**

Possible explanations:
1. **Intentional headroom**: Allows exceptional candidates to score >100
2. **Bug**: Should normalize to 100
3. **Legacy**: Weights changed but normalization code removed

**Evidence**: No scores >100 seen in reports yet (max was 96.6)

**Recommendation**: Clarify intent in code comments or normalize

---

## Recommended Action Plan

### Immediate (< 30 min)
1. ✅ **DONE**: Fix IV expansion scoring bug
2. Add earnings proximity filter/boost
3. Remove or reduce IV Rank backfill
4. Clarify 120% weight design

### Short-term (< 1 week)
5. A/B test sentiment analysis value
6. Track strategy type distribution (iron condor vs directional)
7. Add metrics to measure trade outcome correlation with:
   - Sentiment score
   - IV expansion score
   - IV crush edge score

### Long-term (< 1 month)
8. Build backtesting framework to validate scoring weights
9. Optimize budget allocation (which AI calls provide most value?)
10. Add real-time performance tracking (actual P&L vs predicted)

---

## Conclusion

**Overall Assessment**: System is **well-designed and mostly aligned** with IV crush strategy.

**Key Strengths**:
- Correct entry timing logic
- Prioritizes right metrics (IV expansion, liquidity, crush edge)
- Self-healing with on-demand backfill
- Robust budget controls

**Key Weaknesses**:
- Sentiment analysis ROI unclear (expensive, may be redundant)
- No urgency filter for imminent earnings
- Minor inefficiencies (unused IV Rank calculation)

**Most Important Next Step**:
Validate whether sentiment analysis ($0.10-0.25 per run) provides enough value for IV crush strategy, or if we should make it optional.
