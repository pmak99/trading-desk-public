# Optimization & AB Testing Roadmap

Suggested improvements and tests for the IV Crush 2.0 system, ranked by impact.

---

## High Impact (Implement First)

### 1. Parameter Optimization - VRP Thresholds
**Current:** VRP thresholds are fixed (1.5x good, 2.0x excellent)
**Test:** Grid search optimal thresholds

```python
# Test configurations
vrp_thresholds = [
    (1.3, 1.8),  # More permissive
    (1.5, 2.0),  # Current
    (1.7, 2.2),  # More strict
    (1.4, 1.9),  # Sweet spot?
]
```

**Why:** VRP ratio is 40-70% of scoring weight. Small threshold changes = big impact on trade selection.

**Expected Gain:** 5-10% improvement in Sharpe ratio

**Implementation:** 2 hours
- Modify `ScoringThresholds` class
- Run backtests on each configuration
- Compare Sharpe, win rate, trade count

---

### 2. Historical Lookback Window Optimization
**Current:** Fixed 12 quarters (3 years)
**Test:** Dynamic lookback based on consistency

```python
# Adaptive approach
if consistency > 0.8:
    lookback = 8 quarters  # Very predictable - recent data matters more
elif consistency > 0.5:
    lookback = 12 quarters  # Current default
else:
    lookback = 16 quarters  # Erratic - need more data
```

**Why:** Different tickers have different patterns. Some are consistently volatile (TSLA), others are stable (WMT).

**Expected Gain:** 3-5% improvement in edge detection

**Implementation:** 3 hours
- Add dynamic lookback to VRP calculator
- Backtest with fixed windows (8, 12, 16, 20 quarters)
- Test adaptive vs fixed

---

### 3. Position Sizing Optimization
**Current:** Equal weight ($20K / num_positions)
**Test:** Kelly Criterion + VRP-weighted sizing

```python
# Kelly fraction
f = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win

# VRP-weighted
position_size = base_size * (vrp_ratio / 1.5)  # Scale by VRP
```

**Why:** Current approach treats all trades equally. High-confidence trades (VRP 2.5x) should get more capital than marginal trades (VRP 1.5x).

**Expected Gain:** 10-15% improvement in total P&L

**Implementation:** 4 hours
- Add position sizing module
- Backtest Kelly vs equal weight vs VRP-weighted
- Test with different base sizes

---

### 4. Market Regime Analysis
**Current:** No distinction between market conditions
**Test:** Performance by VIX regime

```python
regimes = {
    "low_vol": VIX < 15,
    "normal": 15 <= VIX < 25,
    "high_vol": VIX >= 25
}
```

**Why:** IV Crush works differently in different volatility regimes. High VIX = higher premiums but also higher risk.

**Expected Gain:** 5-8% improvement in trade selection

**Implementation:** 3 hours
- Add VIX data to database
- Segment backtest by regime
- Test regime-specific thresholds

---

### 5. Earnings Timing Analysis
**Current:** Treats BMO and AMC equally
**Test:** Separate performance by timing

```python
# Hypothesis: AMC earnings have better IV crush
# because options decay overnight

amc_performance = backtest(filter="AMC only")
bmo_performance = backtest(filter="BMO only")
```

**Why:** Market has different dynamics for morning vs afternoon earnings. Options behavior differs.

**Expected Gain:** 3-5% improvement in win rate

**Implementation:** 2 hours
- Segment backtest by EarningsTiming
- Compare metrics (win rate, P&L, Sharpe)
- Adjust scoring if significant difference

---

## Medium Impact (Next Priority)

### 6. Ensemble Methods
**Current:** Single configuration per trade
**Test:** Combine multiple configs

```python
# Voting ensemble
configs = [Consistency-Heavy, VRP-Dominant, Liquidity-First]
score = weighted_average([c.score(ticker) for c in configs])

# Only trade if 2/3 configs agree
```

**Why:** Different configs capture different edges. Ensemble could reduce false positives.

**Expected Gain:** 2-4% improvement in win rate

**Implementation:** 4 hours

---

### 7. Decay Factor Optimization
**Current:** Exponential decay = 0.85
**Test:** Optimize decay for consistency

```python
decay_factors = [0.75, 0.80, 0.85, 0.90, 0.95]

# Test hypothesis: Recent quarters should be weighted MORE heavily
# because market behavior changes over time
```

**Why:** 0.85 is arbitrary. Optimal value likely varies by ticker volatility.

**Expected Gain:** 2-3% improvement in consistency scoring

**Implementation:** 3 hours

---

### 8. Skew Polynomial Degree
**Current:** 2nd degree polynomial
**Test:** Compare polynomial degrees

```python
degrees = [1, 2, 3]  # Linear, quadratic, cubic

# Trade-off: Higher degree = better fit but more overfitting
```

**Why:** Some tickers have complex skew curves. Others are simple.

**Expected Gain:** 1-3% improvement in directional accuracy

**Implementation:** 2 hours

---

### 9. Quarterly Seasonality
**Current:** No seasonal adjustment
**Test:** Performance by quarter

```python
# Hypothesis: Q4 earnings are different (holiday season, year-end)
# Q1 might have lower volatility

performance_by_quarter = {
    "Q1": [],
    "Q2": [],
    "Q3": [],
    "Q4": []
}
```

**Why:** Earnings behavior might be seasonal. Tax considerations, guidance patterns.

**Expected Gain:** 2-4% improvement if seasonal patterns exist

**Implementation:** 2 hours

---

### 10. Rolling Window Backtest
**Current:** Static backtest on 2024 data
**Test:** Walk-forward analysis

```python
# Train on 2022-2023, test on Q1 2024
# Train on 2022-Q1 2024, test on Q2 2024
# ...

# Prevents overfitting to 2024 conditions
```

**Why:** Current backtest might overfit to 2024 market conditions.

**Expected Gain:** Better confidence in real-world performance

**Implementation:** 5 hours

---

## Lower Impact (Nice to Have)

### 11. Correlation Analysis
**Test:** Avoid correlated positions

```python
# If holding GOOGL, reduce score for GOOG
# If holding AMD, reduce score for NVDA (same sector)
```

**Expected Gain:** 1-2% improvement in portfolio Sharpe

**Implementation:** 4 hours

---

### 12. Stop Loss Optimization
**Test:** Optimal stop loss levels

```python
stop_loss_levels = [0.5, 1.0, 1.5, 2.0]  # % of credit received

# Test: Does cutting losers early improve Sharpe?
```

**Expected Gain:** 2-3% improvement in max drawdown

**Implementation:** 3 hours

---

### 13. Partial Profit Taking
**Test:** Close 50% at 50% profit target

```python
# Iron Condor collected $2.00 credit
# Close 50% when P&L = $1.00 (50% of max profit)
# Let remaining 50% ride to expiration
```

**Expected Gain:** 1-2% improvement in win rate

**Implementation:** 4 hours

---

### 14. Entry Timing Optimization
**Current:** Assume entry at market close before earnings
**Test:** Entry at different times

```python
entry_times = [
    "3 days before earnings",
    "2 days before earnings",
    "1 day before earnings",
    "Morning of earnings (BMO only)"
]
```

**Expected Gain:** 1-3% improvement in edge

**Implementation:** 3 hours (requires intraday data)

---

### 15. Feature Engineering
**Add:** New scoring factors

```python
new_features = {
    "iv_rank": current_iv / 52week_iv_range,
    "hv_rank": current_hv / 52week_hv_range,
    "earnings_surprise_history": beat_rate,
    "analyst_dispersion": std(analyst_estimates)
}
```

**Expected Gain:** 2-5% improvement if features are predictive

**Implementation:** 8 hours (data collection + testing)

---

## Recommended Test Sequence

### Phase 1 (Week 1): Quick Wins
1. ✅ VRP Threshold Optimization (2 hours)
2. ✅ Earnings Timing Analysis (2 hours)
3. ✅ Market Regime Analysis (3 hours)

**Expected Combined Gain:** 10-15% improvement in Sharpe

---

### Phase 2 (Week 2): Position Sizing
1. ✅ Position Sizing Optimization (4 hours)
2. ✅ Kelly Criterion Implementation (2 hours)

**Expected Gain:** 10-15% improvement in total P&L

---

### Phase 3 (Week 3): Advanced
1. ✅ Historical Lookback Optimization (3 hours)
2. ✅ Decay Factor Optimization (3 hours)
3. ✅ Ensemble Methods (4 hours)

**Expected Gain:** 5-10% improvement in consistency

---

### Phase 4 (Week 4): Validation
1. ✅ Rolling Window Backtest (5 hours)
2. ✅ Out-of-sample testing (2025 data when available)

**Goal:** Prevent overfitting, build confidence

---

## Success Metrics

Track improvements against baseline (Liquidity-First config):
- **Sharpe Ratio:** 0.93 → Target 1.10+
- **Win Rate:** 90.0% → Target 92%+
- **Avg P&L per Trade:** 3.31% → Target 3.8%+
- **Max Drawdown:** 2.65% → Target <3%

---

## Implementation Notes

### Testing Framework
```python
# scripts/optimize_parameters.py

def grid_search(param_grid, backtest_engine):
    """Test all parameter combinations."""
    results = []
    for params in param_grid:
        config = create_config(params)
        result = backtest_engine.run(config)
        results.append(result)
    return best_config(results)
```

### Validation Protocol
1. ✅ Backtest on 2024 data (in-sample)
2. ✅ Validate on 2023 data (out-of-sample)
3. ✅ Forward test on live data (2025)
4. ✅ Compare to baseline

### Risk Management
- Never deploy untested optimizations to live trading
- Always validate on out-of-sample data
- Monitor for overfitting (too many parameters)
- Keep optimizations simple and interpretable

---

**Next Step:** Which optimization would you like to implement first?
