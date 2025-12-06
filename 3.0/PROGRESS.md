# 3.0 ML System Development Progress

**Started:** December 2025
**Current Phase:** Phase 2 - Model Development

---

## Phase 0: Setup & Baseline ✅

- [x] **Task 0.1:** Evaluate 2.0 baseline on 2025 trades
  - Parsed 289 trades from PDF statements
  - Calculated baseline: 57.4% win rate, $261k YTD
  - Strategy breakdown: naked (81.8% WR), spreads (54.1% WR), iron condors (47.4% WR)

- [x] **Task 0.2:** Setup 3.0 directory structure
  - Created organized project structure
  - Setup config, data, models, src directories
  - Initial documentation and requirements

---

## Phase 1: Data Collection & Feature Engineering ✅

### Task 1.1: Historical Move Features ✅
- [x] Extract historical moves from ivcrush.db
- [x] Calculate rolling statistics (mean, std, percentiles)
- [x] Quarter-over-quarter trends
- [x] **Deliverable:** `data/features/historical_features.parquet`
  - Generated 4,926 feature rows with 35 columns
  - 92.2% coverage for 1Q+ data, 37.8% for 8Q+ data
  - Average data quality score: 0.618

### Task 1.2: Volatility Features ✅
- [x] ATR (Average True Range) calculations
- [x] Bollinger Band width
- [x] Historical Volatility (HV) calculations
- [x] Volatility percentile ranks and regime classification
- [x] **Deliverable:** `data/features/volatility_features.parquet`
  - Generated 4,910 feature rows with 17 columns
  - 99.9%+ data coverage across all features
  - Windows: 10d, 20d, 50d for ATR, BB, HV indicators

### Task 1.3: Market Context Features ✅
- [x] Market regime indicators (VIX, market direction)
- [x] Stock correlations with SPY and QQQ
- [x] Market trend and momentum (RSI, MA analysis)
- [x] **Deliverable:** `data/features/market_features.parquet`
  - Generated 4,910 feature rows with 15 columns
  - 42% data coverage (VIX limited for older periods)
  - Regime distribution: 64% bull, 23% bear, 5% strong_bull, 5% strong_bear

### Task 1.4: Time-Based Features ✅
- [x] Days since last earnings and until next
- [x] Seasonality patterns (month, quarter, day of week)
- [x] Earnings frequency and regularity
- [x] Earnings timing (BMO/AMC)
- [x] **Deliverable:** `data/features/time_features.parquet`
  - Generated 4,926 feature rows with 22 columns
  - 92.2% coverage for earnings gap features
  - 99.4% quarterly earnings, 51.2% BMO vs 48.4% AMC

### Task 1.5: Company Fundamentals (Optional)
- [ ] Market cap, revenue growth
- [ ] Sector classification
- [ ] Analyst estimates vs actuals
- [ ] **Deliverable:** `data/features/fundamental_features.parquet`

### Task 1.6: Feature Validation ✅
- [x] Check for data leakage
- [x] Correlation analysis
- [x] Feature coverage analysis
- [x] Feature distribution analysis
- [x] **Deliverable:** `notebooks/02_feature_engineering.ipynb`
  - Merged all features: 4,926 rows × 83 columns (81 features)
  - Date range: 2007-07-18 to 2025-11-19
  - 385 unique tickers
  - Average coverage: 86.6%
  - 40 highly correlated pairs (>0.9) - mostly 1Q features
  - Output: `data/features/all_features.parquet` (1.6 MB)

---

## Phase 2: Model Development (In Progress)

### Task 2.1: Baseline Models ✅
- [x] Linear regression for magnitude
- [x] Logistic regression for direction
- [x] Walk-forward cross-validation (5 folds)
- [x] Feature importance analysis
- [x] **CRITICAL BUG FIX:** Corrected direction prediction data issue
  - **Issue:** Database stored absolute values instead of signed moves (99.8% up, 0.2% down)
  - **Fix:** Recalculated signed close_move_pct from raw prices
  - **Result:** Balanced 51.7% up / 48.3% down distribution
- [x] **Deliverable:** `notebooks/03_baseline_models.ipynb` + `models/baseline/`
  - **Magnitude Prediction (Linear Regression):**
    - MAE: 2.25% ± 0.38%
    - RMSE: 3.44% ± 0.57%
    - R²: 0.226 ± 0.074
  - **Direction Prediction (Logistic Regression):**
    - Accuracy: 52.8% ± 3.6% (vs 57.4% 2.0 baseline)
    - Precision: 54.7%
    - Recall: 60.1%
    - F1: 57.2%
    - ⚠️ **Underperforms 2.0 by ~4.6%** - need advanced models
  - **Features Used:** 57 (after filtering >50% missing + non-numeric)
  - **Models Saved:** linear_regression_magnitude.pkl, logistic_regression_direction.pkl, scaler.pkl, imputer.pkl

### Task 2.2: Advanced Models ✅
- [x] Random Forest (Regressor & Classifier)
- [x] XGBoost (Regressor & Classifier)
- [x] Feature importance analysis
- [x] 80/20 time-series train/test split
- [x] **Deliverable:** `notebooks/04_advanced_models.ipynb` + `models/advanced/`
  - **Direction Prediction (Test Set):**
    - **Random Forest: 54.0% accuracy** (BEST ML model)
      - Precision: 56.6%, Recall: 57.3%, F1: 56.9%
    - XGBoost: 53.1% accuracy
      - Precision: 55.7%, Recall: 56.8%, F1: 56.2%
    - Logistic Baseline: 52.8%
    - **2.0 System: 57.4%**
    - ⚠️ **Random Forest still 3.4 pp below 2.0 baseline**
  - **Magnitude Prediction (Test Set):**
    - **Random Forest: MAE 2.14%, R² 0.242** (BEST)
    - XGBoost: MAE 2.24%, R² 0.193
    - Linear Baseline: MAE 2.25%, R² 0.226
    - ✅ RF improved MAE by 5% and R² by 7% vs baseline
  - **Top Features (Random Forest - Direction):**
    1. bb_width_20d (Bollinger Band width)
    2. bb_width_10d
    3. bb_width_50d
    4. hv_20d (Historical Volatility)
    5. hv_50d
    6. atr_50d (Average True Range)
    7. earnings_regularity
  - **Top Features (XGBoost - Direction):**
    1. is_q1 (Q1 earnings timing)
    2. is_bmo (before market open)
    3. hist_2q_median (historical stats)
    4. month_sin (seasonal pattern)
    5. hv_20d (volatility)
  - **Models Saved:** rf_magnitude.pkl, rf_direction.pkl, xgb_magnitude.pkl, xgb_direction.pkl, imputer.pkl, feature importance CSVs

### Task 2.3: Model Validation
- [ ] Walk-forward cross-validation
- [ ] Out-of-sample testing
- [ ] Production readiness checks

---

## Phase 3: Integration & Deployment (Pending)

### Task 3.1: 2.0 Integration
- [ ] Create prediction API
- [ ] Integrate with existing trade.sh workflow
- [ ] Fallback to 2.0 logic if ML fails

### Task 3.2: Monitoring & Alerts
- [ ] Model performance tracking
- [ ] Drift detection
- [ ] Alert system

### Task 3.3: Production Deployment
- [ ] Deploy to production environment
- [ ] A/B testing framework
- [ ] Performance monitoring

---

## Success Metrics

### Baseline (2.0 System)
- Win Rate: 57.4%
- Sharpe Ratio: 6.60
- Profit Factor: 1.19
- YTD Gain: $261,102

### Target (3.0 System)
- **Minimum:** Win Rate > 60.3%, Sharpe > 7.26
- **Goal:** Win Rate > 63.2%, Sharpe > 8.25

---

## Notes

- Using existing `../data/ivcrush.db` for historical data
- Storing 2.0 baseline report in `../2.0/reports/2025_baseline_performance.md`
- All ML features stored in parquet format for efficiency
- Models versioned and tracked in `models/` directory

### Key Insights from Phase 2

**What Works:**
- ML models successfully learn patterns from features (>50% accuracy = better than random)
- Random Forest performs best among ML models (54.0% direction, R² 0.242 magnitude)
- Volatility features most predictive: Bollinger Band width, Historical Volatility, ATR
- Time-based features important: Q1 earnings, BMO/AMC timing, seasonality
- Feature engineering pipeline working well (86.6% average coverage)

**What Doesn't Work Yet:**
- ML models underperform 2.0 rule-based system by 3.4 percentage points (54.0% vs 57.4%)
- Gap to minimum target: 6.3 percentage points (need 60.3%)
- Gap to goal target: 9.2 percentage points (need 63.2%)

**Potential Next Steps:**
1. **Hybrid Approach:** Combine ML predictions with 2.0 rule-based logic
   - Use ML as confidence score to filter/rank 2.0 predictions
   - Ensemble: weighted average of ML and rule-based signals
2. **Better Features:** Add option-specific data
   - Implied volatility (IV) levels and IV rank
   - Option Greeks (delta, gamma, theta, vega)
   - Put/Call ratio, Open Interest
   - IV crush magnitude (actual IV before vs after earnings)
3. **Different ML Approach:**
   - Model the 2.0 rules directly (decision tree that replicates logic)
   - Multi-output model (predict move + strategy selection together)
   - Calibrated probabilities for position sizing
4. **Hyperparameter Tuning:**
   - RandomizedSearchCV for RF/XGBoost parameters
   - Try deeper trees, more estimators
   - Feature selection (drop low-importance features)

**Recommendation:** Consider option (1) - Hybrid approach for Phase 3, as 2.0 logic is proven to work and ML can enhance it rather than replace it.

**Last Updated:** 2025-12-04
